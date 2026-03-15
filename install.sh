#!/usr/bin/env bash
# ProxMigrate installer
# Usage: sudo ./install.sh [--port <n>]
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_USER="proxmigrate"
APP_HOME="/opt/proxmigrate"
VENV="${APP_HOME}/venv"
PYTHON="${VENV}/bin/python"
PIP="${VENV}/bin/pip"
CELERY="${VENV}/bin/celery"
GUNICORN="${VENV}/bin/gunicorn"
CERTS_DIR="${APP_HOME}/certs"
SSH_DIR="${APP_HOME}/.ssh"
LOG_DIR="/var/log/proxmigrate"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PORT=8443
PORT="${DEFAULT_PORT}"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    echo "Usage: sudo $0 [--port <1-65535>] [--help]"
    echo ""
    echo "Options:"
    echo "  --port <n>   HTTPS port for ProxMigrate (default: ${DEFAULT_PORT})"
    echo "  --help       Show this help and exit"
    exit 0
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            shift
            PORT="${1:-}"
            if [[ -z "${PORT}" ]]; then
                echo "ERROR: --port requires a value." >&2
                exit 1
            fi
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage
            ;;
    esac
done

# Validate port
if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    echo "ERROR: Invalid port '${PORT}'. Must be an integer between 1 and 65535." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------

echo "==> Detecting operating system..."

if [[ ! -f /etc/os-release ]]; then
    echo "ERROR: /etc/os-release not found. Cannot detect OS." >&2
    exit 1
fi

# shellcheck source=/dev/null
source /etc/os-release

OS_ID="${ID:-unknown}"
OS_VERSION="${VERSION_ID:-unknown}"

case "${OS_ID}" in
    ubuntu)
        case "${OS_VERSION}" in
            22.04|24.04)
                echo "    Detected: Ubuntu ${OS_VERSION} — supported."
                ;;
            *)
                echo "WARNING: Ubuntu ${OS_VERSION} is not officially supported." >&2
                echo "         Supported versions: 22.04, 24.04"
                echo "         Proceeding anyway — things may not work correctly."
                ;;
        esac
        ;;
    debian)
        case "${OS_VERSION}" in
            12)
                echo "    Detected: Debian 12 (Bookworm) — supported."
                ;;
            13)
                echo "    Detected: Debian 13 (Trixie) — supported."
                ;;
            *)
                echo "WARNING: Debian ${OS_VERSION} is not officially supported." >&2
                echo "         Supported versions: 12 (Bookworm), 13 (Trixie)"
                echo "         Proceeding anyway — things may not work correctly."
                ;;
        esac
        ;;
    *)
        echo "ERROR: Unsupported OS '${OS_ID}'. ProxMigrate supports Ubuntu 22.04/24.04 and Debian 12/13." >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

echo "==> Installing system packages..."
apt-get update -qq
PKGS=(
    python3
    python3-pip
    python3-venv
    python3-dev
    gcc
    libldap-dev
    libsasl2-dev
    libssl-dev
    nginx
    redis-server
    openssl
    openssh-client
    rsync
)

# Detect if running on a Proxmox VE host.
# If so, skip qemu-utils (pve-qemu-kvm provides qemu-img already)
# and warn the user that they should ideally run ProxMigrate on a separate server.
if [[ -f /usr/bin/pveversion ]]; then
    echo ""
    echo "  NOTE: Proxmox VE detected on this host."
    echo "  ProxMigrate is designed to run on a SEPARATE server that connects to Proxmox"
    echo "  via SSH and REST API. Installing here is supported but not recommended."
    echo "  qemu-utils install skipped — pve-qemu-kvm already provides qemu-img."
    echo ""
    # pve-qemu-kvm provides qemu-img, no need to install qemu-utils
else
    PKGS+=(qemu-utils)
fi

apt-get install -y "${PKGS[@]}"

# ---------------------------------------------------------------------------
# System user
# ---------------------------------------------------------------------------

echo "==> Creating system user '${APP_USER}'..."
if ! id "${APP_USER}" &>/dev/null; then
    useradd \
        --system \
        --home "${APP_HOME}" \
        --shell /sbin/nologin \
        "${APP_USER}"
    echo "    User '${APP_USER}' created."
else
    echo "    User '${APP_USER}' already exists — skipping."
fi

# ---------------------------------------------------------------------------
# Application directory
# ---------------------------------------------------------------------------

echo "==> Setting up application directory at ${APP_HOME}..."
mkdir -p "${APP_HOME}" "${LOG_DIR}"

