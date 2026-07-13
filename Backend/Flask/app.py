#!/usr/bin/env python3
"""
按照技術文件建議的雲端Flask架構
整合ImageJ無頭模式處理、TensorFlow模型推論、深度分析
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt
)
import numpy as np
import cv2
import base64
import io
import time
import logging
from datetime import datetime, timedelta
import os
import json
import threading
from queue import Queue
import sqlite3
import hashlib

# ImageJ和深度學習相關
try:
    import imagej
    import scyjava
    IMAGEJ_AVAILABLE = True
except ImportError:
    IMAGEJ_AVAILABLE = False
    print("警告: ImageJ Python包未安裝，將使用替代方案")

# ONNX Runtime - 主要推論引擎
try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
    print(f"ONNX Runtime {ort.__version__} 載入成功")
except ImportError:
    ONNX_AVAILABLE = False
    print("警告: ONNX Runtime未安裝，將嘗試TensorFlow或傳統方法")

# TensorFlow - 次要推論引擎
try:
    import tensorflow as tf
    import tensorflow_hub as hub
    TENSORFLOW_AVAILABLE = True
except Exception as _tf_err:  # ImportError 或 numpy ABI 衝突(ValueError)等皆視為不可用
    TENSORFLOW_AVAILABLE = False
    tf = None; hub = None
    print(f"警告: TensorFlow不可用({type(_tf_err).__name__}),改用ONNX/替代方案")

from werkzeug.utils import secure_filename
from PIL import Image, ImageEnhance
import requests

# 初始化Flask應用
app = Flask(__name__)
CORS(app)

# 配置
app.config.update(
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16MB最大上傳
    UPLOAD_FOLDER='uploads',
    PROCESSED_FOLDER='processed',
    MODEL_FOLDER='models',
    SECRET_KEY=os.environ.get('FLASK_SECRET_KEY', 'REPLACE_ME_SET_FLASK_SECRET_KEY_VIA_ENV'),
    JWT_SECRET_KEY=os.environ.get('JWT_SECRET_KEY', 'REPLACE_ME_SET_JWT_SECRET_VIA_ENV'),
    JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=24),
    DATABASE='wound_analysis.db'
)

jwt = JWTManager(app)

# 飛輪 HTTP 端點(/api/v1/annotation, /api/v1/consent/withdraw)
try:
    from api_flywheel import flywheel_bp
    if flywheel_bp is not None:
        app.register_blueprint(flywheel_bp)
except Exception:
    pass

# 預設帳號（正式環境應改為資料庫驗證）
_USERS = {
    'admin': {
        'password_hash': hashlib.sha256(
            os.environ.get('ADMIN_PASSWORD', 'woundai-admin').encode()
        ).hexdigest(),
        'role': 'admin'
    },
    'clinician': {
        'password_hash': hashlib.sha256(
            os.environ.get('CLINICIAN_PASSWORD', 'woundai-clinician').encode()
        ).hexdigest(),
        'role': 'clinician'
    }
}

# 創建必要目錄
for folder in ['uploads', 'processed', 'models', 'logs']:
    os.makedirs(folder, exist_ok=True)

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/wound_analysis.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# 全局變數
processing_queue = Queue()
imagej_instance = None
wound_segmentation_model = None
tissue_classification_model = None

class WoundAnalysisService:
    """核心傷口分析服務類"""
    
    def __init__(self):
        self.setup_database()
        self.load_models()
        self.setup_imagej()
        
    def setup_database(self):
        """初始化數據庫"""
        conn = sqlite3.connect(app.config['DATABASE'])
        cursor = conn.cursor()
        
        # 創建分析記錄表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                image_hash TEXT NOT NULL,
                analysis_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                processing_time_ms INTEGER,
                image_quality REAL,
                depth_quality REAL,
                wound_area_cm2 REAL,
                wound_volume_cm3 REAL,
                wound_perimeter_cm REAL,
                tissue_composition TEXT,
                measurement_confidence REAL,
                processing_method TEXT,
                error_message TEXT
            )
        ''')
        
        # 創建模型訓練數據表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS training_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_hash TEXT NOT NULL,
                image_path TEXT NOT NULL,
                ground_truth_mask TEXT,
                wound_type TEXT,
                tissue_types TEXT,
                measurement_data TEXT,
                quality_score REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                validated BOOLEAN DEFAULT FALSE
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("數據庫初始化完成")
    
    def _resolve_onnx_model_path(self):
        """搜尋可用的 ONNX 模型檔案，依優先順序回傳第一個存在的路徑"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            # 本地 models/ 目錄(student 蒸餾輕量為最優先)
            os.path.join(base_dir, 'models', 'student_fp16.onnx'),
            os.path.join(base_dir, 'models', 'student_distilled.onnx'),
            os.path.join(base_dir, 'models', 'deepskin.onnx'),
            os.path.join(base_dir, 'models', 'wsm.onnx'),
            # 專案模型訓練目錄 - Deepskin (80MB, 較精準)
            os.path.join(base_dir, '..', '..', '雲端 AI 模型訓練及分析服務',
                         'Deepskin-main', 'deepskin.onnx'),
            # 專案模型訓練目錄 - WSM (8MB, 輕量)
            os.path.join(base_dir, '..', '..', '雲端 AI 模型訓練及分析服務',
                         'wound-segmentation-master', 'wsm.onnx'),
        ]
        for path in candidates:
            resolved = os.path.normpath(path)
            if os.path.isfile(resolved):
                return resolved
        return None

    def load_models(self):
        """加載AI模型 - 優先 ONNX Runtime，其次 TensorFlow，最後降級至傳統方法"""
        global wound_segmentation_model, tissue_classification_model

        # ---- 第一優先：ONNX Runtime ----
        if ONNX_AVAILABLE:
            onnx_path = self._resolve_onnx_model_path()
            if onnx_path:
                try:
                    sess_options = ort.SessionOptions()
                    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                    # 優先 GPU，回退 CPU
                    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                    session = ort.InferenceSession(onnx_path, sess_options, providers=providers)
                    wound_segmentation_model = session
                    self._onnx_model_path = onnx_path
                    self._model_backend = 'onnxruntime'
                    active_providers = session.get_providers()
                    logger.info(f"成功加載 ONNX 傷口分割模型: {onnx_path}")
                    logger.info(f"ONNX 執行提供者: {active_providers}")
                    # 記錄模型輸入/輸出資訊以利除錯
                    inp = session.get_inputs()[0]
                    logger.info(f"ONNX 模型輸入: name={inp.name}, shape={inp.shape}, type={inp.type}")
                    # M2: 載入時對齊 SSOT input shape(防止靜默用錯前處理)
                    try:
                        import json as _j
                        _sp = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..","engineering","phase0","preprocessing.json"))
                        _ss = _j.load(open(_sp, encoding="utf-8")) if os.path.exists(_sp) else {}
                        _key = next((k for k in ("student","wsm","deepskin","fusegnet","smp") if k in os.path.basename(onnx_path).lower()), None)
                        _exp = ((_ss.get("models",{}) or {}).get(_key or "",{}) or {}).get("input_size")
                        _got = [d for d in inp.shape if isinstance(d,int) and d>3]
                        if _exp and len(_got)>=2 and (int(_exp[0]),int(_exp[1])) != (int(_got[0]),int(_got[1])):
                            logger.warning(f"⚠ SSOT 對齊失敗: 模型 {_key} input {_got} ≠ SSOT {_exp};前處理恐錯,請使 preprocessing.json 與模型一致")
                        else:
                            logger.info(f"SSOT 對齊檢查通過: {_key} input {_got}")
                    except Exception as _e:
                        logger.warning(f"SSOT 對齊檢查略過: {_e}")
                    return  # 成功，不需繼續
                except Exception as e:
                    logger.error(f"ONNX 模型加載失敗 ({onnx_path}): {e}")
            else:
                logger.warning("未找到任何 ONNX 模型檔案")

        # ---- 第二優先：TensorFlow / Keras ----
        if TENSORFLOW_AVAILABLE:
            try:
                model_path = os.path.join(app.config['MODEL_FOLDER'], 'wound_segmentation.h5')
                if os.path.exists(model_path):
                    wound_segmentation_model = tf.keras.models.load_model(model_path)
                    self._model_backend = 'tensorflow'
                    logger.info(f"成功加載 TensorFlow 傷口分割模型: {model_path}")
                else:
                    logger.warning(f"TensorFlow 模型檔案不存在: {model_path}")

                tissue_model_path = os.path.join(app.config['MODEL_FOLDER'], 'tissue_classification.h5')
                if os.path.exists(tissue_model_path):
                    tissue_classification_model = tf.keras.models.load_model(tissue_model_path)
                    logger.info(f"成功加載組織分類模型: {tissue_model_path}")

                if wound_segmentation_model is not None:
                    return  # 成功
            except Exception as e:
                logger.error(f"TensorFlow 模型加載失敗: {e}")

        # ---- 降級模式：無 ML 模型可用 ----
        self._model_backend = 'traditional_hsv'
        logger.warning("=" * 60)
        logger.warning("降級模式: 無可用的 ML 模型 (ONNX / TensorFlow)")
        logger.warning("傷口分割將使用 HSV 色彩空間傳統方法，精確度較低")
        logger.warning("請部署 ONNX 模型至 models/ 目錄以啟用 AI 推論")
        logger.warning("=" * 60)
    
    def setup_imagej(self):
        """初始化ImageJ無頭模式"""
        global imagej_instance
        
        if IMAGEJ_AVAILABLE:
            try:
                # 啟動ImageJ（無頭模式）
                scyjava.config.add_option('-Xmx4g')  # 分配4GB內存
                imagej_instance = imagej.init(mode='headless')
                logger.info("ImageJ無頭模式初始化成功")
                
                # 測試ImageJ功能
                test_result = imagej_instance.op().run("math.add", 5, 3)
                logger.info(f"ImageJ測試成功: 5 + 3 = {test_result}")
                
            except Exception as e:
                logger.error(f"ImageJ初始化失敗: {e}")
                imagej_instance = None
        else:
            logger.warning("ImageJ不可用，將使用OpenCV替代方案")

