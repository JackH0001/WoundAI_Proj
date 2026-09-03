#!/usr/bin/env python3
"""
按照技術文件建議的雲端Flask架構
整合ImageJ無頭模式處理、TensorFlow模型推論、深度分析
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, decode_token,
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
# 註冊失敗的 blueprint。**這個清單存在的理由是一次真實事故。**
#
# 2026-08-19：`/api/v1/lite/segment` 部署後一直 404。註冊寫在 try/except 裡，
# 而註冊時參照了一個尚未定義的函式 → NameError → `except` 印一行日誌就過去了。
# 服務照常啟動、`/api/health` 全綠、主控台正常，**唯獨那條路不存在**。
# 從 App 端看到的症狀是「偵測不到傷口」，離根因有三層遠。
#
# 「印到 stdout」在 Cloud Run 上等於沒說——沒有人會為了確認端點在不在而去翻日誌。
# 註冊失敗必須出現在健康檢查裡，和「模型沒載到」同一個規格：
# 服務可以降級運作，但不可以假裝自己是完整的。
BLUEPRINT_FAILURES = []
# WoundLite is an anonymous, separate research-data product.  It has a
# different deletion/retention contract from the clinical WORM audit chain, so
# it must be enabled explicitly only after its own IRB/privacy review.  Keeping
# it absent by default is safer than deploying a public persistence endpoint
# because a generic Cloud Run service is otherwise unauthenticated.
LITE_API_ENABLED = os.environ.get("WOUNDAI_ENABLE_LITE_API", "0").strip().lower() in (
    "1", "true", "yes",
)

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


# ── 一次性登入碼（App →「開啟主控台」按鈕的自動登入） ─────────────────────
#
# 需求：醫師在 App 裡按一下就進到瀏覽器的主控台，不必再打一次密碼。
#
# ⚠ **最直覺的做法（把 access token 放進網址查詢字串）是不能做的。**
# Cloud Run 會把完整的請求 URL 寫進 Cloud Logging——token 就這樣以明文躺在日誌裡，
# 任何具 log viewer 權限的人都能複製它去冒用那位醫師的身分，效期還有 24 小時。
# 這條路徑安靜、可稽核性為零，是最糟的一種洩漏。
#
# 這裡的做法：
#   1. 代碼放在 **URL fragment**（`#c=...`）。fragment **不會送到伺服器**，
#      因此不進任何伺服器日誌；主控台讀到後立刻用 replaceState 清掉。
#   2. 代碼是**另一種型別的 token**（`typ=otc`），效期 60 秒，
#      而且被 token_verification_loader 擋在所有一般端點之外——
#      就算它外流，也只能拿去 /api/auth/exchange，且 60 秒後失效。
#   3. 交換行為本身進稽核，重用會被記下來。
#
# 誠實邊界：單次使用是**盡力而為**。判重的 jti 存在行程記憶體裡，Cloud Run 多實例時
# 另一個實例看不到。要做到嚴格單次得共用狀態（GCS 寫入），代價是每次登入多一次寫入
# 與延遲。以 60 秒窗口 + 不進日誌 + 型別限制來說，這個取捨是站得住的——
# 但它是取捨，不是「已經解決」。
OTC_TTL_SECONDS = 60
_otc_used = {}          # jti -> 逾期時間（epoch 秒）


def _otc_burn(jti, exp):
    """記下已用過的 jti；順手清掉過期的，免得這個 dict 無限長大。"""
    now = time.time()
    for k, v in list(_otc_used.items()):
        if v < now:
            _otc_used.pop(k, None)
    if jti in _otc_used:
        return False
    _otc_used[jti] = exp
    return True


@jwt.token_verification_loader
def _reject_otc_on_normal_endpoints(jwt_header, jwt_data):
    """一次性登入碼**不得**當成一般 access token 使用。

    沒有這一段的話，otc 就是一個貨真價實的 access token——`@jwt_required` 會直接放行，
    於是那 60 秒內它能打任何端點。加了型別檢查之後，它唯一能去的地方是
    /api/auth/exchange（那支自己解碼，不走 jwt_required）。
    """
    return jwt_data.get('typ') != 'otc'


@jwt.token_verification_failed_loader
def _otc_rejected(jwt_header, jwt_data):
    """驗證失敗的預設狀態碼是 **400**，那會讓客戶端把「憑證問題」誤判成「參數錯誤」——
    然後去檢查請求主體，而真正的問題在 Authorization 標頭。改回 401 並說清楚。"""
    return jsonify({'error': '這個憑證不能用於一般端點',
                    'issues': ['一次性登入碼只能拿去 /api/auth/exchange 換取正式 token。']}), 401

# 飛輪 HTTP 端點(/api/v1/annotation, /api/v1/consent/withdraw)
try:
    from api_flywheel import flywheel_bp
    if flywheel_bp is not None:
        app.register_blueprint(flywheel_bp)
except Exception as _fe:
    # 原本是 `except Exception: pass`——**連日誌都沒有**。
    # 飛輪端點沒掛上的話，App 送標註會 404，而後端看起來完全健康。
    BLUEPRINT_FAILURES.append(("flywheel", "%s: %s" % (type(_fe).__name__, _fe)))
    print(f"飛輪端點未載入: {_fe}")

# C0 唯讀主控台(/console)。掛在同一個 Flask，不另外部署——
# 多一個服務就多一套權限、憑證與稽核要對，而這一版只是把既有的 stats 端點畫成一頁。
try:
    from api_users import users_bp
    app.register_blueprint(users_bp)
except Exception as _ue:
    BLUEPRINT_FAILURES.append(("users", "%s: %s" % (type(_ue).__name__, _ue)))
    print(f"帳號管理端點未載入: {_ue}")

try:
    from api_console import console_bp
    app.register_blueprint(console_bp)
except Exception as _ce:
    BLUEPRINT_FAILURES.append(("console", "%s: %s" % (type(_ce).__name__, _ce)))
    print(f"主控台未載入: {_ce}")

# ── WoundLite 民眾版的**匿名**端點 ────────────────────────────────────
#
# ⚠ 這是整個服務唯一不需要登入的資料端點。分割函式用注入而不是讓 api_lite
# 反向 import 本模組：一來避免循環匯入，二來讓它在沒有 ONNX 模型的環境下
# 也 import 得起來（契約測試因此不必載入模型就跑得動）。
# ⚠ 民眾版端點**不在這裡註冊**，它搬到 `segment_for_lite` 定義之後（見該函式下方）。
#
# 為什麼：Python 的頂層是逐行執行的。原本寫在這裡的 `init_lite(segment_for_lite)`
# 在第 167 行執行，而那個函式定義在第 1219 行——必然 NameError。
# 而 `except Exception: print(...)` 把它吞成一行日誌，服務照常啟動：
# 健康檢查全綠、主控台正常、**唯獨那條路 404**。
#
# 這個 bug 從端點加進來的第一天就存在（第一版注入的 `segment_wound_ai`
# 定義在 1073 行，同樣在後面），兩輪都沒被發現，因為沒有任何東西會報錯。

# ── 帳號與角色（RBAC S1）────────────────────────────────────────────
#
# 帳號改由 `auth_users` 管理（存在儲存層、PBKDF2 雜湊、可停用、帶角色）。
# 環境變數只保留 **bootstrap** 用途：全新部署時沒有任何帳號，而帳號管理端點
# 本身需要 admin 才能用——沒有這個出口就是雞生蛋。
# 一旦帳號檔裡有任何帳號，環境變數就完全不再生效（否則它會變成永久後門）。
try:
    import auth_users
    _boot = auth_users.bootstrap_from_env()
    if _boot:
        print("已由環境變數建立初始管理者 default:admin（後續請用帳號管理端點新增使用者）")
except Exception as _ae:
    auth_users = None
    print(f"⚠ 帳號模組載入失敗，所有登入都會失敗: {_ae}")

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
    """取得 JWT。body: {username, password[, org]}。username 可用 `org:user` 或純 user。"""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '')
    password = data.get('password', '')

    # 型別防禦：客戶端把 password 送成陣列/數字時，原本會在 .encode() 直接 AttributeError
    # → 500 Internal Server Error，而那看起來像後端壞了。實際發生過:PowerShell 把多行的
    # `gcloud secrets versions access` 輸出當成 string[]，ConvertTo-Json 就序列化成 JSON 陣列。
    # 格式錯誤是客戶端的問題，要回 400 並說清楚，不是 500。
    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify({'error': 'username/password 必須是字串',
                        'hint': '收到的型別為 %s / %s' % (type(username).__name__,
                                                        type(password).__name__)}), 400
    if auth_users is None:
        return jsonify({'error': '帳號模組不可用'}), 503

    # 一律 strip：幾乎所有密鑰佈署方式都會在值尾端留下換行，而症狀是
    # 「密碼看起來完全正確卻登不進去」，因為肉眼看不到那個 \n。
    username, password = username.strip(), password.strip()
    if ':' in username:
        org, user = username.split(':', 1)
    else:
        org, user = (data.get('org') or auth_users.DEFAULT_ORG).strip(), username

    rec, why = auth_users.authenticate(org, user, password)
    ident = auth_users.identity(org, user)
    if rec is None:
        # ⚠ 對外只給一種訊息。分開回「帳號不存在」與「密碼錯誤」等於送人一份帳號列舉工具。
        # 真正的原因記在稽核裡，供事後分析與偵測暴力嘗試。
        try:
            import api_flywheel as _fw
            _fw.audit(ident, 'login_failed', '-', why)
        except Exception:
            pass
        logger.warning(f"登入失敗 {ident}: {why}")
        return jsonify({'error': '帳號或密碼錯誤'}), 401

    token = create_access_token(
        identity=ident,
        # role 進 JWT：端點據此授權。org 一併帶著，S2 加機構隔離時不必改 token 格式。
        additional_claims={'role': rec['role'], 'org': org, 'user': user}
    )
    try:
        import api_flywheel as _fw
        _fw.audit(ident, 'login', '-', f"role={rec['role']}")
    except Exception:
        pass
    return jsonify({
        'access_token': token,
        'role': rec['role'],
        'role_zh': auth_users.ROLES.get(rec['role'], rec['role']),
        'org': org, 'user': user, 'identity': ident,
        'display_name': rec.get('display_name'),
        # App 依此決定畫面呈現。**這只是輔助**——真正的閘門在每個端點的伺服器端檢查。
        'perms': sorted([k for k in auth_users.PERMS if auth_users.can(rec['role'], k)]),
    }), 200


@app.route('/api/v1/auth/onetime', methods=['POST'])
@jwt_required()
def issue_onetime_code():
    """發一個 60 秒、單次使用的登入碼，給 App 開啟主控台用。

    要先有有效的 access token 才拿得到——這不是另一條認證途徑，
    只是把「已經登入的身分」安全地遞給同一台裝置上的瀏覽器。
    """
    c = get_jwt() or {}
    ident = get_jwt_identity() or 'unknown'
    code = create_access_token(
        identity=ident,
        additional_claims={'role': c.get('role'), 'org': c.get('org'),
                           'user': c.get('user'), 'typ': 'otc'},
        expires_delta=timedelta(seconds=OTC_TTL_SECONDS))
    try:
        import api_flywheel as _fw
        _fw.audit(ident, 'otc_issued', '-', f"效期 {OTC_TTL_SECONDS}s",
                  c.get('role'), c.get('org'))
    except Exception:
        pass
    return jsonify({'code': code, 'expires_in': OTC_TTL_SECONDS}), 200


@app.route('/api/auth/exchange', methods=['POST'])
def exchange_onetime_code():
    """用一次性登入碼換一個正常的 access token。**刻意不掛 @jwt_required**——
    掛了就會被上面的 token_verification_loader 擋掉（otc 不是合法的 access token）。
    """
    d = request.get_json(silent=True) or {}
    code = d.get('code')
    if not isinstance(code, str) or not code.strip():
        return jsonify({'error': '缺少登入碼'}), 400
    try:
        data = decode_token(code.strip())
    except Exception as e:
        # 過期與偽造回同一句：分開講等於告訴攻擊者「這個簽章是對的，只是過期了」。
        logger.warning('otc 解碼失敗: %s', e)
        return jsonify({'error': '登入碼無效或已過期'}), 401
    if data.get('typ') != 'otc':
        return jsonify({'error': '登入碼無效或已過期'}), 401

    ident = data.get('sub') or 'unknown'
    role, org, user = data.get('role'), data.get('org'), data.get('user')
    if not _otc_burn(data.get('jti'), data.get('exp', 0)):
        # 重用要留痕：這可能是使用者按了兩次，也可能是代碼被攔截後重放。
        try:
            import api_flywheel as _fw
            _fw.audit(ident, 'otc_reused', '-', '同一個登入碼被重複交換（已拒絕）', role, org)
        except Exception:
            pass
        return jsonify({'error': '登入碼已使用過'}), 401

    rec = auth_users.get_user(org, user) if (org and user) else None
    # 發碼到交換之間帳號可能已被停用（管理者剛按下停用）。60 秒也是時間。
    if rec is None or rec.get('disabled'):
        return jsonify({'error': '帳號已停用或不存在'}), 401

    token = create_access_token(identity=ident,
                                additional_claims={'role': rec['role'], 'org': org, 'user': user})
    try:
        import api_flywheel as _fw
        _fw.audit(ident, 'login_otc', '-', f"role={rec['role']}（一次性登入碼）", rec['role'], org)
    except Exception:
        pass
    return jsonify({
        'access_token': token, 'role': rec['role'],
        'role_zh': auth_users.ROLES.get(rec['role'], rec['role']),
        'org': org, 'user': user, 'identity': ident,
        'display_name': rec.get('display_name'),
        'perms': sorted([k for k in auth_users.PERMS if auth_users.can(rec['role'], k)]),
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康檢查端點。

    ⚠ **降級模式必須在這裡現形。**
    先前這支只回 `status: healthy`，而「模型載不進來、退回 HSV 色彩法」只寫在啟動日誌裡。
    結果是雲端部署漏裝 onnxruntime 時，健康檢查一路綠燈、API 照常回 200 並給出面積——
    只是那個數字來自完全不同的演算法。**會回答的錯誤比不會回答的錯誤危險得多**，
    因為沒有人會去查一個「看起來正常」的服務。

    所以：模型沒載到就回 `status: degraded`，並明說影響。監控與主控台都看得到。
    """
    model_ready = wound_segmentation_model is not None
    # 色準校正模組。缺了 classify **不會壞**（呼叫端有 try/except），
    # 而是安靜退回 gray-world 白平衡：紅色被壓抑 ×0.78、肉芽被低估，
    # 服務照回 200、數字看起來合理。這正是本專案定義的「危險失敗」，
    # 與漏裝 onnxruntime 同一類，所以同樣要讓它在健康檢查裡現形。
    try:
        _load_classify_mods()          # 確保 vendor/ 已進 sys.path
        import color_calib as _cc_probe          # noqa: F401
        colorcal_ready = True
    except Exception:
        colorcal_ready = False
    # classify 端點還需要 engineering 的組織分類與 PUSH 模組。它們在容器裡是
    # 部署時複製的 vendor/ 副本——漏了複製的話，**只有 classify 會 503**，
    # 而健康檢查依舊全綠（實際發生過：登入 200、stats 200、classify 503）。
    # 健康檢查要涵蓋「主要功能真的能用」，不只是「行程還活著」。
    classify_ready = _load_classify_mods() is not None
    # blueprint 沒掛上＝**整條路不存在**（404），而服務其餘部分完全正常。
    # 這比模型沒載到更難察覺：模型缺席至少 classify 會回錯，
    # 端點缺席則是「App 說偵測不到傷口」，離根因三層遠。
    bp_ok = not BLUEPRINT_FAILURES
    # The health gate must compare the *measured bytes* with the reviewed
    # fixture, not merely compare two version strings generated by the same
    # runtime.  A wheel/base-image drift can preserve the version text while
    # changing JPEG output.
    canonicalization_version = None
    canonicalization_golden = {"sha256": None}
    canonicalization_golden_ok = False
    canonicalization_golden_error = None
    try:
        from image_canonical import CANONICALIZATION_VERSION as _canon_version
        from runtime_golden import compute as _compute_canonicalization_golden, is_expected as _golden_expected
        canonicalization_version = _canon_version
        canonicalization_golden = _compute_canonicalization_golden()
        canonicalization_golden_ok = _golden_expected(canonicalization_golden)
    except Exception as _e:
        canonicalization_golden_error = type(_e).__name__

    degraded = ((not model_ready) or (not classify_ready)
                or (not colorcal_ready) or (not bp_ok)
                or (not canonicalization_golden_ok))
    status = {
        'status': 'degraded' if degraded else 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'imagej': IMAGEJ_AVAILABLE and imagej_instance is not None,
            'onnxruntime': ONNX_AVAILABLE,
            'tensorflow': TENSORFLOW_AVAILABLE,
            'segmentation_model': model_ready,
            'classify_modules': classify_ready,
            'color_calibration': colorcal_ready,
            'endpoints_registered': bp_ok,
            'canonicalization_golden': canonicalization_golden_ok,
            'lite_public_api_enabled': LITE_API_ENABLED,
            'database': True
        },
        # 哪一個沒掛上、以及原始例外。沒有這個，看到 endpoints_registered=false
        # 也還是得去翻容器日誌。
        'blueprint_failures': [{'name': n, 'error': e} for n, e in BLUEPRINT_FAILURES],
        'store': None,
        'version': '1.0.0',
        # ── 「跑的是不是我推的那份程式碼」──────────────────────────────
        #
        # 原本這裡只有寫死的 '1.0.0'，回答不了任何問題。而這個專案已經被
        # 「看起來成功的部署」咬過兩次（漏裝 onnxruntime 靜默降級、
        # gcloud 的 status.urls[] 欄位不存在導致 URL 探測靜默失效），
        # 兩次都是因為沒有東西能把「部署的動作」與「實際在跑的東西」對起來。
        #
        # K_REVISION / K_SERVICE 由 Cloud Run 自動注入，不必自己維護。
        # GIT_COMMIT 由部署腳本帶進來——沒有它就只知道「換了一版」，
        # 不知道是哪一版，而那在回推問題時等於沒有。
        'build': {
            'service': os.environ.get('K_SERVICE'),
            'revision': os.environ.get('K_REVISION'),
            'git_commit': os.environ.get('GIT_COMMIT'),
            'deployed_at': os.environ.get('DEPLOYED_AT'),
            # 本地跑時上面全是 None，這個旗標讓主控台不會把本機誤標成雲端。
            'on_cloud_run': bool(os.environ.get('K_REVISION')),
        },
    }
    if degraded:
        reasons = []
        for _bn, _be in BLUEPRINT_FAILURES:
            reasons.append(
                f'`{_bn}` 端點**未註冊**，該路徑一律 404，而服務其餘部分正常。'
                f'原始例外：{_be}')
        if not model_ready:
            reasons.append(
                '無可用的 ML 分割模型，已退回 HSV 色彩法。面積與組織判讀不具臨床參考價值。'
                + ('（onnxruntime 未安裝——請確認 requirements.txt）' if not ONNX_AVAILABLE
                   else '（onnxruntime 可用但模型未載入——請確認 models/ 內有 .onnx）'))
        if not classify_ready:
            reasons.append(
                'classify 所需的 engineering 模組載不進來（wound_classifier / clinical_rules '
                '/ aruco_calibrate），/api/v1/classify 會回 503。'
                '容器內請確認部署時已把它們複製到 vendor/。')
        if not colorcal_ready:
            reasons.append(
                'color_calib 載不進來，組織分類的白平衡會退回 gray-world。'
                '實測紅色增益被壓到正確值的 ×0.78（傷口佔畫面越大越嚴重），'
                '肉芽會被低估並落入「其他」。classify 仍回 200，**數字看起來合理**。'
                '部署時請確認 vendor/color_calib.py 存在。')
        if not canonicalization_golden_ok:
            reasons.append(
                'canonicalization golden 驗證失敗；目前映像產生的 canonical bytes '
                '未能對上已覆核的 fixture。'
                + (f'原始例外：{canonicalization_golden_error}'
                   if canonicalization_golden_error else ''))
        status['degraded_reason'] = ' ｜ '.join(reasons)
    # 儲存後端也一併回報：WOUNDAI_STORE 沒設成 gcs 時，Cloud Run 的資料會隨實例回收消失，
    # 而那同樣是「一切看起來正常，直到某天統計歸零」。
    try:
        import api_flywheel as _fw
        status['store'] = _fw._store().describe()
        status['audit_retention'] = _fw._store().retention_info()
    except Exception as _e:
        status['store'] = 'unavailable: %s' % _e

    from consent_staging import care_key_status
    status['canonicalization_version'] = canonicalization_version
    status['canonicalization_golden_sha256'] = canonicalization_golden['sha256']
    status['canonicalization_golden_ok'] = canonicalization_golden_ok
    if canonicalization_golden_error:
        status['canonicalization_golden_error'] = canonicalization_golden_error
    status['care_receipt'] = care_key_status()
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
    claims = get_jwt() or {}
    role, org = claims.get('role'), claims.get('org')
    actor = get_jwt_identity() or 'unknown'
    # This endpoint persists clinical measurements in SQLite.  JWT possession
    # alone is not a clinical authorization boundary: a patient/support token
    # must not be able to create a record simply because it can request an
    # in-memory analysis.
    if auth_users is None or not auth_users.can(role, 'measure.clinical'):
        return jsonify({'error': '權限不足，需要臨床量測權限'}), 403
    
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
        
        # 6. 記錄分析結果。SQLite is not part of the training flywheel, but
        # it is persistent clinical measurement state.  Its audit intent must
        # be durable before the database transaction; an outcome-audit failure
        # is reported as an explicit unknown state rather than a false 200.
        processing_time = int((time.time() - start_time) * 1000)
        try:
            import api_flywheel as _fw
            _fw.audit_intent(actor, 'analysis_record', image_hash, role, org,
                             {'image_hash': image_hash, 'processing_time_ms': processing_time})
            save_analysis_record(session_id, image_hash, analysis_result, processing_time)
            _fw.audit(actor, 'analysis_recorded', image_hash,
                      'processing_time_ms=%d' % processing_time, role, org)
        except Exception as record_exc:
            logger.exception('分析結果持久化／稽核失敗: %s', record_exc)
            return jsonify({
                'success': False,
                'error': 'analysis_record_persistence_unknown',
                'session_id': session_id,
                'timestamp': datetime.now().isoformat(),
            }), 503
        
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
    """**已退役**（2026-08-21）。舊實作已完整刪除，不是註解掉、也不是改名保留。

    這是飛輪之前的舊訓練資料入口。它只要求登入，沒有醫師資格、沒有
    annotation.submit、沒有去識別化驗證、沒有 consent_train、沒有 image_id
    綁定——/api/v1/annotation 上每一道守門，這條路徑都繞過。
    全 repo 零呼叫端（2026-08-21 實查）。

    **為什麼不把舊實作改名留著**：留一個可呼叫的繞過路徑在 production source
    裡，只要有人把 handler 改成 `return _legacy()` 就整條復活，而靜態測試若只看
    handler 直接呼叫了什麼就抓不到。稽核需求由 git history 負責，那才是稽核紀錄
    該待的地方。

    保留路由回 410（而非刪掉路由）是為了讓殘存呼叫端收到明確訊息，
    而不是一個看起來像部署出錯的 404。
    """
    return jsonify({
        'error': 'endpoint retired',
        'message': '/api/train 已退役。請改用 POST /api/v1/annotation（需醫師角色、'
                   'image_id 綁定、去識別化與訓練同意驗證）。',
        'replacement': '/api/v1/annotation',
        'retired_at': '2026-08-21',
    }), 410


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

