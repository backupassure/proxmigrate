#!/usr/bin/env bash
# ProxMigrate updater
# Usage: sudo ./update.sh
#
# Run from the repo root after pulling the latest code.  Updates application
# files, installs any new Python/Node dependencies, runs database migrations,
# syncs systemd units (daphne), patches nginx for WebSocket support if needed,
# and restarts services.  Does NOT change TLS certs or SSH keys.
set -euo pipefail

APP_USER="proxmigrate"
APP_HOME="/opt/proxmigrate"
VENV="${APP_HOME}/venv"
PYTHON="${VENV}/bin/python"
PIP="${VENV}/bin/pip"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: update.sh must be run as root (sudo ./update.sh)" >&2
    exit 1
fi

echo "==> Copying application files to ${APP_HOME}..."
COPY_DIRS=(apps proxmigrate templates static help deploy scripts)
for d in "${COPY_DIRS[@]}"; do
    if [[ -d "${SCRIPT_DIR}/${d}" ]]; then
        rsync -a --delete "${SCRIPT_DIR}/${d}/" "${APP_HOME}/${d}/"
        chown -R "${APP_USER}:${APP_USER}" "${APP_HOME}/${d}"
    fi
done

# Copy top-level Python files
for f in manage.py requirements.txt package.json; do
    if [[ -f "${SCRIPT_DIR}/${f}" ]]; then
        cp "${SCRIPT_DIR}/${f}" "${APP_HOME}/${f}"
        chown "${APP_USER}:${APP_USER}" "${APP_HOME}/${f}"
    fi
done

# Ensure venv is owned by app user (fixes permission issues from older installs
# where install.sh ran pip as root)
chown -R "${APP_USER}:${APP_USER}" "${VENV}"

echo "==> Installing/upgrading Python dependencies..."
sudo -u "${APP_USER}" "${PIP}" install -q -r "${APP_HOME}/requirements.txt"

echo "==> Running database migrations..."
sudo -u "${APP_USER}" \
    DJANGO_SETTINGS_MODULE=proxmigrate.settings.production \
    "${PYTHON}" "${APP_HOME}/manage.py" migrate --noinput \
    --settings=proxmigrate.settings.production

echo "==> Installing frontend dependencies..."
if command -v npm &>/dev/null; then
    sudo -u "${APP_USER}" bash -c "cd ${APP_HOME} && npm install --omit=dev 2>&1 | tail -1"
else
    echo "    WARN: npm not found — skipping xterm.js install"
fi

echo "==> Collecting static files..."
sudo -u "${APP_USER}" \
    DJANGO_SETTINGS_MODULE=proxmigrate.settings.production \
    "${PYTHON}" "${APP_HOME}/manage.py" collectstatic --noinput \
    --settings=proxmigrate.settings.production

# ---------------------------------------------------------------------------
# Ensure ACME certificate automation prerequisites exist
# ---------------------------------------------------------------------------
echo "==> Checking ACME prerequisites..."

# ACME challenge directory
ACME_CHALLENGE_DIR="${APP_HOME}/certs/acme-challenge"
if [[ ! -d "${ACME_CHALLENGE_DIR}" ]]; then
    mkdir -p "${ACME_CHALLENGE_DIR}"
    chown "${APP_USER}:${APP_USER}" "${ACME_CHALLENGE_DIR}"
    echo "    Created ${ACME_CHALLENGE_DIR}"
fi

# Empty ACME challenge nginx config
ACME_CONF="${APP_HOME}/deploy/acme-challenge.conf"
if [[ ! -f "${ACME_CONF}" ]]; then
    touch "${ACME_CONF}"
    chown "${APP_USER}:${APP_USER}" "${ACME_CONF}"
    echo "    Created ${ACME_CONF}"
fi

# Add ACME include to live nginx config if not present
NGINX_CONF=""
for p in /etc/nginx/sites-enabled/proxmigrate /etc/nginx/sites-available/proxmigrate /etc/nginx/conf.d/proxmigrate.conf; do
    if [[ -f "${p}" ]]; then
        NGINX_CONF="${p}"
        break
    fi
done

