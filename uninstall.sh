#!/usr/bin/env bash
# ProxOrchestrator uninstaller
# Usage: sudo ./uninstall.sh [--keep-data]
set -euo pipefail

APP_USER="proxorchestrator"
APP_HOME="/opt/proxorchestrator"
LOG_DIR="/var/log/proxorchestrator"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

KEEP_DATA=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --help|-h)
            echo "Usage: sudo $0 [--keep-data]"
            echo ""
            echo "Options:"
            echo "  --keep-data   Remove services and packages but keep /opt/proxorchestrator data"
            echo "                (database, uploads, SSL certs, SSH keys)"
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

echo ""
echo "============================================================"
echo "  ProxOrchestrator Uninstaller"
echo "============================================================"
echo ""
if [[ "${KEEP_DATA}" == "true" ]]; then
    echo "  Mode: Remove services only (data preserved)"
else
    echo "  Mode: Full removal (services + all data)"
    echo ""
    echo "  WARNING: This will permanently delete:"
    echo "    - The ProxOrchestrator application and database"
    echo "    - All uploaded disk images"
    echo "    - SSL certificates and SSH keys"
    echo "    - The '${APP_USER}' system user"
fi
echo ""
read -rp "  Type 'yes' to confirm: " CONFIRM
if [[ "${CONFIRM}" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi
echo ""

# ---------------------------------------------------------------------------
# Stop and disable services
# ---------------------------------------------------------------------------

echo "==> Stopping ProxOrchestrator services..."

for SVC in proxorchestrator-gunicorn proxorchestrator-celery proxorchestrator-daphne; do
    if systemctl is-active "${SVC}" &>/dev/null; then
        systemctl stop "${SVC}"
        echo "    Stopped: ${SVC}"
    fi
    if systemctl is-enabled "${SVC}" &>/dev/null; then
        systemctl disable "${SVC}"
        echo "    Disabled: ${SVC}"
    fi
    if [[ -f "/etc/systemd/system/${SVC}.service" ]]; then
        rm -f "/etc/systemd/system/${SVC}.service"
        echo "    Removed: /etc/systemd/system/${SVC}.service"
    fi
done

systemctl daemon-reload

# ---------------------------------------------------------------------------
# Remove Nginx config
# ---------------------------------------------------------------------------

echo "==> Removing sudoers rules..."
rm -f /etc/sudoers.d/proxorchestrator-nginx
rm -f /etc/sudoers.d/proxmigrate-nginx
echo "    Sudoers rules removed."

echo "==> Removing Nginx configuration..."

# Remove both old and new naming
for name in proxorchestrator proxmigrate; do
    rm -f "/etc/nginx/sites-enabled/${name}"
    rm -f "/etc/nginx/sites-available/${name}"
    rm -f "/etc/nginx/conf.d/${name}.conf"
done

NGINX_AVAILABLE="/etc/nginx/sites-available/proxorchestrator"
NGINX_ENABLED="/etc/nginx/sites-enabled/proxorchestrator"

if [[ -L "${NGINX_ENABLED}" ]]; then
    rm -f "${NGINX_ENABLED}"
    echo "    Removed: ${NGINX_ENABLED}"
fi
if [[ -f "${NGINX_AVAILABLE}" ]]; then
    rm -f "${NGINX_AVAILABLE}"
    echo "    Removed: ${NGINX_AVAILABLE}"
fi

if systemctl is-active nginx &>/dev/null; then
    nginx -t 2>/dev/null && systemctl reload nginx
    echo "    Nginx reloaded."
fi

# ---------------------------------------------------------------------------
# Remove runtime directory
# ---------------------------------------------------------------------------

if [[ -d /run/proxorchestrator ]]; then
    rm -rf /run/proxorchestrator
    echo "==> Removed runtime directory: /run/proxorchestrator"
fi

# ---------------------------------------------------------------------------
# Full data removal (unless --keep-data)
# ---------------------------------------------------------------------------

if [[ "${KEEP_DATA}" == "false" ]]; then
    echo "==> Removing application data..."

    for dir in "${APP_HOME}" /opt/proxmigrate; do
        if [[ -d "${dir}" ]]; then
            rm -rf "${dir}"
            echo "    Removed: ${dir}"
        fi
    done

    for dir in "${LOG_DIR}" /var/log/proxmigrate; do
        if [[ -d "${dir}" ]]; then
            rm -rf "${dir}"
            echo "    Removed: ${dir}"
        fi
    done

    echo "==> Removing system users..."
    for user in "${APP_USER}" proxmigrate; do
        if id "${user}" &>/dev/null; then
            userdel "${user}"
            echo "    User '${user}' removed."
        fi
    done

    # Clean up old service files that may linger
    for svc in proxmigrate-gunicorn proxmigrate-celery proxmigrate-daphne; do
        systemctl stop "${svc}" 2>/dev/null || true
        systemctl disable "${svc}" 2>/dev/null || true
        rm -f "/etc/systemd/system/${svc}.service"
    done
    systemctl daemon-reload
else
    echo "==> Data preserved at ${APP_HOME} (--keep-data specified)."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "============================================================"
echo "  ProxOrchestrator has been uninstalled."
if [[ "${KEEP_DATA}" == "true" ]]; then
    echo "  Your data is preserved at: ${APP_HOME}"
    echo "  To also remove data, run: sudo $0 (without --keep-data)"
fi
echo "============================================================"
echo ""