def student_threshold():
    """student 機率圖轉二值遮罩的門檻。**唯一來源。**

    `segment_wound_ai` 回的是機率圖，不是遮罩。要用 SSOT 裡該模型的 threshold 去切。

    ## 為什麼要一個函式，而不是「兩邊都寫同一個運算式」

    2026-08-19 的教訓有兩層。第一層是我在 `segment_for_lite` 裡漏了門檻——
    `mask > 0` 會把整張圖當成傷口，**而且不會有任何錯誤**。
    第二層是即使補上了，那個運算式就有了兩份拷貝：

        thr = float(((_load_ssot().get("models", {}) or {})
                     .get(_active_model_key() or "", {}) or {}).get("threshold", 0.4))

    兩份拷貝之後，只要有人改其中一邊，同一張照片在醫療版與民眾版就會得到
    不同的遮罩——而兩邊各自都「有套用 SSOT 門檻」，靜態檢查看不出來，
    使用者只會發現兩個 App 對同一張傷口說了不同的話。

    測「兩份拷貝有沒有同步」是治標。**讓拷貝不存在才是治本。**
    """
    cfg = (_load_ssot().get("models", {}) or {}).get(_active_model_key() or "", {}) or {}
    return float(cfg.get("threshold", 0.4))


def escalate_mask(img_rgb, mask, W, H, policy="always"):
    """難例升級：student 的遮罩不夠好時，改用雲端 A∪U 集成。

    回 `(mask, info)`。`info` 含 route / escalated / seg_model /
    iou_student_au / au_area_ratio；沒有升級時這些鍵可能缺席。

    ## 為什麼抽成共用函式

    2026-08-19：`/api/v1/lite/segment` 對同一批印刷樣例**一律回空**，而醫療版
    認得出來——因為 Lite 只接了 student 一顆，沒有這段升級鏈。
    印刷翻拍正是 student 最弱的 domain shift，也正是集成救得回來的那種難例。

    兩條路徑吃同一組判準，才不會再出現「醫療版看得到、民眾版看不到」而
    兩邊的程式碼各自都沒有 bug 的情況。

    ## `policy` 為什麼要分兩種

    · `"always"`（醫療版）：**每次都跑集成**取第二意見。student 漏掉一個區域時
      自己不會有任何訊號，所以要靠另一組模型比對才判斷得出「這次不可靠」。
      代價是每一次請求都多跑兩顆模型。臨床端是登入使用者、量可控，付得起。

    · `"on_weak"`（民眾版）：**只在 student 空手或極小時**才跑集成。
      理由不是省錢那麼簡單——`lite/segment` 是匿名端點，
      「每次都跑集成」等於把每個請求的運算成本乘三，而任何人都打得到它。
      刻意送難例就能放大成本，這是限流擋不住的那一類濫用。

      取捨是明確的：民眾版會漏掉「student 有輸出但低估」的那種難例
      （只救得回「完全空手」的）。這是**已知的降級**，不是疏忽。
    """
    info = {}
    try:
        # 弱門檻的判斷放在**載入集成之前**。第一版順序相反——on_weak 提早返回時
        # 兩顆模型已經載進來了（有快取所以第二次起不貴，但首次載入是實際成本，
        # 而且「不會用到的東西不該先載」這個順序錯了，日後在這前面加任何昂貴
        # 操作都會被靜默執行）。測試改數 _au_infer 的呼叫次數時抓到的。
        if policy == "on_weak":
            # 「夠弱才升級」的門檻。
            #
            # ⚠ 第一版設 0.001（千分之一畫面），實測第一晚就漏掉一個活樣本：
            # `0c8dab2b`（2026-08-20 00:15）——student 吐出一個 0.76 cm² 的小遮罩，
            # 佔畫面約千分之三，**過了門檻所以沒升級**，而那其實是集成救得回來的
            # 難例。「有一點輸出」和「輸出可信」是兩回事。
            #
            # 改成 1%：民眾版的拍攝指引是傷口部位特寫，傷口正常會佔畫面數個百分點；
            # 小於 1% 的遮罩要嘛是真的極小傷口（遠拍，違反指引）、要嘛是 student
            # 低估——兩種都值得再問一次集成。成本上界仍然有效：升級只在弱輸出時
            # 觸發，正常照片（遮罩 >1%）不會多跑模型。
            # 可用 LITE_WEAK_FRAC 調整；回歸樣本＝0c8dab2b（應升級）與正常特寫（不應）。
            weak_frac = float(os.environ.get("LITE_WEAK_FRAC", "0.01"))
            frac = float(np.asarray(mask, bool).sum()) / max(1, mask.size)
            if frac >= weak_frac:
                return mask, info
            info["weak_reason"] = "empty" if frac == 0.0 else ("tiny_mask %.4f" % frac)
        _a, _u = _load_cloud_au()
        if _a is None or _u is None:
            return mask, info
        _fused = 0.5 * _au_infer(_a, img_rgb) + 0.5 * _au_infer(_u, img_rgb)
        au_mask = cv2.resize(_fused, (W, H)) > 0.40

        def _big(m):
            cs, _h = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL,
                                      cv2.CHAIN_APPROX_SIMPLE)
            return max((cv2.contourArea(c) for c in cs), default=0.0)

        _sp, _ap = _big(mask), _big(au_mask)
        _inter = float(np.logical_and(mask, au_mask).sum())
        _uni = float(np.logical_or(mask, au_mask).sum())
        info["iou_student_au"] = round(_inter / _uni, 3) if _uni > 0 else 1.0
        info["au_area_ratio"] = round(_ap / _sp, 2) if _sp > 0 else (999.0 if _ap > 0 else 0.0)
        if _ap > 0 and (info["au_area_ratio"] > 1.5 or info["iou_student_au"] < 0.5):
            info.update({"route": "cloud_escalated(AU)", "escalated": True,
                         "seg_model": "ensemble.AU"})
            return au_mask, info
    except Exception as _e:
        logger.warning(f"escalate 略過: {_e}")
    return mask, info


