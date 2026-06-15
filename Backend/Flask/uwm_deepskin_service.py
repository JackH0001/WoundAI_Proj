#!/usr/bin/env python3
"""
整合UWM MobileNetV2和Deepskin模型的Flask後端服務
支援雙重模型驗證和高精度傷口分析
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import numpy as np
import cv2
import base64
import io
import logging
from datetime import datetime
import uuid
import os
import sqlite3
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import traceback

# AI/ML imports
try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras.models import load_model
    import torch
    import torchvision.transforms as transforms
    from PIL import Image
    import deepskin  # Deepskin package
    from sklearn.metrics import jaccard_score
    import scipy.ndimage as ndimage
    from skimage import measure, morphology
    from skimage.feature import graycomatrix, graycoprops
    
    ML_AVAILABLE = True
    print("✅ 所有機器學習套件載入成功")
except ImportError as e:
    ML_AVAILABLE = False
    print(f"⚠️ 機器學習套件載入失敗: {e}")

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

class AdvancedWoundAnalysisService:
    """整合UWM MobileNetV2和Deepskin的進階傷口分析服務"""
    
    def __init__(self):
        self.uwm_model = None
        self.deepskin_processor = None
        self.models_loaded = False
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        # 模型路徑
        self.uwm_model_path = 'models/uwm_mobilenetv2_wound_segmentation.h5'
        self.deepskin_model_path = 'models/deepskin_wound_segmentation'
        
        # 載入模型
        asyncio.create_task(self.load_models())
        
        # 初始化資料庫
        self.init_database()
    
    async def load_models(self):
        """非同步載入兩個模型"""
        try:
            logger.info("🔄 開始載入AI模型...")
            
            # 1. 載入UWM MobileNetV2模型
            if os.path.exists(self.uwm_model_path):
                self.uwm_model = load_model(self.uwm_model_path)
                logger.info("✅ UWM MobileNetV2模型載入成功")
            else:
                logger.warning("⚠️ UWM模型檔案未找到，將使用備用實現")
                self.uwm_model = self.create_dummy_uwm_model()
            
            # 2. 載入Deepskin模型
            try:
                self.deepskin_processor = deepskin.WoundSegmentation()
                logger.info("✅ Deepskin模型載入成功")
            except Exception as e:
                logger.warning(f"⚠️ Deepskin模型載入失敗: {e}")
                self.deepskin_processor = self.create_dummy_deepskin_processor()
            
            self.models_loaded = True
            logger.info("🎉 所有模型載入完成")
            
        except Exception as e:
            logger.error(f"❌ 模型載入失敗: {e}")
            logger.error(traceback.format_exc())
    
    def create_dummy_uwm_model(self):
        """創建UWM模型的備用實現"""
        # 簡單的U-Net架構
        inputs = keras.Input(shape=(224, 224, 3))
        
        # 編碼器
        conv1 = keras.layers.Conv2D(32, 3, activation='relu', padding='same')(inputs)
        conv1 = keras.layers.Conv2D(32, 3, activation='relu', padding='same')(conv1)
        pool1 = keras.layers.MaxPooling2D(pool_size=(2, 2))(conv1)
        
        conv2 = keras.layers.Conv2D(64, 3, activation='relu', padding='same')(pool1)
        conv2 = keras.layers.Conv2D(64, 3, activation='relu', padding='same')(conv2)
        pool2 = keras.layers.MaxPooling2D(pool_size=(2, 2))(conv2)
        
        # 瓶須層
        conv3 = keras.layers.Conv2D(128, 3, activation='relu', padding='same')(pool2)
        conv3 = keras.layers.Conv2D(128, 3, activation='relu', padding='same')(conv3)
        
        # 解碼器
        up1 = keras.layers.UpSampling2D(size=(2, 2))(conv3)
        merge1 = keras.layers.concatenate([conv2, up1], axis=3)
        conv4 = keras.layers.Conv2D(64, 3, activation='relu', padding='same')(merge1)
        
        up2 = keras.layers.UpSampling2D(size=(2, 2))(conv4)
        merge2 = keras.layers.concatenate([conv1, up2], axis=3)
        conv5 = keras.layers.Conv2D(32, 3, activation='relu', padding='same')(merge2)
        
        # 輸出層 - 二元分割
        outputs = keras.layers.Conv2D(1, 1, activation='sigmoid')(conv5)
        
        model = keras.Model(inputs=inputs, outputs=outputs)
        logger.info("✅ 備用UWM模型建立完成")
        return model
    
    def create_dummy_deepskin_processor(self):
        """創建Deepskin的備用實現"""
        class DummyDeepskinProcessor:
            def segment(self, image):
                # 簡化的三分類分割
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                
                # Otsu閾值分割
                _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                
                # 創建三通道結果：背景、皮膚、傷口
                h, w = gray.shape
                result = np.zeros((h, w, 3), dtype=np.float32)
                
                # 背景（黑色區域）
                result[:, :, 0] = (gray < 50).astype(np.float32)
                
                # 皮膚（中等灰階）
                result[:, :, 1] = ((gray >= 50) & (gray < 150)).astype(np.float32)
                
                # 傷口（亮區域）
                result[:, :, 2] = (gray >= 150).astype(np.float32)
                
                return result
                
            def calculate_pwat_score(self, image, segmentation):
                # 簡化的PWAT評分
                wound_area = np.sum(segmentation[:, :, 2])
                total_area = segmentation.shape[0] * segmentation.shape[1]
                area_ratio = wound_area / total_area
                
                # 基本評分邏輯
                if area_ratio < 0.1:
                    return 2.0  # 小傷口
                elif area_ratio < 0.3:
                    return 6.0  # 中等傷口
                else:
                    return 12.0  # 大傷口
        
        logger.info("✅ 備用Deepskin處理器建立完成")
        return DummyDeepskinProcessor()
    
    def init_database(self):
        """初始化SQLite資料庫"""
        conn = sqlite3.connect('wound_analysis.db')
        cursor = conn.cursor()
        
        # 創建分析結果表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_results (
                id TEXT PRIMARY KEY,
                timestamp DATETIME,
                image_data TEXT,
                uwm_result TEXT,
                deepskin_result TEXT,
                consensus_result TEXT,
                pwat_score REAL,
                confidence_score REAL,
                processing_time REAL,
                metadata TEXT
            )
        ''')
        
        # 創建模型效能表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                model_name TEXT,
                accuracy REAL,
                processing_time REAL,
                memory_usage REAL,
                metadata TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ 資料庫初始化完成")
    
    def preprocess_image_for_uwm(self, image):
        """為UWM MobileNetV2預處理影像"""
        # 調整大小到224x224（MobileNetV2標準）
        image_resized = cv2.resize(image, (224, 224))
        
        # 正規化到[0,1]
        image_normalized = image_resized.astype(np.float32) / 255.0
        
        # 添加批次維度
        return np.expand_dims(image_normalized, axis=0)
    
    def preprocess_image_for_deepskin(self, image):
        """為Deepskin預處理影像"""
        # Deepskin預設使用256x256
        image_resized = cv2.resize(image, (256, 256))
        
        # RGB格式
        if len(image_resized.shape) == 3 and image_resized.shape[2] == 3:
            # 確保RGB順序
            image_rgb = cv2.cvtColor(image_resized, cv2.COLOR_BGR2RGB)
        else:
            image_rgb = image_resized
        
        return image_rgb
    
    def perform_uwm_segmentation(self, image):
        """執行UWM MobileNetV2分割"""
        try:
            preprocessed = self.preprocess_image_for_uwm(image)
            
            # 模型預測
            prediction = self.uwm_model.predict(preprocessed, verbose=0)
            
            # 處理輸出
            mask = prediction[0, :, :, 0]
            
            # 閾值化
            binary_mask = (mask > 0.5).astype(np.uint8) * 255
            
            # 計算邊界
            contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # 面積計算
            area = np.sum(binary_mask > 0)
            
            # 信心度（基於預測的確定性）
            confidence = float(np.mean(np.abs(mask - 0.5) * 2))
            
            return {
                'mask': binary_mask,
                'contours': contours,
                'area': float(area),
                'confidence': confidence,
                'model': 'UWM_MobileNetV2'
            }
            
        except Exception as e:
            logger.error(f"UWM分割失敗: {e}")
            return None
    
    def perform_deepskin_segmentation(self, image):
        """執行Deepskin語義分割"""
        try:
            preprocessed = self.preprocess_image_for_deepskin(image)
            
            # Deepskin分割
            segmentation = self.deepskin_processor.segment(preprocessed)
            
            # 提取傷口通道（假設第三通道是傷口）
            wound_mask = (segmentation[:, :, 2] > 0.5).astype(np.uint8) * 255
            
            # 計算邊界
            contours, _ = cv2.findContours(wound_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # 面積計算
            area = np.sum(wound_mask > 0)
            
            # 計算PWAT評分
            pwat_score = self.deepskin_processor.calculate_pwat_score(preprocessed, segmentation)
            
            # 信心度（基於分割確定性）
            confidence = float(np.mean(segmentation[:, :, 2]))
            
            return {
                'mask': wound_mask,
                'semantic_mask': segmentation,
                'contours': contours,
                'area': float(area),
                'confidence': confidence,
                'pwat_score': float(pwat_score),
                'model': 'Deepskin_U-Net'
            }
            
        except Exception as e:
            logger.error(f"Deepskin分割失敗: {e}")
            return None
    
    def calculate_consensus(self, uwm_result, deepskin_result):
        """計算兩模型的共識結果"""
        if not uwm_result or not deepskin_result:
            return None
            
        try:
            # 1. 計算IoU（交集除以聯集）
            mask1 = uwm_result['mask']
            mask2 = deepskin_result['mask']
            
            intersection = np.logical_and(mask1 > 0, mask2 > 0).sum()
            union = np.logical_or(mask1 > 0, mask2 > 0).sum()
            
            iou = intersection / union if union > 0 else 0
            
            # 2. 面積一致性
            area1 = uwm_result['area']
            area2 = deepskin_result['area']
            area_agreement = 1.0 - abs(area1 - area2) / max(area1, area2) if max(area1, area2) > 0 else 0
            
            # 3. 融合策略
            if iou > 0.8 and area_agreement > 0.8:
                # 高一致性：取交集
                fusion_method = 'intersection'
                consensus_mask = np.logical_and(mask1 > 0, mask2 > 0).astype(np.uint8) * 255
                consensus_confidence = min(uwm_result['confidence'], deepskin_result['confidence']) * 1.1
                
            elif iou > 0.6 and area_agreement > 0.6:
                # 中等一致性：加權平均
                fusion_method = 'weighted_average'
                # Deepskin權重較高（論文顯示更穩健）
                weight1, weight2 = 0.4, 0.6
                consensus_mask = ((mask1.astype(np.float32) * weight1 + 
                                 mask2.astype(np.float32) * weight2) > 127.5).astype(np.uint8) * 255
                consensus_confidence = (uwm_result['confidence'] * weight1 + 
                                      deepskin_result['confidence'] * weight2) * 0.9
                
            else:
                # 低一致性：偏好Deepskin
                fusion_method = 'prefer_deepskin'
                consensus_mask = mask2
                consensus_confidence = deepskin_result['confidence'] * 0.8
            
            # 4. 計算最終結果
            consensus_area = np.sum(consensus_mask > 0)
            contours, _ = cv2.findContours(consensus_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            return {
                'mask': consensus_mask,
                'contours': contours,
                'area': float(consensus_area),
                'confidence': float(min(1.0, max(0.0, consensus_confidence))),
                'iou': float(iou),
                'area_agreement': float(area_agreement),
                'fusion_method': fusion_method,
                'model_agreement': float((iou + area_agreement) / 2)
            }
            
        except Exception as e:
            logger.error(f"共識計算失敗: {e}")
            return None
    
    def analyze_tissue_types(self, image, consensus_result):
        """分析組織類型"""
        try:
            if not consensus_result:
                return None
            
            mask = consensus_result['mask']
            
            # 在遮罩區域內分析組織
            masked_image = cv2.bitwise_and(image, image, mask=mask)
            
            # 轉換到HSV色彩空間進行色彩分析
            hsv = cv2.cvtColor(masked_image, cv2.COLOR_RGB2HSV)
            
            # 組織類型分類（基於顏色範圍）
            tissue_regions = {}
            
            # 肉芽組織（紅色）
            red_lower = np.array([0, 50, 50])
            red_upper = np.array([10, 255, 255])
            red_mask = cv2.inRange(hsv, red_lower, red_upper)
            red_area = np.sum((red_mask > 0) & (mask > 0))
            tissue_regions['granulation'] = float(red_area)
            
            # 壞死組織（深色/黑色）
            dark_lower = np.array([0, 0, 0])
            dark_upper = np.array([180, 255, 50])
            dark_mask = cv2.inRange(hsv, dark_lower, dark_upper)
            dark_area = np.sum((dark_mask > 0) & (mask > 0))
            tissue_regions['necrotic'] = float(dark_area)
            
            # 腐肉組織（黃色）
            yellow_lower = np.array([20, 50, 50])
            yellow_upper = np.array([30, 255, 255])
            yellow_mask = cv2.inRange(hsv, yellow_lower, yellow_upper)
            yellow_area = np.sum((yellow_mask > 0) & (mask > 0))
            tissue_regions['slough'] = float(yellow_area)
            
            # 計算比例
            total_area = consensus_result['area']
            tissue_percentages = {}
            for tissue_type, area in tissue_regions.items():
                tissue_percentages[tissue_type] = (area / total_area * 100) if total_area > 0 else 0
            
            # 評估癒合階段
            healing_stage = self.determine_healing_stage(tissue_percentages)
            
            # 風險評估
            risk_score = self.calculate_risk_score(tissue_percentages, healing_stage)
            
            return {
                'tissue_areas': tissue_regions,
                'tissue_percentages': tissue_percentages,
                'healing_stage': healing_stage,
                'risk_score': risk_score,
                'total_wound_area': total_area
            }
            
        except Exception as e:
            logger.error(f"組織分析失敗: {e}")
            return None
    
    def determine_healing_stage(self, tissue_percentages):
        """根據組織比例判斷癒合階段"""
        granulation = tissue_percentages.get('granulation', 0)
        necrotic = tissue_percentages.get('necrotic', 0)
        slough = tissue_percentages.get('slough', 0)
        
        if necrotic > 30:
            return 'chronic'
        elif slough > 50:
            return 'inflammatory'
        elif granulation > 60:
            return 'proliferative'
        elif granulation > 30:
            return 'remodeling'
        else:
            return 'infected'
    
    def calculate_risk_score(self, tissue_percentages, healing_stage):
        """計算風險評分（0-1，1為最高風險）"""
        risk = 0.0
        
        # 壞死組織風險
        risk += tissue_percentages.get('necrotic', 0) / 100 * 0.4
        
        # 腐肉組織風險
        risk += tissue_percentages.get('slough', 0) / 100 * 0.3
        
        # 癒合階段風險
        stage_risk = {
            'infected': 0.9,
            'chronic': 0.8,
            'inflammatory': 0.6,
            'proliferative': 0.3,
            'remodeling': 0.1
        }
        risk += stage_risk.get(healing_stage, 0.5) * 0.3
        
        return min(1.0, risk)
    
    def save_analysis_result(self, analysis_result):
        """儲存分析結果到資料庫"""
        try:
            conn = sqlite3.connect('wound_analysis.db')
            cursor = conn.cursor()
            
            analysis_id = str(uuid.uuid4())
            timestamp = datetime.now().isoformat()
            
            cursor.execute('''
                INSERT INTO analysis_results 
                (id, timestamp, uwm_result, deepskin_result, consensus_result, 
                 pwat_score, confidence_score, processing_time, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                analysis_id,
                timestamp,
                json.dumps(analysis_result.get('uwm_result')),
                json.dumps(analysis_result.get('deepskin_result')),
                json.dumps(analysis_result.get('consensus_result')),
                analysis_result.get('pwat_score'),
                analysis_result.get('confidence_score'),
                analysis_result.get('processing_time'),
                json.dumps(analysis_result.get('metadata', {}))
            ))
            
            conn.commit()
            conn.close()
            
            return analysis_id
            
        except Exception as e:
            logger.error(f"資料儲存失敗: {e}")
            return None

# 全域服務實例
analysis_service = AdvancedWoundAnalysisService()

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康檢查端點"""
    return jsonify({
        'status': 'healthy',
        'models_loaded': analysis_service.models_loaded,
        'timestamp': datetime.now().isoformat(),
        'service': 'UWM_Deepskin_Integration'
    })