echo "    Syncing application files..."
rsync -a \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='node_modules' \
    "${SCRIPT_DIR}/" "${APP_HOME}/"

chown -R "${APP_USER}:${APP_USER}" "${APP_HOME}"
chown -R "${APP_USER}:${APP_USER}" "${LOG_DIR}"

# ---------------------------------------------------------------------------
# Python virtual environment
# ---------------------------------------------------------------------------

echo "==> Creating Python virtual environment..."
if [[ ! -d "${VENV}" ]]; then
    python3 -m venv "${VENV}"
fi

echo "==> Installing Python dependencies..."
"${PIP}" install --quiet --upgrade pip
"${PIP}" install --quiet -r "${APP_HOME}/requirements.txt"

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------

echo "==> Creating runtime directories..."
UPLOAD_ROOT="${APP_HOME}/uploads"
mkdir -p "${UPLOAD_ROOT}" "${CERTS_DIR}" "${SSH_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${UPLOAD_ROOT}" "${CERTS_DIR}" "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

# ---------------------------------------------------------------------------
# SSL certificate (self-signed)
# ---------------------------------------------------------------------------

echo "==> Generating self-signed SSL certificate (10 years)..."
if [[ ! -f "${CERTS_DIR}/proxmigrate.crt" ]]; then
    openssl req \
        -x509 \
        -nodes \
        -days 3650 \
        -newkey rsa:4096 \
        -keyout "${CERTS_DIR}/proxmigrate.key" \
        -out "${CERTS_DIR}/proxmigrate.crt" \
        -subj "/CN=proxmigrate/O=ProxMigrate/C=US" \
        2>/dev/null
    chmod 600 "${CERTS_DIR}/proxmigrate.key"
    chown -R "${APP_USER}:${APP_USER}" "${CERTS_DIR}"
    echo "    Certificate written to ${CERTS_DIR}/"
else
    echo "    Certificate already exists — skipping generation."
fi

# ---------------------------------------------------------------------------
# SSH keypair for Proxmox access
# ---------------------------------------------------------------------------

echo "==> Generating SSH keypair for Proxmox host access..."
if [[ ! -f "${SSH_DIR}/id_rsa" ]]; then
    ssh-keygen \
        -t rsa \
        -b 4096 \
        -N "" \
        -C "proxmigrate@$(hostname -f 2>/dev/null || hostname)" \
        -f "${SSH_DIR}/id_rsa" \
        2>/dev/null
    chmod 600 "${SSH_DIR}/id_rsa"
    chmod 644 "${SSH_DIR}/id_rsa.pub"
    chown -R "${APP_USER}:${APP_USER}" "${SSH_DIR}"
    echo "    SSH keypair written to ${SSH_DIR}/"
else
    echo "    SSH keypair already exists — skipping generation."
fi

# ---------------------------------------------------------------------------
# Secret keys
# ---------------------------------------------------------------------------

echo "==> Generating application secret keys..."

SECRET_KEY="$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")"

# Generate FIELD_ENCRYPTION_KEY using django-encrypted-model-fields format
# (Fernet key: 32 bytes base64-encoded)
FIELD_ENCRYPTION_KEY="$(python3 -c "
import base64, os
key = base64.urlsafe_b64encode(os.urandom(32)).decode()
print(key)
")"

# ---------------------------------------------------------------------------
# .env file
# ---------------------------------------------------------------------------

echo "==> Writing /opt/proxmigrate/.env..."
ENV_FILE="${APP_HOME}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
    cat > "${ENV_FILE}" <<EOF
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ALLOWED_HOSTS=*
WEB_PORT=${PORT}
DB_PATH=${APP_HOME}/db.sqlite3
UPLOAD_ROOT=${UPLOAD_ROOT}
FIELD_ENCRYPTION_KEY=${FIELD_ENCRYPTION_KEY}
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
EOF
    chmod 600 "${ENV_FILE}"
    chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
    echo "    .env written."
else
    echo "    .env already exists — skipping (to regenerate, delete ${ENV_FILE})."
fi

# ---------------------------------------------------------------------------
# Nginx configuration
# ---------------------------------------------------------------------------

echo "==> Configuring Nginx..."
NGINX_AVAILABLE="/etc/nginx/sites-available/proxmigrate"
NGINX_ENABLED="/etc/nginx/sites-enabled/proxmigrate"

sed \
    -e "s|{{ WEB_PORT }}|${PORT}|g" \
    -e "s|{{ UPLOAD_ROOT }}|${UPLOAD_ROOT}|g" \
    "${APP_HOME}/deploy/nginx.conf.template" \
    > "${NGINX_AVAILABLE}"

