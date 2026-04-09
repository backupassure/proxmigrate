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

for SVC in proxorchestrator-gunicorn proxorchestrator-celery; do
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

echo "==> Removing Nginx configuration..."

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

    if [[ -d "${APP_HOME}" ]]; then
        rm -rf "${APP_HOME}"
        echo "    Removed: ${APP_HOME}"
    fi

    if [[ -d "${LOG_DIR}" ]]; then
        rm -rf "${LOG_DIR}"
        echo "    Removed: ${LOG_DIR}"
    fi

    echo "==> Removing system user '${APP_USER}'..."
    if id "${APP_USER}" &>/dev/null; then
        userdel "${APP_USER}"
        echo "    User '${APP_USER}' removed."
    else
        echo "    User '${APP_USER}' not found — skipping."
    fi
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