def segment_for_lite(image_rgb):
    """民眾版用的分割：student ＋ 只在空手時觸發的集成。

    回 `(mask, info)`。`api_lite` 依此回報 route，讓「這張是靠集成救回來的」
    在民眾版也看得見——不然同一張照片在兩個 App 得到不同結果時，無從歸因。

    ## ⚠ 兩件第一版都寫錯的事

    **一、`segment_wound_ai` 回的是 `(wound_prob, confidence)` 兩個值，不是遮罩。**
    第一版寫成 `mask = segment_wound_ai(...)`，於是 mask 是一個 tuple。
    `if mask is None` 當然不成立，後面 `np.asarray(tuple, bool)` 一路炸到
    `_polygons_from_mask()`——那裡沒有 try，所以回的是 Flask 預設的 HTML 500，
    連 JSON 錯誤都沒有。classify 的兩處呼叫（第 927、1725 行）都有正確解包，
    只有這裡沒有。

    **二、它回的是機率圖，不是二值遮罩。** 要用 SSOT 裡該模型的 threshold 去切
    （classify 是這樣做的）。少了這一步，就算 tuple 的問題修掉，
    `mask > 0` 也會把整張圖當成傷口——**而且不會有任何錯誤**，
    只會回一個荒謬的輪廓。兩條路徑必須用同一個門檻，否則同一張照片
    在醫療版與民眾版會得到不同答案，而那種差異最難歸因。
    """
    import numpy as _np
    out = segment_wound_ai(image_rgb)
    wound_prob = out[0] if isinstance(out, tuple) else out
    if wound_prob is None:
        mask = _np.zeros(image_rgb.shape[:2], bool)
    else:
        mask = _np.asarray(wound_prob) > student_threshold()
    h, w = image_rgb.shape[:2]
    mask, info = escalate_mask(image_rgb, mask, w, h, policy="on_weak")
    info.setdefault("route", "student")
    info.setdefault("escalated", False)
    return mask, info