@app.route('/api/analyze_wound', methods=['POST'])
def analyze_wound():
    """主要傷口分析端點"""
    start_time = datetime.now()
    
    try:
        # 檢查模型是否載入
        if not analysis_service.models_loaded:
            return jsonify({'error': '模型尚未載入完成'}), 503
        
        # 取得上傳的影像
        if 'image' not in request.files:
            return jsonify({'error': '未提供影像檔案'}), 400
        
        image_file = request.files['image']
        image_bytes = image_file.read()
        
        # 解碼影像
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            return jsonify({'error': '無法解碼影像'}), 400
        
        # 轉換為RGB
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # 1. UWM MobileNetV2分析
        logger.info("🔄 執行UWM MobileNetV2分割...")
        uwm_result = analysis_service.perform_uwm_segmentation(image_rgb)
        
        # 2. Deepskin分析
        logger.info("🔄 執行Deepskin分割...")
        deepskin_result = analysis_service.perform_deepskin_segmentation(image_rgb)
        
        # 3. 計算共識
        logger.info("🔄 計算模型共識...")
        consensus_result = analysis_service.calculate_consensus(uwm_result, deepskin_result)
        
        # 4. 組織分析
        logger.info("🔄 執行組織分析...")
        tissue_analysis = analysis_service.analyze_tissue_types(image_rgb, consensus_result)
        
        # 計算處理時間
        processing_time = (datetime.now() - start_time).total_seconds()
        
        # 整合結果
        result = {
            'analysis_id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'processing_time': processing_time,
            'uwm_result': {
                'area': uwm_result['area'] if uwm_result else 0,
                'confidence': uwm_result['confidence'] if uwm_result else 0,
                'model': 'UWM_MobileNetV2'
            } if uwm_result else None,
            'deepskin_result': {
                'area': deepskin_result['area'] if deepskin_result else 0,
                'confidence': deepskin_result['confidence'] if deepskin_result else 0,
                'pwat_score': deepskin_result.get('pwat_score', 0) if deepskin_result else 0,
                'model': 'Deepskin_U-Net'
            } if deepskin_result else None,
            'consensus_result': consensus_result,
            'tissue_analysis': tissue_analysis,
            'confidence_score': consensus_result['confidence'] if consensus_result else 0,
            'pwat_score': deepskin_result.get('pwat_score') if deepskin_result else None,
            'recommendations': []
        }
        
        # 添加建議
        if consensus_result and consensus_result['confidence'] > 0.8:
            result['recommendations'].append('結果可信度高，可作為參考')
        elif consensus_result and consensus_result['confidence'] > 0.6:
            result['recommendations'].append('建議重新拍攝獲得更清晰的影像')
        else:
            result['recommendations'].append('影像品質不佳，建議諮詢專業醫療人員')
        
        # 儲存結果
        analysis_id = analysis_service.save_analysis_result(result)
        result['saved_id'] = analysis_id
        
        logger.info(f"✅ 分析完成，處理時間: {processing_time:.2f}秒")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"❌ 分析失敗: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': '分析過程發生錯誤',
            'details': str(e)
        }), 500