# 初始化服務
analysis_service = WoundAnalysisService()

@app.route('/api/auth/login', methods=['POST'])
def login():
    """取得 JWT Token（username + password）"""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '')
    password = data.get('password', '')

    user = _USERS.get(username)
    if not user:
        return jsonify({'error': '帳號或密碼錯誤'}), 401

    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if password_hash != user['password_hash']:
        return jsonify({'error': '帳號或密碼錯誤'}), 401

    token = create_access_token(
        identity=username,
        additional_claims={'role': user['role']}
    )
    return jsonify({'access_token': token, 'role': user['role']}), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康檢查端點"""
    status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'imagej': IMAGEJ_AVAILABLE and imagej_instance is not None,
            'tensorflow': TENSORFLOW_AVAILABLE and wound_segmentation_model is not None,
            'database': True
        },
        'version': '1.0.0'
    }
    
    return jsonify(status)

@app.route('/api/analyze', methods=['POST'])
@app.route('/api/analyze_wound', methods=['POST'])
@jwt_required()
def analyze_wound():
    """
    主要分析端點 - 接收圖像和深度數據進行傷口分析
    按照技術文件建議的完整分析流程
    """
    start_time = time.time()
    session_id = request.headers.get('Session-ID', 'anonymous')
    
    try:
        # 1. 驗證請求數據
        if 'image' not in request.files:
            return jsonify({'error': '缺少圖像文件'}), 400
        
        image_file = request.files['image']
        # 深度數據可以是 multipart file 或 base64 字串
        # 並可附帶 depth_unit: 'm'|'cm'|'mm'（預設 'm'）
        depth_file = request.files.get('depth_data')
        depth_data = request.form.get('depth_data')  # Base64編碼的深度數據
        depth_unit = request.form.get('depth_unit', 'm')
        roi_data = request.form.get('roi_data')      # ROI座標 (JSON)
        calibration_data = request.form.get('calibration_data')  # 校準數據
        
        # 2. 處理上傳的圖像
        image_array = process_uploaded_image(image_file)
        image_hash = calculate_image_hash(image_array)
        
        logger.info(f"開始分析: Session={session_id}, Hash={image_hash[:8]}")
        
        # 3. 處理深度數據
        depth_array = None
        if depth_file:
            try:
                raw_bytes = depth_file.read()
                flat = np.frombuffer(raw_bytes, dtype=np.float32)
                depth_height, depth_width = 192, 256
                if flat.size == depth_height * depth_width:
                    depth_array = flat.reshape((depth_height, depth_width))
                else:
                    logger.warning(f"深度數據尺寸不匹配: 期望{depth_height*depth_width}, 實際{flat.size}")
                    depth_array = None
            except Exception as e:
                logger.error(f"深度檔案解析失敗: {e}")
                depth_array = None
        elif depth_data:
            depth_array = process_depth_data(depth_data)

        # 將深度單位統一為公尺（下游再轉 cm）
        if depth_array is not None:
            if depth_unit.lower() == 'cm':
                depth_array = depth_array / 100.0
            elif depth_unit.lower() == 'mm':
                depth_array = depth_array / 1000.0
        
        # 4. 解析ROI和校準數據
        roi_coords = json.loads(roi_data) if roi_data else None
        calibration_info = json.loads(calibration_data) if calibration_data else None
        
        # 5. 執行核心分析流程
        analysis_result = perform_comprehensive_analysis(
            image=image_array,
            depth=depth_array,
            roi=roi_coords,
            calibration=calibration_info,
            session_id=session_id
        )
        
        # 6. 記錄分析結果
        processing_time = int((time.time() - start_time) * 1000)
        save_analysis_record(session_id, image_hash, analysis_result, processing_time)
        
        # 7. 準備響應
        response = {
            'success': True,
            'session_id': session_id,
            'processing_time_ms': processing_time,
            'image_hash': image_hash,
            'analysis': analysis_result,
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"分析完成: Session={session_id}, 耗時={processing_time}ms")
        return jsonify(response)
        
    except Exception as e:
        error_msg = f"分析失敗: {str(e)}"
        logger.error(f"Session={session_id}, Error={error_msg}")
        
        return jsonify({
            'success': False,
            'error': error_msg,
            'session_id': session_id,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/batch_analyze', methods=['POST'])
@jwt_required()
def batch_analyze():
    """批量分析端點 - 用於處理多張傷口圖像"""
    start_time = time.time()
    session_id = request.headers.get('Session-ID', f'batch_{int(time.time())}')
    
    try:
        files = request.files.getlist('images')
        if not files:
            return jsonify({'error': '未提供圖像文件'}), 400
        
        results = []
        
        for i, image_file in enumerate(files):
            try:
                image_array = process_uploaded_image(image_file)
                image_hash = calculate_image_hash(image_array)
                
                # 執行分析
                analysis_result = perform_comprehensive_analysis(
                    image=image_array,
                    depth=None,  # 批量處理暫不支援深度
                    roi=None,
                    calibration=None,
                    session_id=f"{session_id}_image_{i}"
                )
                
                results.append({
                    'image_index': i,
                    'image_hash': image_hash,
                    'analysis': analysis_result
                })
                
            except Exception as e:
                results.append({
                    'image_index': i,
                    'error': str(e)
                })
        
        processing_time = int((time.time() - start_time) * 1000)
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'processing_time_ms': processing_time,
            'results_count': len(results),
            'results': results,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'session_id': session_id
        }), 500

@app.route('/api/train', methods=['POST'])
@jwt_required()
def contribute_training_data():
    """接收用戶標註的訓練數據，用於模型改進"""
    try:
        session_id = request.headers.get('Session-ID', 'anonymous')
        
        image_file = request.files.get('image')
        ground_truth_mask = request.files.get('mask')  # 用戶標註的遮罩
        metadata = request.form.get('metadata')  # JSON格式的元數據
        
        if not image_file:
            return jsonify({'error': '缺少圖像文件'}), 400
        
        # 處理圖像和遮罩
        image_array = process_uploaded_image(image_file)
        image_hash = calculate_image_hash(image_array)
        
        # 保存訓練數據
        image_path = save_training_image(image_array, image_hash)
        
        mask_path = None
        if ground_truth_mask:
            mask_array = process_uploaded_image(ground_truth_mask)
            mask_path = save_training_mask(mask_array, image_hash)
        
        # 解析元數據
        meta_info = json.loads(metadata) if metadata else {}
        
        # 存儲到訓練數據庫
        save_training_data_record(
            image_hash, image_path, mask_path, meta_info, session_id
        )
        
        logger.info(f"收到訓練數據: Hash={image_hash[:8]}, Session={session_id}")
        
        return jsonify({
            'success': True,
            'message': '訓練數據已接收，將用於模型改進',
            'data_id': image_hash,
            'session_id': session_id
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/model/retrain', methods=['POST'])
@jwt_required()
def trigger_model_retraining():
    """觸發模型重新訓練（需要管理員權限）"""
    try:
        claims = get_jwt()
        if claims.get('role') != 'admin':
            return jsonify({'error': '權限不足，需要管理員角色'}), 403
        
        # 異步觸發重新訓練
        training_thread = threading.Thread(target=retrain_models_async)
        training_thread.daemon = True
        training_thread.start()
        
        return jsonify({
            'success': True,
            'message': '模型重新訓練已啟動',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# 核心處理函數

def process_uploaded_image(image_file):
    """處理上傳的圖像文件"""
    image_bytes = image_file.read()
    image = Image.open(io.BytesIO(image_bytes))
    
    # 轉換為RGB格式
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # 標準化大小
    image = image.resize((512, 512), Image.Resampling.LANCZOS)
    
    # 轉換為numpy數組
    return np.array(image)

def process_depth_data(depth_data_base64):
    """處理Base64編碼的深度數據"""
    try:
        depth_bytes = base64.b64decode(depth_data_base64)
        depth_array = np.frombuffer(depth_bytes, dtype=np.float32)
        
        # 重塑為標準深度圖尺寸
        depth_height, depth_width = 192, 256  # ARKit標準
        if len(depth_array) == depth_height * depth_width:
            return depth_array.reshape((depth_height, depth_width))
        else:
            logger.warning(f"深度數據尺寸不匹配: 期望{depth_height*depth_width}, 實際{len(depth_array)}")
            return None
            
    except Exception as e:
        logger.error(f"深度數據處理失敗: {e}")
        return None

def calculate_image_hash(image_array):
    """計算圖像的SHA256哈希值"""
    image_bytes = image_array.tobytes()
    return hashlib.sha256(image_bytes).hexdigest()

def perform_comprehensive_analysis(image, depth, roi, calibration, session_id):
    """
    執行綜合傷口分析 - 按照技術文件建議的完整流程
    """
    analysis_result = {
        'image_quality': {},
        'depth_quality': {},
        'wound_detection': {},
        'measurements': {},
        'tissue_analysis': {},
        'confidence_metrics': {},
        'processing_method': 'hybrid'
    }
    
    # 1. 圖像品質評估
    analysis_result['image_quality'] = assess_image_quality(image)
    
    # 2. 深度數據品質評估
    if depth is not None:
        analysis_result['depth_quality'] = assess_depth_quality(depth)
    
    # 3. 傷口檢測和分割
    if wound_segmentation_model is not None:
        # 使用AI模型
        wound_mask, confidence = segment_wound_ai(image)
        analysis_result['processing_method'] = 'ai_model'
    else:
        # 使用傳統方法
        wound_mask, confidence = segment_wound_traditional(image)
        analysis_result['processing_method'] = 'traditional'
    
    analysis_result['wound_detection'] = {
        'has_wound': np.any(wound_mask > 0.5),
        'confidence': float(confidence),
        'mask_area_pixels': int(np.sum(wound_mask > 0.5))
    }
    
    # 4. 測量計算
    if analysis_result['wound_detection']['has_wound']:
        measurements = calculate_measurements(wound_mask, depth, calibration)
        analysis_result['measurements'] = measurements
        
        # 5. 組織分析
        tissue_analysis = analyze_tissue_composition(image, wound_mask)
        analysis_result['tissue_analysis'] = tissue_analysis
    
    # 6. 置信度評估
    analysis_result['confidence_metrics'] = calculate_confidence_metrics(
        analysis_result, image, depth
    )
    
    return analysis_result

@app.route('/api/calculate_volume', methods=['POST'])
@jwt_required()
def calculate_volume_endpoint():
    """JSON 端點：依 cm_per_pixel 與深度(cm)進行像素積分體積計算"""
    try:
        payload = request.get_json(force=True, silent=False)
        if not payload:
            return jsonify({'error': '缺少請求內容'}), 400

        depth_values = payload.get('depth_data')  # 扁平 float 陣列（長度 256*192）
        mask_png_b64 = payload.get('mask_data')   # 可選，base64 PNG
        cm_per_pixel = float(payload.get('cm_per_pixel', 0.0))
        if cm_per_pixel <= 0:
            return jsonify({'error': 'cm_per_pixel 無效'}), 400

        depth_width, depth_height = 256, 192
        if not depth_values or len(depth_values) != depth_width * depth_height:
            return jsonify({'error': 'depth_data 尺寸不符(應為 256x192)'}), 400

        depth_array = np.array(depth_values, dtype=np.float32).reshape((depth_height, depth_width))

        # 解析遮罩（若提供）
        if mask_png_b64:
            try:
                mask_bytes = base64.b64decode(mask_png_b64)
                mask_img = Image.open(io.BytesIO(mask_bytes)).convert('L')
                mask_img = mask_img.resize((depth_width, depth_height), Image.Resampling.NEAREST)
                mask_np = np.array(mask_img)
                lesion_mask = (mask_np > 128)
            except Exception:
                lesion_mask = np.ones_like(depth_array, dtype=bool)
        else:
            lesion_mask = np.ones_like(depth_array, dtype=bool)

        # 單位：深度應為 cm；若前端傳 m，請在前端或另行提供 depth_unit
        area_per_pixel_cm2 = cm_per_pixel * cm_per_pixel
        valid_depths = depth_array[lesion_mask]
        valid_depths = valid_depths[(valid_depths > 0.01) & (valid_depths < 300.0)]
        if valid_depths.size == 0:
            return jsonify({'error': '有效深度不足'}), 400

        volume_cm3 = float(np.sum(valid_depths) * area_per_pixel_cm2)
        average_depth = float(np.mean(valid_depths))
        max_depth = float(np.max(valid_depths))
        surface_area = float(np.sum(lesion_mask) * area_per_pixel_cm2)
        depth_coverage = float(valid_depths.size) / float(depth_array.size)

        return jsonify({
            'volume': volume_cm3,
            'surfaceArea': surface_area,
            'averageDepth': average_depth,
            'maxDepth': max_depth,
            'confidence': min(0.9, depth_coverage),
            'depthCoverage': depth_coverage,
            'method': 'local_pixelwise_integration'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def assess_image_quality(image):
    """評估圖像品質"""
    # 轉為灰階
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    
    # 計算各項指標
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()  # 銳利度
    brightness = np.mean(gray) / 255.0  # 亮度
    contrast = gray.std() / 255.0  # 對比度
    
    # 噪聲評估（使用高斯濾波前後的差異）
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    noise_level = np.mean(np.abs(gray.astype(float) - blurred.astype(float))) / 255.0
    
    return {
        'sharpness': float(min(1.0, laplacian_var / 500.0)),  # 正規化
        'brightness': float(brightness),
        'contrast': float(contrast),
        'noise_level': float(noise_level),
        'overall_score': float((laplacian_var / 500.0 + contrast + (1 - abs(brightness - 0.5) * 2)) / 3.0)
    }

def assess_depth_quality(depth_array):
    """評估深度數據品質"""
    valid_depth = depth_array[(depth_array > 0.001) & (depth_array < 2.0)]
    
    if len(valid_depth) == 0:
        return {
            'coverage': 0.0,
            'consistency': 0.0,
            'noise_level': 1.0,
            'overall_score': 0.0
        }
    
    coverage = len(valid_depth) / depth_array.size
    consistency = 1.0 - (valid_depth.std() / valid_depth.mean()) if valid_depth.mean() > 0 else 0.0
    noise_level = np.mean(np.abs(np.diff(valid_depth))) / valid_depth.mean() if valid_depth.mean() > 0 else 1.0
    
    return {
        'coverage': float(coverage),
        'consistency': float(max(0, min(1, consistency))),
        'noise_level': float(min(1, noise_level)),
        'overall_score': float((coverage + max(0, consistency) + (1 - min(1, noise_level))) / 3.0)
    }


# ---- SSOT 驅動前處理(M1 接線：依 preprocessing.json 按模型套 channel_order+normalize) ----
import json as _json
_SSOT_CACHE = None
def _load_ssot():
    global _SSOT_CACHE
    if _SSOT_CACHE is None:
        p = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", "engineering", "phase0", "preprocessing.json"))
        try:
            _SSOT_CACHE = _json.load(open(p, encoding="utf-8"))
        except Exception as e:
            logger.warning(f"無法讀取 SSOT preprocessing.json: {e}; 退回 [0,1] RGB")
            _SSOT_CACHE = {}
    return _SSOT_CACHE

def _active_model_key():
    path = (getattr(analysis_service, "_onnx_model_path", "") or "")
    name = os.path.basename(path).lower()
    for k in ("student", "wsm", "deepskin", "fusegnet", "smp"):
        if k in name:
            return k
    return None

def _apply_ssot_preproc(resized_rgb, model_key):
    """依 SSOT 對 RGB 影像套 channel_order(BGR 翻轉)+normalize。回傳 float32 連續陣列。"""
    cfg = (_load_ssot().get("models", {}) or {}).get(model_key or "", {})
    x = resized_rgb.astype(np.float32)
    if cfg.get("channel_order") == "BGR":
        x = x[..., ::-1]
    nrm = cfg.get("normalize", "[0,1]")
    if nrm == "[-1,1]":
        x = x / 127.5 - 1.0
    elif nrm == "imagenet":
        mean = np.array(_load_ssot().get("imagenet_mean", [0.485, 0.456, 0.406]), np.float32)
        std = np.array(_load_ssot().get("imagenet_std", [0.229, 0.224, 0.225]), np.float32)
        x = (x / 255.0 - mean) / std
    else:
        x = x / 255.0
    return np.ascontiguousarray(x)

def segment_wound_ai(image):
    """使用AI模型進行傷口分割 - 支援 ONNX Runtime 與 TensorFlow Keras。"""
    try:
        # ---- ONNX Runtime 推論 ----
        if ONNX_AVAILABLE and isinstance(wound_segmentation_model, ort.InferenceSession):
            inp_info = wound_segmentation_model.get_inputs()[0]
            shape = inp_info.shape
            # 從模型 input 取空間尺寸 (支援 NHWC / NCHW)
            mkey = _active_model_key()
            scfg = (_load_ssot().get("models", {}) or {}).get(mkey or "", {})
            ssize = scfg.get("input_size")
            h_in, w_in = (int(ssize[0]), int(ssize[1])) if ssize else (256, 256)
            if not ssize and len(shape) == 4:
                spatial = [s for s in shape[1:] if isinstance(s, int) and s > 3]
                if len(spatial) >= 2:
                    h_in, w_in = int(spatial[0]), int(spatial[1])
            orig_h, orig_w = image.shape[:2]
            resized = cv2.resize(image, (w_in, h_in), interpolation=cv2.INTER_CUBIC)
            x = _apply_ssot_preproc(resized, mkey)   # SSOT: channel_order + normalize(按模型)
            # NCHW 偵測
            if len(shape) == 4 and shape[1] == 3:
                x = np.transpose(x, (2, 0, 1))
            x = np.expand_dims(x, axis=0)
            outputs = wound_segmentation_model.run(None, {inp_info.name: x})
            pred = np.squeeze(outputs[0], axis=0)
            if pred.ndim == 3 and pred.shape[0] in (1, 2, 3):
                pred = np.transpose(pred, (1, 2, 0))
            if pred.ndim == 3 and pred.shape[-1] >= 3:
                wound_mask = pred[..., 2]  # Deepskin: ch2 = wound
            elif pred.ndim == 3:
                wound_mask = pred[..., -1]
            else:
                wound_mask = pred
            wound_mask = cv2.resize(
                wound_mask.astype(np.float32), (orig_w, orig_h),
                interpolation=cv2.INTER_LINEAR,
            )
            wound_mask = np.clip(wound_mask, 0.0, 1.0)
            confidence = float(np.mean(np.maximum(wound_mask, 1.0 - wound_mask)))
            return wound_mask, confidence

        # ---- TensorFlow Keras 推論 ----
        input_image = image.astype(np.float32) / 255.0
        input_image = np.expand_dims(input_image, axis=0)
        prediction = wound_segmentation_model.predict(input_image, verbose=0)
        wound_mask = prediction[0, :, :, 0]
        confidence = float(np.mean(np.max([wound_mask, 1 - wound_mask], axis=0)))
        return wound_mask, confidence

    except Exception as e:
        logger.error(f"AI分割失敗: {e}")
        return segment_wound_traditional(image)

# ===== 雲端 A∪U 集成 escalate 端點(雙軌路由:端上判難→上雲) =====
_CLOUD_AU = {"a": None, "u": None, "ver": "AU-2026-06"}
def _resolve_au_paths():
    base = os.path.dirname(os.path.abspath(__file__))
    cands = {
        "a": [os.path.join(base,"models","a_unet.onnx"),
              os.path.join(base,"..","..","WoundAI_weights_archive","onnx_export","a_unet.onnx")],
        "u": [os.path.join(base,"models","unetpp.onnx"),
              os.path.join(base,"..","..","WoundAI_weights_archive","onnx_export","unetpp.onnx")],
    }
    out = {}
    for k, lst in cands.items():
        out[k] = next((os.path.normpath(p) for p in lst if os.path.isfile(os.path.normpath(p))), None)
    return out
def _load_cloud_au():
    if not ONNX_AVAILABLE: return None, None
    if _CLOUD_AU["a"] is None:
        p = _resolve_au_paths()
        if not p["a"] or not p["u"]: return None, None
        _CLOUD_AU["a"] = ort.InferenceSession(p["a"], providers=["CPUExecutionProvider"])
        _CLOUD_AU["u"] = ort.InferenceSession(p["u"], providers=["CPUExecutionProvider"])
    return _CLOUD_AU["a"], _CLOUD_AU["u"]
def _au_infer(sess, image_rgb):
    # a_unet/unetpp: 256 NHWC, [-1,1] RGB
    r = cv2.resize(image_rgb, (256, 256)).astype(np.float32) / 127.5 - 1.0
    o = np.squeeze(sess.run(None, {sess.get_inputs()[0].name: r[None].astype(np.float32)})[0]).astype(np.float32)
    if o.ndim == 3: o = o[..., 0]
    if o.min() < 0 or o.max() > 1: o = 1.0/(1.0+np.exp(-np.clip(o,-30,30)))
    return o

@app.route('/api/v1/segment/escalate', methods=['POST'])
@jwt_required()
def segment_escalate():
    """端上判為難例時呼叫:回傳雲端 A∪U(a_unet⊕unet++ 機率融合 thr0.4)遮罩。"""
    if 'image' not in request.files:
        return jsonify({'error': '缺少圖像文件'}), 400
    a, u = _load_cloud_au()
    if a is None:
        return jsonify({'error': '雲端 A∪U 模型不可用(請部署 models/a_unet.onnx, unetpp.onnx)', 'route': 'cloud_unavailable'}), 503
    img = process_uploaded_image(request.files['image'])      # RGB
    fused = 0.5 * _au_infer(a, img) + 0.5 * _au_infer(u, img)  # A∪U 機率融合
    mask = (cv2.resize(fused, (img.shape[1], img.shape[0])) > 0.40).astype(np.uint8) * 255
    ok, buf = cv2.imencode('.png', mask)
    mask_b64 = base64.b64encode(buf.tobytes()).decode('ascii') if ok else None
    return jsonify({
        'mask_png_b64': mask_b64,
        'model': 'ensemble.AU',
        'model_version': _CLOUD_AU["ver"],
        'route': 'cloud',
        'threshold': 0.40,
        'note': 'A∪U 機率融合(a_unet⊕unet++);面積由端上校正計算'
    }), 200

def segment_wound_traditional(image):
    """使用傳統方法進行傷口分割"""
    # 轉為HSV色彩空間
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    
    # 定義傷口顏色範圍（紅色系）
    lower_red1 = np.array([0, 50, 50])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 50, 50])
    upper_red2 = np.array([180, 255, 255])
    
    # 創建遮罩
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    wound_mask = cv2.bitwise_or(mask1, mask2)
    
    # 形態學操作清理遮罩
    kernel = np.ones((5, 5), np.uint8)
    wound_mask = cv2.morphologyEx(wound_mask, cv2.MORPH_OPEN, kernel)
    wound_mask = cv2.morphologyEx(wound_mask, cv2.MORPH_CLOSE, kernel)
    
    # 正規化到0-1範圍
    wound_mask = wound_mask.astype(np.float32) / 255.0
    
    # 計算置信度（基於遮罩的一致性）
    confidence = float(np.mean(wound_mask) * 2)  # 簡化的置信度計算
    
    return wound_mask, min(1.0, confidence)

def calculate_measurements(wound_mask, depth_data, calibration):
    """計算傷口測量數據"""
    # 像素面積
    pixel_area = np.sum(wound_mask > 0.5)
    
    # 校準：優先使用 cm_per_pixel，其次 pixels_per_mm
    cm_per_pixel = None
    pixels_per_mm = None
    if calibration:
        if 'cm_per_pixel' in calibration:
            cm_per_pixel = float(calibration['cm_per_pixel'])
        if 'pixels_per_mm' in calibration and not cm_per_pixel:
            pixels_per_mm = float(calibration['pixels_per_mm'])
            if pixels_per_mm > 0:
                cm_per_pixel = 1.0 / (pixels_per_mm * 10.0)
    if cm_per_pixel is None:
        # 回退預設：pixels_per_mm=10 等價 cm_per_pixel=0.01
        cm_per_pixel = 0.01
    
    # 面積：像素面積 × (cm/pixel)^2
    area_cm2 = float(pixel_area) * (cm_per_pixel * cm_per_pixel)
    
    # 周長計算
    contours, _ = cv2.findContours(
        (wound_mask > 0.5).astype(np.uint8), 
        cv2.RETR_EXTERNAL, 
        cv2.CHAIN_APPROX_SIMPLE
    )
    
    perimeter_pixels = 0
    if contours:
        perimeter_pixels = cv2.arcLength(contours[0], True)
    
    perimeter_cm = float(perimeter_pixels) * cm_per_pixel
    
    # 體積計算（如果有深度數據）
    volume_cm3 = 0.0
    max_depth_cm = 0.0
    avg_depth_cm = 0.0
    
    if depth_data is not None:
        # 將深度數據縮放到與傷口遮罩相同的尺寸
        depth_resized = cv2.resize(depth_data, (wound_mask.shape[1], wound_mask.shape[0]))
        
        # 計算傷口區域的深度統計
        wound_depths = depth_resized[wound_mask > 0.5]
        if len(wound_depths) > 0:
            # 深度目前為公尺 → 轉換為公分
            wound_depths_cm = wound_depths * 100.0
            max_depth_cm = float(np.max(wound_depths_cm))
            avg_depth_cm = float(np.mean(wound_depths_cm))
            
            # 像素積分法：每像素面積為 (cm_per_pixel^2)
            lesion_mask = (wound_mask > 0.5)
            area_per_pixel_cm2 = cm_per_pixel * cm_per_pixel
            volume_cm3 = float(np.sum(wound_depths_cm) * area_per_pixel_cm2)
    
    return {
        'area_cm2': float(area_cm2),
        'perimeter_cm': float(perimeter_cm),
        'volume_cm3': float(volume_cm3),
        'max_depth_cm': float(max_depth_cm),
        'avg_depth_cm': float(avg_depth_cm),
        'pixel_area': int(pixel_area),
        'cm_per_pixel': float(cm_per_pixel)
    }

def analyze_tissue_composition(image, wound_mask):
    """分析組織成分"""
    # 提取傷口區域
    wound_region = image[wound_mask > 0.5]
    
    if len(wound_region) == 0:
        return {
            'granulation_percentage': 0.0,
            'necrotic_percentage': 0.0,
            'epithelial_percentage': 0.0,
            'fibrin_percentage': 0.0,
            'healthy_percentage': 0.0
        }
    
    # 轉換到HSV色彩空間進行分析
    hsv_region = cv2.cvtColor(wound_region.reshape(-1, 1, 3), cv2.COLOR_RGB2HSV)
    hsv_region = hsv_region.reshape(-1, 3)
    
    total_pixels = len(hsv_region)
    
    # 基於顏色特徵分類組織類型
    granulation_count = 0  # 紅色 - 肉芽組織
    necrotic_count = 0     # 黑色/深棕色 - 壞死組織
    epithelial_count = 0   # 粉紅色 - 上皮組織
    fibrin_count = 0       # 黃白色 - 纖維組織
    
    for pixel in hsv_region:
        h, s, v = pixel
        
        if s > 100 and v > 100:  # 有色彩且明亮
            if (h < 10 or h > 160) and s > 150:  # 紅色
                granulation_count += 1
            elif 10 <= h <= 30:  # 黃色
                fibrin_count += 1
            elif 140 <= h <= 160 and s < 150:  # 粉紅色
                epithelial_count += 1
        elif v < 80:  # 暗色
            necrotic_count += 1
    
    # 計算百分比
    return {
        'granulation_percentage': float(granulation_count / total_pixels * 100),
        'necrotic_percentage': float(necrotic_count / total_pixels * 100),
        'epithelial_percentage': float(epithelial_count / total_pixels * 100),
        'fibrin_percentage': float(fibrin_count / total_pixels * 100),
        'healthy_percentage': float(max(0, 100 - (granulation_count + necrotic_count + epithelial_count + fibrin_count) / total_pixels * 100))
    }

def calculate_confidence_metrics(analysis_result, image, depth):
    """計算整體置信度指標"""
    scores = []
    
    # 圖像品質得分
    img_quality = analysis_result['image_quality']['overall_score']
    scores.append(img_quality)
    
    # 深度品質得分（如果有）
    if 'depth_quality' in analysis_result and analysis_result['depth_quality']:
        depth_quality = analysis_result['depth_quality']['overall_score']
        scores.append(depth_quality)
    
    # 傷口檢測置信度
    wound_confidence = analysis_result['wound_detection']['confidence']
    scores.append(wound_confidence)
    
    # 測量可靠性（基於傷口大小合理性）
    if 'measurements' in analysis_result and analysis_result['measurements']:
        area = analysis_result['measurements'].get('area_cm2', 0.0)
        size_reliability = 1.0 if 0.1 <= area <= 100.0 else 0.5  # 合理的傷口大小範圍
        scores.append(size_reliability)
    
    overall_confidence = float(np.mean(scores))
    
    return {
        'overall_confidence': overall_confidence,
        'image_quality_score': float(img_quality),
        'detection_confidence': float(wound_confidence),
        'is_medical_grade': overall_confidence >= 0.8,
        'recommendation': get_confidence_recommendation(overall_confidence)
    }

def get_confidence_recommendation(confidence):
    """根據置信度提供建議"""
    if confidence >= 0.9:
        return "測量結果可信度極高，可用於醫療參考"
    elif confidence >= 0.7:
        return "測量結果可信度良好，建議結合臨床評估"
    elif confidence >= 0.5:
        return "測量結果可信度中等，建議重新拍攝或手動確認"
    else:
        return "測量結果可信度較低，建議改善拍攝條件後重新測量"

# 數據庫操作函數

def save_analysis_record(session_id, image_hash, analysis_result, processing_time):
    """保存分析記錄到數據庫"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        cursor = conn.cursor()
        
        measurements = analysis_result.get('measurements', {})
        confidence = analysis_result.get('confidence_metrics', {})
        
        cursor.execute('''
            INSERT INTO analysis_records 
            (session_id, image_hash, processing_time_ms, image_quality, depth_quality,
             wound_area_cm2, wound_volume_cm3, wound_perimeter_cm, tissue_composition,
             measurement_confidence, processing_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            image_hash,
            processing_time,
            analysis_result.get('image_quality', {}).get('overall_score', 0.0),
            analysis_result.get('depth_quality', {}).get('overall_score', 0.0),
            measurements.get('area_cm2', 0.0),
            measurements.get('volume_cm3', 0.0),
            measurements.get('perimeter_cm', 0.0),
            json.dumps(analysis_result.get('tissue_analysis', {})),
            confidence.get('overall_confidence', 0.0),
            analysis_result.get('processing_method', 'unknown')
        ))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"保存分析記錄失敗: {e}")

def save_training_data_record(image_hash, image_path, mask_path, metadata, session_id):
    """保存訓練數據記錄"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO training_data 
            (image_hash, image_path, ground_truth_mask, wound_type, tissue_types, measurement_data, quality_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            image_hash,
            image_path,
            mask_path,
            metadata.get('wound_type', ''),
            json.dumps(metadata.get('tissue_types', {})),
            json.dumps(metadata.get('measurements', {})),
            metadata.get('quality_score', 0.0)
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"訓練數據已保存: {image_hash[:8]}")
        
    except Exception as e:
        logger.error(f"保存訓練數據失敗: {e}")

def save_training_image(image_array, image_hash):
    """保存訓練用圖像"""
    filename = f"training_{image_hash[:16]}.jpg"
    filepath = os.path.join('uploads', filename)
    
    image_pil = Image.fromarray(image_array)
    image_pil.save(filepath, 'JPEG', quality=95)
    
    return filepath

def save_training_mask(mask_array, image_hash):
    """保存訓練用遮罩"""
    filename = f"mask_{image_hash[:16]}.png"
    filepath = os.path.join('uploads', filename)
    
    mask_pil = Image.fromarray(mask_array)
    mask_pil.save(filepath, 'PNG')
    
    return filepath

def retrain_models_async():
    """異步重新訓練模型"""
    logger.info("開始重新訓練模型...")
    
    try:
        # 這裡應實現完整的模型重新訓練邏輯
        # 1. 從數據庫加載訓練數據
        # 2. 預處理數據
        # 3. 訓練模型
        # 4. 驗證模型性能
        # 5. 更新生產模型
        
        time.sleep(60)  # 模擬訓練時間
        logger.info("模型重新訓練完成")
        
    except Exception as e:
        logger.error(f"模型重新訓練失敗: {e}")

# ===== 分類/嚴重度端點(PUSH 量表 + 組織 v2;方案1+3 接線) =====
_ENG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "engineering"))
def _load_classify_mods():
    """延遲載入 engineering 的 wound_classifier(v2)/clinical_rules(PUSH)/aruco;失敗回 None。"""
    import sys
    for sub in ("phase2", "phase1"):
        pth = os.path.join(_ENG, sub)
        if pth not in sys.path: sys.path.insert(0, pth)
    try:
        from wound_classifier import tissue_proxy_v2
        from clinical_rules import push_score
        try:
            import aruco_calibrate as _ac
        except Exception:
            _ac = None
        return tissue_proxy_v2, push_score, _ac
    except Exception as e:
        logger.error(f"classify 模組載入失敗: {e}")
        return None

@app.route('/api/v1/classify', methods=['POST'])
@jwt_required()
def classify_wound():
    """分割→(ArUco/手動)校正面積→組織v2→PUSH 嚴重度。回傳標準階段結果。
    body(multipart): image=<jpg/png>; 選配 cm_per_pixel=<float>(無 ArUco 時手動校正)。"""
    mods = _load_classify_mods()
    if mods is None:
        return jsonify({'error': '分類模組不可用(engineering 模組缺)', 'stage': 'init'}), 503
    tissue_proxy_v2, push_score, _ac = mods
    if 'image' not in request.files:
        return jsonify({'error': '缺少 image'}), 400
    try:
        data = np.frombuffer(request.files['image'].read(), np.uint8)
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if bgr is None: return jsonify({'error': '影像解碼失敗'}), 400
        img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        H, W = img.shape[:2]
        # Stage2 分割(端上主力 student)
        wound_prob, conf = segment_wound_ai(img)
        thr = float(((_load_ssot().get("models", {}) or {}).get(_active_model_key() or "", {}) or {}).get("threshold", 0.4))
        mask = wound_prob > thr
        seg_model = _active_model_key(); route = "student"; escalated = False; au_ratio = None; iou_sa = None
        # 雙軌自動 escalate:難例(碎片/低對比→student 大幅低估)自動改用雲端 A∪U 集成
        # 判難靠「第二意見」(student vs A∪U),因 student 漏 segment 區域機率≈0、無自我訊號
        if str(request.form.get('escalate', 'on')).lower() not in ('off', '0', 'false'):
            try:
                _a, _u = _load_cloud_au()
                if _a is not None and _u is not None:
                    _fused = 0.5 * _au_infer(_a, img) + 0.5 * _au_infer(_u, img)
                    au_mask = cv2.resize(_fused, (W, H)) > 0.40
                    def _big(m):
                        cs, _h = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        return max((cv2.contourArea(c) for c in cs), default=0.0)
                    _sp, _ap = _big(mask), _big(au_mask)
                    _inter = float(np.logical_and(mask, au_mask).sum()); _uni = float(np.logical_or(mask, au_mask).sum())
                    iou_sa = round(_inter / _uni, 3) if _uni > 0 else 1.0
                    au_ratio = round(_ap / _sp, 2) if _sp > 0 else (999.0 if _ap > 0 else 0.0)
                    if _ap > 0 and (au_ratio > 1.5 or (iou_sa is not None and iou_sa < 0.5)):
                        mask = au_mask; route = "cloud_escalated(AU)"; escalated = True; seg_model = "ensemble.AU"
            except Exception as _e:
                logger.warning(f"escalate 略過: {_e}")
        # Stage3 校正面積:優先 ArUco,否則 cm_per_pixel(手動)
        area_cm2 = None; calib = "none"
        if _ac is not None:
            det = _ac.detect_marker(img)
            if det is not None:
                _mm = float((_load_ssot().get("calibration", {}) or {}).get("marker_mm_active", 12.0))
                area_cm2 = float(_ac.measure_area_cm2_ratio(mask.astype(np.uint8), det[0], marker_mm=_mm)); calib = f"aruco(marker {_mm}mm)"
        if area_cm2 is None:
            cpp = request.form.get('cm_per_pixel', type=float)
            if cpp: area_cm2 = float(mask.sum()) * (cpp ** 2); calib = "manual_cm_per_pixel"
        # Stage4 組織 v2 + Stage5 PUSH
        t = tissue_proxy_v2(img, mask)
        push = push_score(area_cm2, t)
        # 傷口輪廓多邊形(最大連通、approxPolyDP 精簡)→ 供 App 醫師修邊/飛輪標註
        wound_poly = []
        _cnts, _hh = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if _cnts:
            _bc = max(_cnts, key=cv2.contourArea)
            _ap = cv2.approxPolyDP(_bc, 0.01 * cv2.arcLength(_bc, True), True).reshape(-1, 2)
            wound_poly = [[int(x), int(y)] for x, y in _ap.tolist()]
        return jsonify({
            'stage2_segment': {'model': seg_model, 'wound_ratio': round(float(mask.mean()), 4), 'confidence': round(conf, 4),
                               'route': route, 'escalated': escalated, 'au_area_ratio': au_ratio, 'iou_student_au': iou_sa,
                               'wound_polygon': wound_poly},
            'stage3_calibrate': {'method': calib, 'area_cm2': (round(area_cm2, 2) if area_cm2 is not None else None),
                                 'note': ('未校正(無 ArUco 且未提供 cm_per_pixel)' if area_cm2 is None else None)},
            'stage4_tissue': {'method': 'v2(WB+HSV)', 'tissue_frac': {k: round(t[k], 3) for k in ('necrosis','slough','granulation','epithelial','other')}},
            'stage5_severity': {k: push[k] for k in ('tool','area_subscore','tissue_subscore','exudate_subscore','total_partial_img','total_full','range_full')},
            'disclaimer': '輔助用途、非診斷、需醫師確認;滲液量無法由單張影像判定,需醫師輸入'
        }), 200
    except Exception as e:
        logger.error(f"classify 失敗: {e}")
        return jsonify({'error': str(e), 'stage': 'inference'}), 500

if __name__ == '__main__':
    logger.info("啟動傷口分析Flask服務...")
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True
    )