# ── WoundLite 民眾版的**匿名**端點（註冊點刻意放在這裡）─────────────────
#
# 這是整個服務唯一不需要登入的資料端點。
#
# ⚠ 位置是刻意的，不是隨手擺的：它必須在 `segment_for_lite` **定義之後**。
# 放在檔案上方那一區（其他 blueprint 旁邊）會 NameError，而那個錯誤會被
# `except` 吞成一行日誌——服務照常啟動、健康檢查全綠、端點 404。
# 用「Python 自己會擋」取代「記得順序」：搬到這裡之後，順序錯了就是啟動失敗。
#
# 分割函式用注入而不是讓 api_lite 反向 import 本模組：避免循環匯入，
# 也讓它在沒有 ONNX 模型的環境下 import 得起來（契約測試因此不必載入模型）。
if LITE_API_ENABLED:
    try:
        from api_lite import lite_bp as _lite_bp, init_lite as _init_lite
        _init_lite(segment_for_lite)
        app.register_blueprint(_lite_bp)
    except Exception as _le:
        # 仍然不讓它擋住服務啟動（其他端點該照常運作），但**要留下痕跡**，
        # 而且那個痕跡必須出現在 /api/health——只印到 stdout 等於沒有人會看到。
        BLUEPRINT_FAILURES.append(("lite", "%s: %s" % (type(_le).__name__, _le)))
        print(f"民眾版端點未載入: {_le}")
