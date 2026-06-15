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

EXPOSE 5000

# Gunicorn：4 worker，逾時 120s（AI 推論可能耗時）
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