if [[ ! -L "${NGINX_ENABLED}" ]]; then
    ln -s "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
fi

# Remove default nginx site if it conflicts on port 80/443
if [[ -L "/etc/nginx/sites-enabled/default" ]]; then
    rm -f "/etc/nginx/sites-enabled/default"
    echo "    Removed default nginx site."
fi

nginx -t 2>/dev/null && echo "    Nginx config valid."

# ---------------------------------------------------------------------------
# Systemd units
# ---------------------------------------------------------------------------

echo "==> Installing systemd service units..."

GUNICORN_SERVICE="/etc/systemd/system/proxmigrate-gunicorn.service"
CELERY_SERVICE="/etc/systemd/system/proxmigrate-celery.service"

cp "${APP_HOME}/deploy/gunicorn.service.template" "${GUNICORN_SERVICE}"
cp "${APP_HOME}/deploy/celery.service.template" "${CELERY_SERVICE}"

# Create RuntimeDirectory parent so systemd tmpfiles doesn't complain
mkdir -p /run/proxmigrate
chown "${APP_USER}:${APP_USER}" /run/proxmigrate

# ---------------------------------------------------------------------------
# Database migrations & static files
# ---------------------------------------------------------------------------

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
# Admin superuser
# ---------------------------------------------------------------------------

echo "==> Creating admin user..."
echo ""

# Allow non-interactive install via environment variables
if [[ -n "${PROXMIGRATE_ADMIN_USER:-}" && -n "${PROXMIGRATE_ADMIN_PASS:-}" ]]; then
    ADMIN_USER="${PROXMIGRATE_ADMIN_USER}"
    ADMIN_PASS="${PROXMIGRATE_ADMIN_PASS}"
    echo "    Using credentials from environment variables."
else
    read -rp "    Admin username [admin]: " ADMIN_USER
    ADMIN_USER="${ADMIN_USER:-admin}"

    while true; do
        read -rsp "    Admin password: " ADMIN_PASS
        echo ""
        if [[ -z "${ADMIN_PASS}" ]]; then
            echo "    Password cannot be empty. Please try again."
        else
            read -rsp "    Confirm password: " ADMIN_PASS2
            echo ""
            if [[ "${ADMIN_PASS}" == "${ADMIN_PASS2}" ]]; then
                break
            else
                echo "    Passwords do not match. Please try again."
            fi
        fi
    done
fi

ADMIN_EMAIL="${ADMIN_USER}@localhost"

sudo -u "${APP_USER}" \
    DJANGO_SUPERUSER_USERNAME="${ADMIN_USER}" \
    DJANGO_SUPERUSER_PASSWORD="${ADMIN_PASS}" \
    DJANGO_SUPERUSER_EMAIL="${ADMIN_EMAIL}" \
    DJANGO_SETTINGS_MODULE=proxmigrate.settings.production \
    "${PYTHON}" "${APP_HOME}/manage.py" createsuperuser \
    --noinput \
    --settings=proxmigrate.settings.production \
    2>/dev/null || echo "    (Superuser '${ADMIN_USER}' may already exist — skipping.)"

# ---------------------------------------------------------------------------
# Enable and start services
# ---------------------------------------------------------------------------

echo "==> Enabling and starting services..."
systemctl daemon-reload

for SVC in redis-server nginx proxmigrate-gunicorn proxmigrate-celery; do
    systemctl enable "${SVC}" 2>/dev/null || true
    systemctl restart "${SVC}" 2>/dev/null || systemctl start "${SVC}" 2>/dev/null || true
    echo "    ${SVC}: $(systemctl is-active "${SVC}" 2>/dev/null || echo 'unknown')"
done

# ---------------------------------------------------------------------------
# Success banner
# ---------------------------------------------------------------------------

PRIMARY_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo ""
echo "============================================================"
echo "  ProxMigrate installation complete!"
echo "============================================================"
echo ""
echo "  Access URL : https://${PRIMARY_IP}:${PORT}"
echo "  Admin user : ${ADMIN_USER}"
echo ""
echo "  NOTE: Your browser will show a self-signed certificate"
echo "  warning. This is expected. Click 'Advanced' and proceed."
echo ""
echo "  SSH public key for Proxmox setup wizard:"
echo "  $(cat "${SSH_DIR}/id_rsa.pub" 2>/dev/null || echo '  (not found)')"
echo ""
echo "  Logs: ${LOG_DIR}/"
echo "  Config: ${APP_HOME}/.env"
echo "============================================================"