else:
    print("WoundLite 匿名端點未啟用（WOUNDAI_ENABLE_LITE_API 必須明確設為 1）。")


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
        
    except Exception:
        # The caller must decide how to report a record whose transaction may
        # have committed just before an I/O failure.  Swallowing this exception
        # would make the API claim a fully persisted clinical measurement.
        logger.exception("保存分析記錄失敗")
        raise

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
# 容器內的 engineering 模組副本。
#
# ⚠ Docker 的建置上下文只有 `Backend/Flask/`，`../../engineering` 在映像裡**不存在**。
# 本機開發時 app.py 從 repo 相對路徑載得到，所以這個問題在本機完全看不出來——
# 直到部署上雲，classify 端點才以 `No module named 'wound_classifier'` 回 503。
# 部署腳本會在建置前把需要的模組複製到 `vendor/`（見 deploy_cloudrun.ps1）。
#
# 搜尋順序刻意是「先 repo、後 vendor」：本機開發要用**正在編輯的那一份**，
# 而不是某次部署留下的舊副本。vendor/ 因此也不進版控——同一份程式碼放兩個地方，
# 遲早會有人只改了其中一個。
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _classify_search_paths():
    return [os.path.join(_ENG, s) for s in ("phase2", "phase1")] + [_VENDOR]


