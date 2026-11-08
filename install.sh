#!/usr/bin/env bash
# Instalasi apiqris di VPS Ubuntu/Debian — project mandiri (tanpa kode bot),
# pola sama dengan apisms: disalin ke /var/www/apiqris + systemd service.
#
# Dengan domain (nginx + HTTPS Let's Encrypt):
#   chmod +x install.sh && sudo ./install.sh qris.domainkamu.com
# Tanpa domain (pakai IP VPS langsung, API di http://IP_VPS:2020):
#   chmod +x install.sh && sudo ./install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Jalankan sebagai root: sudo ./install.sh [domain]"
  exit 1
fi

DOMAIN="${1:-}"
APP_NAME="apiqris"
APP_DIR="/var/www/${APP_NAME}"
APP_USER="${APP_NAME}"
PORT="2020"
BIND_HOST="127.0.0.1"
if [ -z "${DOMAIN}" ]; then
  # Tanpa domain -> bind 0.0.0.0 agar API bisa diakses via http://IP_VPS:PORT.
  BIND_HOST="0.0.0.0"
fi
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
NGINX_CONF="/etc/nginx/sites-available/${APP_NAME}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> [1/5] install paket sistem"
apt-get update -y
if [ -n "${DOMAIN}" ]; then
  apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx curl
else
  apt-get install -y python3 python3-venv python3-pip curl
fi

echo "==> [2/5] user service & salin folder ke ${APP_DIR}"
id -u "${APP_USER}" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
  "${SRC_DIR}/" "${APP_DIR}/"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> [3/5] virtualenv & dependensi"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "==> [4/5] cek konfigurasi (${APP_DIR}/.env)"
[ -s "${APP_DIR}/.env" ] || { echo "!! apiqris/.env tidak ada / kosong. Isi credential dulu."; exit 1; }
grep -q "^WIJAYAPAY_CODE_MERCHANT=." "${APP_DIR}/.env" || { echo "!! WIJAYAPAY_CODE_MERCHANT kosong di .env."; exit 1; }
grep -q "^WIJAYAPAY_API_KEY=." "${APP_DIR}/.env" || { echo "!! WIJAYAPAY_API_KEY kosong di .env."; exit 1; }
grep -q "^APIQRIS_API_KEY=." "${APP_DIR}/.env" || { echo "!! APIQRIS_API_KEY kosong di .env (klien tidak bisa auth)."; exit 1; }

echo "==> [5/5] systemd service ${APP_NAME}"
cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=apiqris - API pembuat QRIS WijayaPay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/uvicorn main:app --host ${BIND_HOST} --port ${PORT} --workers 1 --access-logfile -
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${APP_NAME}"
systemctl restart "${APP_NAME}"

if [ -n "${DOMAIN}" ]; then
  echo "==> nginx reverse proxy untuk ${DOMAIN}"
  cat > "${NGINX_CONF}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }
}
EOF

  ln -sf "${NGINX_CONF}" "/etc/nginx/sites-enabled/${APP_NAME}"
  nginx -t
  systemctl reload nginx

  echo "==> HTTPS (Let's Encrypt)"
  certbot --nginx -d "${DOMAIN}" --redirect --non-interactive --agree-tos --register-unsafely-without-email 2>/tmp/certbot.log || {
    echo "!! certbot gagal. Pastikan DNS ${DOMAIN} sudah mengarah ke IP server, lalu jalankan:"
    echo "   certbot --nginx -d ${DOMAIN} --redirect"
  }
else
  echo "==> nginx/HTTPS dilewati (tanpa domain)"
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  if [ -n "${DOMAIN}" ]; then
    ufw allow 80/tcp
    ufw allow 443/tcp
  else
    ufw allow "${PORT}/tcp"
  fi
fi

echo
echo "==> selesai"
up=0
for _ in $(seq 1 15); do
  if curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    up=1
    break
  fi
  sleep 1
done
if [ "${up}" = "1" ]; then
  curl -fsS "http://127.0.0.1:${PORT}/" && echo
else
  echo "!! service belum merespons setelah 15 detik. Cek:"
  echo "   systemctl status ${APP_NAME}"
  echo "   journalctl -u ${APP_NAME} -n 50"
fi
if [ -n "${DOMAIN}" ]; then
  BASE_URL="https://${DOMAIN}"
else
  IP="$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  BASE_URL="http://${IP:-IP_VPS}:${PORT}"
fi
echo "Endpoint:"
echo "  POST ${BASE_URL}/create-qris   body: {api_key, device, user_id, amount}"
echo "  POST ${BASE_URL}/check-qris    body: {api_key, ref_id, trx_reference}"
echo "  POST ${BASE_URL}/webhook       (didaftarkan sebagai callback URL di dashboard WijayaPay)"
echo
echo "Perintah berguna:"
echo "  systemctl status ${APP_NAME}"
echo "  journalctl -u ${APP_NAME} -f        # lihat log [CREATE]"
echo "  systemctl restart ${APP_NAME}"
