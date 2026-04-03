#!/usr/bin/env bash
# ProxMigrate updater
# Usage: sudo ./update.sh
#
# Run from the repo root after pulling the latest code.  Updates application
# files, installs any new Python dependencies, runs database migrations, and
# restarts services.  Does NOT change the nginx config, TLS certs, systemd
# units, or SSH keys.
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
COPY_DIRS=(apps proxmigrate templates static help deploy)
for d in "${COPY_DIRS[@]}"; do
    if [[ -d "${SCRIPT_DIR}/${d}" ]]; then
        rsync -a --delete "${SCRIPT_DIR}/${d}/" "${APP_HOME}/${d}/"
        chown -R "${APP_USER}:${APP_USER}" "${APP_HOME}/${d}"
    fi
done

# Copy top-level Python files
for f in manage.py requirements.txt; do
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

echo "==> Restarting services..."
systemctl restart proxmigrate-gunicorn proxmigrate-celery
systemctl is-active proxmigrate-gunicorn proxmigrate-celery

echo ""
echo "ProxMigrate updated successfully."