def _load_classify_mods():
    """延遲載入 engineering 的 wound_classifier(v2)/clinical_rules(PUSH)/aruco;失敗回 None。"""
    import sys
    for pth in _classify_search_paths():
        if os.path.isdir(pth) and pth not in sys.path:
            sys.path.insert(0, pth)
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


def _load_seg_red():
    """印刷模擬圖專用的**決定性色彩分割**(HSV 紅域 + 最大連通)。

    為什麼不讓模型學印刷色塊:①那是分布外樣本,硬訓會污染臨床分布;
    ②量測鏈驗證本來就該用決定性方法——用 AI 去驗尺度鏈等於兩個未知數解一個方程式。
    本函式即 verify_area_sheet.seg_red,已在同批驗證單實拍 n=15 上達 **平均|誤差| 1.9%**
    (EVIDENCE_LEDGER 2026-07-20),遠優於模型且不會隨訓練漂移。"""
    import sys
    # 與 _load_classify_mods 用同一組搜尋路徑：容器裡沒有 ../../engineering，
    # 只有部署時複製過去的 vendor/。少寫這一行的話，模擬圖路由會單獨壞掉。
    for pth in _classify_search_paths():
        if os.path.isdir(pth) and pth not in sys.path:
            sys.path.insert(0, pth)
    try:
        from verify_area_sheet import seg_red_robust
        return seg_red_robust
    except Exception as e:
        logger.warning(f"seg_red 載入失敗: {e}")
        return None


