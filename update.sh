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

echo "==> Restarting services..."
systemctl restart proxmigrate-gunicorn proxmigrate-celery
systemctl is-active proxmigrate-gunicorn proxmigrate-celery

echo ""
echo "ProxMigrate updated successfully."