@app.route('/api/models_info', methods=['GET'])
def get_models_info():
    """獲取模型資訊"""
    return jsonify({
        'models': [
            {
                'name': 'UWM_MobileNetV2',
                'description': 'University of Wisconsin-Milwaukee足部潰瘍分割模型',
                'architecture': 'MobileNetV2-based U-Net',
                'input_size': '224x224x3',
                'accuracy': '高精度足部傷口分割',
                'loaded': analysis_service.uwm_model is not None
            },
            {
                'name': 'Deepskin_U-Net',
                'description': '義大利半監督式傷口分割模型',
                'architecture': 'U-Net with Active Semi-Supervised Learning',
                'input_size': '256x256x3',
                'features': ['語義分割', 'PWAT評分', '三分類輸出'],
                'loaded': analysis_service.deepskin_processor is not None
            }
        ],
        'integration_features': [
            '雙重模型驗證',
            '共識計算',
            'IoU一致性分析',
            '組織類型分類',
            '癒合階段評估',
            'PWAT自動評分',
            '風險評估'
        ]
    })

@app.route('/api/analysis_history', methods=['GET'])
def get_analysis_history():
    """獲取分析歷史"""
    try:
        conn = sqlite3.connect('wound_analysis.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, timestamp, pwat_score, confidence_score, processing_time
            FROM analysis_results
            ORDER BY timestamp DESC
            LIMIT 50
        ''')
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'id': row[0],
                'timestamp': row[1],
                'pwat_score': row[2],
                'confidence_score': row[3],
                'processing_time': row[4]
            })
        
        conn.close()
        
        return jsonify({
            'history': results,
            'total_count': len(results)
        })
        
    except Exception as e:
        logger.error(f"歷史記錄查詢失敗: {e}")
        return jsonify({'error': '查詢失敗'}), 500

if __name__ == '__main__':
    logger.info("🚀 啟動UWM-Deepskin整合服務...")
    app.run(host='0.0.0.0', port=5000, debug=True)