@app.route('/api/v1/classify', methods=['POST'])
@jwt_required()
def classify_wound():
    """分割→(ArUco/手動)校正面積→組織v2→PUSH 嚴重度。回傳標準階段結果。
    body(multipart): image=<jpg/png>; 選配 cm_per_pixel=<float>(無 ArUco 時手動校正)。"""
    from image_canonical import canonicalize, InvalidImage, CANONICALIZATION_VERSION
    from consent_staging import ConsentError
    mods = _load_classify_mods()
    if mods is None:
        return jsonify({'error': '分類模組不可用(engineering 模組缺)', 'stage': 'init'}), 503
    tissue_proxy_v2, push_score, _ac = mods
    if 'image' not in request.files:
        return jsonify({'error': '缺少 image'}), 400
    try:
        canonical = canonicalize(request.files['image'].read())
        bgr = canonical.pixels  # exactly decode(stored canonical), including orientation
        persistence = {'persisted': False, 'persistence_reason': 'care_receipt_required', 'image_id': None}
        try:
            import api_flywheel as _fw
            actor, role, org = _fw._who()
            try:
                persistence = _fw.promotion_service(actor, role, org).stage(
                    canonical, request.form.get('care_receipt'), actor, role, org)
            except _fw.AuditUnavailable:
                # A valid care receipt would otherwise permit a state-changing
                # staging write.  Do not disguise an unavailable immutable
                # audit intent as an analysis-only success.
                return jsonify({'error': 'audit_unavailable', 'stage': 'persistence'}), 503
        except ConsentError as exc:
            if exc.status >= 500:
                return jsonify({'error': exc.code, 'stage': 'persistence'}), exc.status
            persistence['persistence_reason'] = exc.code
        except Exception:
            logger.warning("staging unavailable; analysis only, no image id")
            persistence['persistence_reason'] = 'staging_unavailable'
        image_id = persistence['image_id']
        image_reused = persistence.get('image_reused', False)
        img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        H, W = img.shape[:2]
        # seg=color:印刷模擬圖走決定性色彩分割,**完全不碰模型**
        # (模型權重/訓練集/golden 釘值一律不變 → 對臨床效能零風險)
        phantom = str(request.form.get('seg', 'auto')).lower() == 'color'
        seg_model = _active_model_key(); route = "student"; escalated = False; au_ratio = None; iou_sa = None
        phantom_pass = None; phantom_hint = False
        if phantom:
            _sr = _load_seg_red()
            if _sr is None:
                return jsonify({'error': '色彩分割模組不可用(verify_area_sheet 缺)', 'stage': 'init'}), 503
            _m, _c, phantom_pass = _sr(img)
            mask = _m.astype(bool); conf = 1.0 if mask.any() else 0.0
            seg_model = f"color_hsv(phantom,{phantom_pass})"; route = "phantom_color(非AI)"
        else:
            # Stage2 分割(端上主力 student)
            wound_prob, conf = segment_wound_ai(img)
            # 門檻只有一個來源（`student_threshold()`）。先前這裡與 segment_for_lite
            # 各寫一份同樣的運算式——兩份拷貝一旦分岔，同一張照片在醫療版與
            # 民眾版會得到不同遮罩，而兩邊看起來都「有套用 SSOT 門檻」。
            thr = student_threshold()
            mask = wound_prob > thr
            # AI 回空遮罩時,順手用色彩分割探一下:若找得到形狀合理的飽和紅色塊,
            # 幾乎可以確定使用者把「印刷模擬圖」誤選成了臨床/範例(選錯來源就會走 AI,而 AI 對
            # 印刷色塊必定空手)。回一個 hint 讓 App 直接提示改選,不要只丟「AI 未偵測到傷口」
            # 讓人以為是模型爛掉。色彩分割是微秒級,不會拖慢回應。
            if not mask.any():
                try:
                    _sr = _load_seg_red()
                    if _sr is not None:
                        _pm, _pc, _pp = _sr(img)
                        _frac = float((_pm > 0).sum()) / _pm.size
                        if _pc is not None and 0.001 < _frac < 0.5:
                            phantom_hint = True
                            logger.info(f"AI 空遮罩但色彩分割找到 {_frac:.3f} 面積 → 疑似選錯來源(應為模擬圖)")
                except Exception as _pe:
                    logger.warning(f"phantom hint 探測略過: {_pe}")
        # 雙軌自動 escalate:難例(碎片/低對比→student 大幅低估)自動改用雲端 A∪U 集成
        # 判難靠「第二意見」(student vs A∪U),因 student 漏 segment 區域機率≈0、無自我訊號
        # phantom 走色彩分割,沒有「第二意見」可言,直接跳過
        if not phantom and str(request.form.get('escalate', 'on')).lower() not in ('off', '0', 'false'):
            mask, _esc = escalate_mask(img, mask, W, H, policy="always")
            route = _esc.get("route") or route
            escalated = _esc.get("escalated", escalated)
            seg_model = _esc.get("seg_model") or seg_model
            iou_sa = _esc.get("iou_student_au", iou_sa)
            au_ratio = _esc.get("au_area_ratio", au_ratio)

        # ⚠ phantom_hint 必須在**最終遮罩定案後**重新評估。
        #
        # 它是在 student 跑完、escalate 之前算的：student 回空遮罩 → hint=True。
        # 但緊接著 A∪U 集成常常救得回來（實測 route=cloud_escalated(AU)、面積 12.05 cm²、
        # 信心 100%），此時畫面卻仍顯示「AI 沒偵測到傷口，這看起來是印刷模擬圖」。
        #
        # 這不只是訊息不準——它會把醫師導向**錯誤的操作**：提示叫他改選「模擬圖」，
        # 而模擬圖走的是 HSV 色彩分割。拿色彩分割去量真實傷口照，得到的是紅色區域而非傷口，
        # 面積會錯得離譜卻照樣回 200。誤導性的提示比沒有提示更危險。
        if phantom_hint and mask.any():
            logger.info("最終遮罩非空(route=%s) → 撤銷 phantom_hint(集成已救回,不是模擬圖)", route)
            phantom_hint = False

        # Stage3 校正面積:優先 ArUco,否則 cm_per_pixel(手動)
        area_cm2 = None; calib = "none"; mm_per_px = None
        marker_quad = None; marker_id = None; marker_mm = None
        if _ac is not None:
            det = _ac.detect_marker(img)
            if det is not None:
                _mm = float((_load_ssot().get("calibration", {}) or {}).get("marker_mm_active", 12.0))
                area_cm2 = float(_ac.measure_area_cm2_ratio(mask.astype(np.uint8), det[0], marker_mm=_mm)); calib = f"aruco(marker {_mm}mm)"
                # 尺度直傳:App 修邊面積=像素數×(mm/px)²,不依賴 AI 初始面積(消換算鏈偏差)
                _c = np.array(det[0]).reshape(-1, 2)
                _side = float(np.mean([np.linalg.norm(_c[_i] - _c[(_i + 1) % 4]) for _i in range(4)]))
                if _side > 0: mm_per_px = _mm / _side
                # ⚠ **把角點回傳給 App 目視複核。**
                #
                # ArUco 偵測本身沒有「認錯了」這個錯誤狀態——它要嘛回一個四邊形，要嘛回 None。
                # 若它把反光、地磚接縫或別處的印刷圖案認成標記，mm_per_px 就是錯的，
                # 而**每一筆面積都會安靜地錯**：服務照回 200、畫面照顯示一個合理的數字，
                # 沒有任何跡象。這是本專案最危險的失敗形狀。
                #
                # 唯一實際可行的防線是讓人看一眼：把框畫在照片上，貼歪了、框到別的東西，
                # 醫師三秒就看得出來。所以角點必須回傳。
                marker_quad = [[int(round(x)), int(round(y))] for x, y in _c.tolist()]
                marker_id = int(det[1]); marker_mm = _mm
        # ── 色準校正：用貼紙的中性色塊做白平衡與曝光正規化 ───────────────
        #
        # ⚠ 這裡修掉的是一個**現行路徑的缺陷**，不只是新增功能。
        #
        # wound_classifier.tissue_classmap_v2 的白平衡有兩條路：有量到的灰塊就用
        # patch_wb（正確），否則退回 gray_world_wb。而這裡一直沒有傳灰塊值，
        # 所以每一張臨床影像的組織分類都是在 **gray-world** 之後做的。
        #
        # gray-world 假設「場景平均為灰」。一張以傷口為主體的近拍照嚴重違反這個假設：
        # 實測它把紅色增益壓到正確值的 ×0.78（傷口佔畫面越大越嚴重，最差 ×0.74），
        # 而紅色正是肉芽的判準——被壓掉的肉芽像素會掉出飽和度條件，落進「其他」。
        #
        # 更根本的問題是 gray-world 的增益取自**場景統計**：同一個傷口換個取景
        # 就是另一組增益（實測跨構圖離散 2.6%，色卡則是 0.0%）。那讓跨次追蹤
        # 與跨裝置比較都失去共同基準，而那正是這個平台的核心用途。
        colorcal = None
        if marker_quad is not None:
            try:
                from color_calib import calibrate as _color_calibrate
                # img 是 RGB（見上方 cvtColor），務必指明——弄反的話增益會反向套用，
                # 而輸出仍是一張「看起來有被處理過」的影像。
                colorcal = _color_calibrate(img, marker_quad,
                                            marker_mm=marker_mm or 12.0, order="rgb")
            except Exception as _e:
                logger.warning("色準校正略過：%s", _e)
        if area_cm2 is None:
            cpp = request.form.get('cm_per_pixel', type=float)
            if cpp:
                area_cm2 = float(mask.sum()) * (cpp ** 2); calib = "manual_cm_per_pixel"
                mm_per_px = cpp * 10.0
        # 影像品質指標。
        #
        # 模糊、過曝、角度過斜的影像會產生垃圾組織 GT，而**垃圾 GT 比沒有 GT 更糟**：
        # 它會把錯誤教給模型，且從指標上看不出來。所以在 classify 當下就算好落盤，
        # 匯出訓練集時可以依門檻篩。
        #
        # ⚠ 只標記、不自動丟棄。自動丟會讓「為什麼這張沒進訓練集」變成黑箱；
        # 標記讓門檻可以事後調整，而且每次調整都留得下紀錄。
        quality = {}
        try:
            _ys, _xs = np.nonzero(mask)
            if _ys.size > 0:
                _y0, _y1 = int(_ys.min()), int(_ys.max()) + 1
                _x0, _x1 = int(_xs.min()), int(_xs.max()) + 1
                _roi = img[_y0:_y1, _x0:_x1]
                _g = cv2.cvtColor(_roi, cv2.COLOR_RGB2GRAY)
                quality["focus_lapvar"] = round(float(cv2.Laplacian(_g, cv2.CV_64F).var()), 1)
                quality["clipped_frac"] = round(float(((_g <= 2) | (_g >= 253)).mean()), 4)
                quality["roi_short_px"] = int(min(_y1 - _y0, _x1 - _x0))
            if marker_quad:
                _q = np.array(marker_quad, dtype=float)
                _sides = [float(np.linalg.norm(_q[i] - _q[(i + 1) % 4])) for i in range(4)]
                quality["marker_side_px"] = round(float(np.mean(_sides)), 1)
                quality["marker_frac"] = round(float(np.mean(_sides)) / max(W, H), 4)
                # 正方形標記在正射投影下四邊等長；差異愈大代表拍攝角度愈斜，
                # 而斜視角會讓 mm/px 在畫面不同位置不一致（比例法假設平面正對）。
                quality["marker_skew"] = round((max(_sides) - min(_sides)) / max(1e-6, np.mean(_sides)), 4)
        except Exception as _qe:
            logger.warning("品質指標計算失敗: %s", _qe)

        # Stage4 組織 v2 + Stage5 PUSH
        #
        # ⚠ 有色卡就把量到的灰塊值傳進去，讓 tissue_classmap_v2 走 patch_wb 那條路。
        # 沒傳的話它會退回 gray_world_wb——見上方色準校正段落的說明。
        #
        # 傳 grey_reference() 而不是自己先套用增益再傳一張校正過的圖：
        # 後者會讓 wound_classifier 在已校正的影像上**再做一次** gray-world，
        # 兩層白平衡疊起來的結果沒有人推得出來，而且它不會報錯。
        _gp = colorcal.grey_reference() if (colorcal is not None and colorcal.ok) else None
        t = tissue_proxy_v2(img, mask, gray_patch_rgb=_gp)
        push = push_score(area_cm2, t)
        # 傷口輪廓多邊形(最大連通、approxPolyDP 精簡)→ 供 App 醫師修邊/飛輪標註
        # ── 傷口輪廓：**所有**連通元件，不是只有最大的那一個 ──────────
        #
        # 同一肢體多處傷口是臨床常態（小腿同時有兩處潰瘍）。舊版寫成
        # `max(_cnts, key=cv2.contourArea)`，於是 AI 明明分割到兩個傷口，
        # 回傳的初始輪廓只框一個——醫師得自己把第二個補畫出來，
        # 而如果他沒注意到，那個傷口在訓練集裡就被標成背景。
        #
        # 面積（measure_area_cm2_ratio）本來就用整張遮罩、涵蓋所有元件，
        # 所以舊版的「面積算兩個、輪廓只有一個」是自相矛盾的。
        #
        # 雜點過濾：小於 64 px 的元件不回傳。分割模型的邊緣毛邊會產生
        # 幾像素的孤立點，把它們當成獨立傷口只會讓醫師多刪幾次。
        wound_polys = []
        _cnts, _hh = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for _c in sorted(_cnts, key=cv2.contourArea, reverse=True):
            if cv2.contourArea(_c) < 64:
                continue
            # 0.01→0.003:初始點加密(~3x),描邊更貼
            _ap = cv2.approxPolyDP(_c, 0.003 * cv2.arcLength(_c, True), True).reshape(-1, 2)
            if len(_ap) >= 3:
                wound_polys.append([[int(x), int(y)] for x, y in _ap.tolist()])
        # 相容用：舊版 App 只讀 wound_polygon，拿到的仍是主要傷口。
        wound_poly = wound_polys[0] if wound_polys else []
        return jsonify({
            # 飛輪資料鏈:image_id 綁後端已存影像;image_w/h = wound_polygon 與醫師修邊 GT 的座標空間
            # (缺尺寸則 polygon 無法柵格化成遮罩 → 樣本不可訓練,見 api_flywheel 稽核註記)
            'image_id': image_id, 'image_w': int(W), 'image_h': int(H),
            'persisted': persistence['persisted'],
            'persistence_reason': persistence['persistence_reason'],
            'canonicalization_version': CANONICALIZATION_VERSION,
            # 同一批位元組先前已上傳過 → 幾乎必然是重複量測同一張範例/示範圖。
            # App 在臨床模式看到 true 要警示:那多半不是這次回診拍的照片。
            'image_reused': bool(image_reused),
            'stage2_segment': {'model': seg_model, 'wound_ratio': round(float(mask.mean()), 4), 'confidence': round(conf, 4),
                               'route': route, 'escalated': escalated, 'au_area_ratio': au_ratio, 'iou_student_au': iou_sa,
                               'wound_polygon': wound_poly,
                               # 所有輪廓（由大到小）。只讀 wound_polygon 的話，
                               # 多處傷口時第二個之後都看不到。
                               'wound_polygons': wound_polys},
            'stage3_calibrate': {'method': calib, 'area_cm2': (round(area_cm2, 2) if area_cm2 is not None else None),
                                 'mm_per_px': (round(mm_per_px, 6) if mm_per_px is not None else None),
                                 # 角點順序 TL,TR,BR,BL(影像座標,與 image_w/h 同一空間)。供 App 畫出校正框。
                                 'marker_quad': marker_quad, 'marker_id': marker_id, 'marker_mm': marker_mm,
                                 'note': ('未校正(無 ArUco 且未提供 cm_per_pixel)' if area_cm2 is None else None)},
            'stage4_tissue': {
                # method 要說清楚走了哪一條白平衡。兩條路的結果差很多，
                # 而事後從 tissue_frac 完全看不出來當初用的是哪一條。
                'method': ('v2(色卡WB+HSV)' if _gp is not None else 'v2(gray-world WB+HSV)'),
                'tissue_frac': {k: round(t[k], 3) for k in ('necrosis','slough','granulation','epithelial','other')},
                # 印刷單也可能做成多組織混色示範,故照常計算;但顏料≠組織,讀數不可作臨床解讀
                'note': ('印刷模擬圖:組織比例由顏料色彩推得,僅供色彩分型演算法比對,不可作臨床解讀'
                         if phantom else
                         (None if _gp is not None else
                          '無色卡參考,白平衡退回 gray-world:紅色會被系統性壓抑(實測 ×0.78),'
                          '肉芽可能被低估。請確認校正貼紙完整入鏡。'))},
            # 色準校正的參數與診斷。**一定要落盤**：影像會依保存政策清除，
            # 事後想知道「那批資料當時的光源是什麼」就只剩這幾個數字。
            'stage3b_colorcal': (colorcal.as_dict() if colorcal is not None else
                                 {'ok': False, 'reason': '無 ArUco,無法定位色卡'}),
            'stage5_severity': {k: push[k] for k in ('tool','area_subscore','tissue_subscore','exudate_subscore','total_partial_img','total_full','range_full')},
            # 品質指標供 App 顯示與訓練集篩選。門檻不寫在後端——
            # 不同用途（臨床顯示 vs 訓練集）該用不同門檻，硬編一組會讓其中一邊將就。
            'quality': quality,
            'phantom_mode': phantom,
            'phantom_pass': phantom_pass,   # strict / gray_world_wb(偏色時已自動白平衡重試)
            # true = AI 沒抓到但色彩分割抓得到 → 幾乎確定是把印刷模擬圖誤選成臨床/範例
            'phantom_hint': phantom_hint,
            'disclaimer': ('【印刷模擬圖模式】分割走決定性 HSV 色彩法、**未使用 AI 模型**;'
                           '面積可作量測鏈驗證,組織/PUSH 為顏料推算,非臨床結果'
                           if phantom else
                           '輔助用途、非診斷、需醫師確認;滲液量無法由單張影像判定,需醫師輸入')
        }), 200
    except InvalidImage as e:
        return jsonify({'error': str(e), 'stage': 'canonicalization'}), 400
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
