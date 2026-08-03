# ── Stage 1: 依賴安裝 ─────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 系統依賴（OpenCV headless 需要 libglib2.0）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: 執行映像 ─────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 複製已安裝的套件
COPY --from=builder /install /usr/local
# 同步系統函式庫
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# 複製應用程式碼
COPY . .

# 非 root 使用者（安全最佳實務）
RUN useradd -m -u 1000 woundai \
    && chown -R woundai:woundai /app
USER woundai

# 上傳暫存目錄
RUN mkdir -p /app/uploads /app/models

# Cloud Run 會注入 PORT（預設 8080）。本機不設時退回 8080。
ENV PORT=8080
EXPOSE 8080

# ⚠ **1 個 worker + 多執行緒**，不是多個 worker。
#
# ONNX Runtime 的 session 是 per-process 的：`analysis_service = WoundAnalysisService()`
# 在模組層載入模型，所以 gunicorn 每 fork 一個 worker 就多一份完整的模型駐留記憶體。
# 原本的 `--workers 4` 等於 4 份 student + A-UNet + UNet++ ——在 Cloud Run 2Gi 上必定
# 被 OOM kill，而 Cloud Run 的 OOM 只會回 500、不留任何 Python 例外，是最難查的一種失敗。
#
# 用執行緒是安全的：推論在 ONNX Runtime 的 C++ 層執行並且**會釋放 GIL**，
# 所以多執行緒真的能並行，不是假的。並行度由 Cloud Run 的 --concurrency 控制。
#
# --timeout 120 對齊 Cloud Run 的請求逾時；難例集成實測 2 vCPU 上約 0.8 秒，
# 但冷啟動載模型要 10–30 秒，逾時設太短會讓第一個請求直接失敗。
CMD exec gunicorn \
     --bind "0.0.0.0:${PORT}" \
     --workers 1 \
     --threads 8 \
     --timeout 120 \
     --graceful-timeout 30 \
     --access-logfile - \
     --error-logfile - \
     app:app
