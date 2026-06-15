#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# generate-ssl.sh  —  產生自簽憑證（開發 / 測試環境用）
#
# 正式環境請改用 Let's Encrypt：
#   certbot certonly --standalone -d your-domain.com
#   cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ssl/
#   cp /etc/letsencrypt/live/your-domain.com/privkey.pem   ssl/
# ══════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSL_DIR="$SCRIPT_DIR/ssl"

mkdir -p "$SSL_DIR"

openssl req -x509 \
    -newkey rsa:4096 \
    -keyout "$SSL_DIR/privkey.pem" \
    -out    "$SSL_DIR/fullchain.pem" \
    -days   365 \
    -nodes  \
    -subj   "/C=TW/ST=Taipei/L=Taipei/O=WoundAI/CN=localhost"

echo "自簽憑證已產生："
echo "  $SSL_DIR/fullchain.pem"
echo "  $SSL_DIR/privkey.pem"
echo ""
echo "警告：自簽憑證僅供開發測試，正式環境請使用 Let's Encrypt 或 CA 憑證。"