if [[ -n "${NGINX_CONF}" ]] && ! grep -q "acme-challenge.conf" "${NGINX_CONF}" 2>/dev/null; then
    echo "" >> "${NGINX_CONF}"
    echo "# ACME HTTP-01 challenge server (managed by ProxMigrate)" >> "${NGINX_CONF}"
    echo "include ${ACME_CONF};" >> "${NGINX_CONF}"
    echo "    Added ACME include to ${NGINX_CONF}"
    nginx -t 2>/dev/null && nginx -s reload 2>/dev/null && echo "    nginx reloaded"
fi

# Add ACME sudoers rule if not present
SUDOERS_FILE="/etc/sudoers.d/proxmigrate-nginx"
if [[ -f "${SUDOERS_FILE}" ]] && ! grep -q "acme-challenge" "${SUDOERS_FILE}" 2>/dev/null; then
    echo "${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee ${ACME_CONF}" >> "${SUDOERS_FILE}"
    echo "    Added ACME sudoers rule"
fi

# Update celery service to include Beat scheduler (-B flag)
CELERY_SERVICE="/etc/systemd/system/proxmigrate-celery.service"
if [[ -f "${CELERY_SERVICE}" ]] && ! grep -q "\-B" "${CELERY_SERVICE}" 2>/dev/null; then
    cp "${APP_HOME}/deploy/celery.service.template" "${CELERY_SERVICE}"
    systemctl daemon-reload
    echo "    Updated celery service with Beat scheduler"
fi

# ---------------------------------------------------------------------------
# Daphne WebSocket service
# ---------------------------------------------------------------------------
echo "==> Updating Daphne WebSocket service..."
cp "${APP_HOME}/deploy/daphne.service.template" /etc/systemd/system/proxmigrate-daphne.service
systemctl daemon-reload
systemctl enable proxmigrate-daphne 2>/dev/null
echo "    Daphne service installed."

# Ensure gunicorn service also preserves the shared RuntimeDirectory
if ! grep -q 'RuntimeDirectoryPreserve' /etc/systemd/system/proxmigrate-gunicorn.service 2>/dev/null; then
    sed -i '/^RuntimeDirectoryMode=/a RuntimeDirectoryPreserve=yes' \
        /etc/systemd/system/proxmigrate-gunicorn.service
    systemctl daemon-reload
    echo "    Added RuntimeDirectoryPreserve to gunicorn service."
fi

# Auto-update nginx config with WebSocket proxy block if missing
if [[ -n "$NGINX_CONF" ]] && ! grep -q 'proxmigrate_ws' "$NGINX_CONF"; then
    echo "==> Adding WebSocket proxy config to nginx..."
    # Add the daphne upstream block after the gunicorn upstream
    sed -i '/^upstream proxmigrate_app {/,/^}/{
        /^}/a\
\
upstream proxmigrate_ws {\
    server unix:\/run\/proxmigrate\/daphne.sock fail_timeout=0;\
}
    }' "$NGINX_CONF"
    # Add the /ws/ location block before the catch-all location /
    sed -i '/location \/ {/i\
    location /ws/ {\
        proxy_pass          http://proxmigrate_ws;\
        proxy_http_version  1.1;\
        proxy_set_header    Upgrade           $http_upgrade;\
        proxy_set_header    Connection        "upgrade";\
        proxy_set_header    Host              $http_host;\
        proxy_set_header    X-Real-IP         $remote_addr;\
        proxy_set_header    X-Forwarded-For   $proxy_add_x_forwarded_for;\
        proxy_set_header    X-Forwarded-Proto $scheme;\
        proxy_read_timeout  86400s;\
        proxy_send_timeout  86400s;\
    }\
' "$NGINX_CONF"
    nginx -t && systemctl reload nginx
    echo "    Nginx WebSocket config added and reloaded."
fi

echo "==> Restarting services..."
systemctl restart proxmigrate-gunicorn proxmigrate-celery
sleep 2
systemctl restart proxmigrate-daphne
systemctl is-active proxmigrate-gunicorn proxmigrate-celery proxmigrate-daphne

echo ""
echo "ProxMigrate updated successfully."
