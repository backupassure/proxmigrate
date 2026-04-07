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
# Package manager detection
# ---------------------------------------------------------------------------

echo "==> Detecting package manager..."

PKG_MANAGER=""
if command -v apt-get &>/dev/null; then
    PKG_MANAGER="apt"
    echo "    Found: apt (Debian/Ubuntu family)"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
    echo "    Found: dnf (RHEL/Fedora family)"
elif command -v yum &>/dev/null; then
    PKG_MANAGER="yum"
    echo "    Found: yum (older RHEL/CentOS family)"
elif command -v zypper &>/dev/null; then
    PKG_MANAGER="zypper"
    echo "    Found: zypper (SUSE/openSUSE family)"
else
    echo ""
    echo "ERROR: No supported package manager found (apt-get, dnf, yum, zypper)." >&2
    echo ""
    echo "ProxMigrate requires the following packages to be installed manually:"
    echo "  - Python 3 with pip and venv"
    echo "  - python3-dev (or python3-devel)"
    echo "  - gcc"
    echo "  - LDAP development headers (libldap-dev or openldap-devel)"
    echo "  - SASL development headers (libsasl2-dev or cyrus-sasl-devel)"
    echo "  - OpenSSL development headers (libssl-dev or openssl-devel)"
    echo "  - nginx"
    echo "  - redis"
    echo "  - openssl, openssh-client (or openssh), rsync, wget"
    echo ""
    echo "After installing the above, re-run this script."
    exit 1
fi

# ---------------------------------------------------------------------------
# Distro-specific variables
# ---------------------------------------------------------------------------

if [[ "${PKG_MANAGER}" == "apt" ]]; then
    REDIS_SVC="redis-server"
    NGINX_CONF_DIR="/etc/nginx/sites-available"
    NGINX_ENABLED_DIR="/etc/nginx/sites-enabled"
else
    REDIS_SVC="redis"
    NGINX_CONF_DIR="/etc/nginx/conf.d"
    NGINX_ENABLED_DIR=""
fi

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

echo "==> Installing system packages..."

case "${PKG_MANAGER}" in
    apt)
        # Remove CD-ROM apt source if present — common on fresh Debian installs
        # where the installer adds the install media as a repository.
        if grep -q '^deb cdrom:' /etc/apt/sources.list 2>/dev/null; then
            sed -i 's|^deb cdrom:|# deb cdrom:|g' /etc/apt/sources.list
            echo "    Disabled CD-ROM apt source in /etc/apt/sources.list"
        fi

        apt-get update -qq

        APT_PKGS=(
            python3
            python3-pip
            python3-venv
            python3-dev
            gcc
            libldap-dev
            libsasl2-dev
            libssl-dev
            nginx
            nodejs
            npm
            redis-server
            openssl
            openssh-client
            rsync
            wget
        )

        # qemu-utils is optional — disk conversion runs on Proxmox via SSH.
        # We install it on apt-based systems (when not on Proxmox itself) as a bonus
        # for local format detection.
        if [[ -f /usr/bin/pveversion ]]; then
            echo ""
            echo "  NOTE: Proxmox VE detected on this host."
            echo "  ProxMigrate is designed to run on a SEPARATE server that connects to Proxmox"
            echo "  via SSH and REST API. Installing here is supported but not recommended."
            echo ""
        else
            APT_PKGS+=(qemu-utils)
        fi

        apt-get install -y "${APT_PKGS[@]}" 2>/dev/null || true
        ;;

    dnf|yum)
        if [[ -f /usr/bin/pveversion ]]; then
            echo ""
            echo "  NOTE: Proxmox VE detected on this host."
            echo "  ProxMigrate is designed to run on a SEPARATE server that connects to Proxmox"
            echo "  via SSH and REST API. Installing here is supported but not recommended."
            echo ""
        fi

        "${PKG_MANAGER}" install -y epel-release 2>/dev/null || true
        "${PKG_MANAGER}" install -y \
            python3 \
            python3-pip \
            python3-devel \
            gcc \
            openldap-devel \
            cyrus-sasl-devel \
            openssl-devel \
            nginx \
            nodejs \
            npm \
            redis \
            openssl \
            openssh-clients \
            rsync \
            wget \
            2>/dev/null || true
        ;;

    zypper)
        if [[ -f /usr/bin/pveversion ]]; then
            echo ""
            echo "  NOTE: Proxmox VE detected on this host."
            echo "  ProxMigrate is designed to run on a SEPARATE server that connects to Proxmox"
            echo "  via SSH and REST API. Installing here is supported but not recommended."
            echo ""
        fi

        zypper install -y \
            python3 \
            python3-pip \
            python3-devel \
            gcc \
            openldap2-devel \
            cyrus-sasl-devel \
            libopenssl-devel \
            nginx \
            nodejs \
            npm \
            redis \
            openssl \
            openssh \
            rsync \
            wget \
            2>/dev/null || true
        ;;
esac

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

# Ensure the venv is owned by the app user so update.sh can install packages
chown -R "${APP_USER}:${APP_USER}" "${VENV}"

echo "==> Installing Python dependencies..."
sudo -u "${APP_USER}" "${PIP}" install --quiet --upgrade pip

# Air-gap support: if vendor/ directory is present and non-empty, install offline
if [[ -d "${SCRIPT_DIR}/vendor" ]] && [[ -n "$(ls -A "${SCRIPT_DIR}/vendor/" 2>/dev/null)" ]]; then
    echo "    Vendor directory detected — installing in offline mode."
    sudo -u "${APP_USER}" "${PIP}" install --quiet --no-index \
        --find-links "${SCRIPT_DIR}/vendor/" \
        -r "${APP_HOME}/requirements.txt"
else
    sudo -u "${APP_USER}" "${PIP}" install --quiet -r "${APP_HOME}/requirements.txt"
fi

echo "==> Installing frontend dependencies..."
if command -v npm &>/dev/null; then
    sudo -u "${APP_USER}" bash -c "cd ${APP_HOME} && npm install --omit=dev 2>&1 | tail -1"
else
    echo "    WARN: npm not found — skipping frontend asset install"
fi

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------

echo "==> Creating runtime directories..."
UPLOAD_ROOT="${APP_HOME}/uploads"
ACME_CHALLENGE_DIR="${CERTS_DIR}/acme-challenge"
mkdir -p "${UPLOAD_ROOT}" "${CERTS_DIR}" "${ACME_CHALLENGE_DIR}" "${SSH_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${UPLOAD_ROOT}" "${CERTS_DIR}" "${ACME_CHALLENGE_DIR}" "${SSH_DIR}"
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
# UPLOAD_TEMP_DIR=
# Where Django writes upload temp files during HTTP transfer.
# /tmp is often a small RAM-backed tmpfs — if you import large disk images
# (e.g. 15 GB qcow2) set this to a path on a disk with sufficient free space.
# The directory must exist and be writable by the proxmigrate user.
# Example: UPLOAD_TEMP_DIR=/data/proxmigrate/tmp
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

if [[ "${PKG_MANAGER}" == "apt" ]]; then
    # apt-based: sites-available + sites-enabled symlink
    NGINX_CONF_FILE="${NGINX_CONF_DIR}/proxmigrate"

    sed \
        -e "s|{{ WEB_PORT }}|${PORT}|g" \
        -e "s|{{ UPLOAD_ROOT }}|${UPLOAD_ROOT}|g" \
        "${APP_HOME}/deploy/nginx.conf.template" \
        > "${NGINX_CONF_FILE}"

    if [[ ! -L "${NGINX_ENABLED_DIR}/proxmigrate" ]]; then
        ln -s "${NGINX_CONF_FILE}" "${NGINX_ENABLED_DIR}/proxmigrate"
    fi

    # Remove default nginx site if it conflicts on port 80/443
    if [[ -L "${NGINX_ENABLED_DIR}/default" ]]; then
        rm -f "${NGINX_ENABLED_DIR}/default"
        echo "    Removed default nginx site."
    fi
else
    # dnf/yum/zypper: write directly to conf.d (no symlink needed)
    NGINX_CONF_FILE="${NGINX_CONF_DIR}/proxmigrate.conf"

    sed \
        -e "s|{{ WEB_PORT }}|${PORT}|g" \
        -e "s|{{ UPLOAD_ROOT }}|${UPLOAD_ROOT}|g" \
        "${APP_HOME}/deploy/nginx.conf.template" \
        > "${NGINX_CONF_FILE}"
fi

nginx -t 2>/dev/null && echo "    Nginx config valid."

# Allow proxmigrate user to reload nginx without a password (needed for VM console WebSocket proxy)
SUDOERS_FILE="/etc/sudoers.d/proxmigrate-nginx"
cat > "${SUDOERS_FILE}" <<EOF
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -s reload
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/sites-available/proxmigrate
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/proxmigrate.conf
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /opt/proxmigrate/deploy/acme-challenge.conf
EOF
chmod 440 "${SUDOERS_FILE}"
echo "    Sudoers rule written: ${SUDOERS_FILE}"

# SELinux configuration for RHEL/CentOS/Rocky-based installs
if [[ "${PKG_MANAGER}" =~ ^(dnf|yum)$ ]] && command -v getenforce &>/dev/null; then
    SELINUX_STATUS="$(getenforce 2>/dev/null || echo Disabled)"
    echo "==> SELinux status: ${SELINUX_STATUS}"

    if [[ "${SELINUX_STATUS}" == "Enforcing" || "${SELINUX_STATUS}" == "Permissive" ]]; then
        echo "    Applying SELinux policy for ProxMigrate..."

        # Allow nginx (httpd_t) to proxy to the gunicorn Unix socket
        setsebool -P httpd_can_network_connect 1
        echo "    Set httpd_can_network_connect = on"

        # Allow nginx to listen on the configured port if non-standard
        # (SELinux only knows about ports 80/443/8080 by default)
        if command -v semanage &>/dev/null; then
            if ! semanage port -l | grep -qw "${PORT}"; then
                semanage port -a -t http_port_t -p tcp "${PORT}"
                echo "    Registered port ${PORT} as http_port_t"
            else
                echo "    Port ${PORT} already known to SELinux"
            fi
        else
            echo "    WARNING: semanage not found — install policycoreutils-python-utils"
            echo "    Then run: semanage port -a -t http_port_t -p tcp ${PORT}"
        fi

        # Fix file contexts under /opt/proxmigrate
        if command -v restorecon &>/dev/null; then
            restorecon -R "${APP_HOME}"
            echo "    Restored SELinux file contexts on ${APP_HOME}"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Systemd units
# ---------------------------------------------------------------------------

echo "==> Installing systemd service units..."

GUNICORN_SERVICE="/etc/systemd/system/proxmigrate-gunicorn.service"
CELERY_SERVICE="/etc/systemd/system/proxmigrate-celery.service"
DAPHNE_SERVICE="/etc/systemd/system/proxmigrate-daphne.service"

cp "${APP_HOME}/deploy/gunicorn.service.template" "${GUNICORN_SERVICE}"
cp "${APP_HOME}/deploy/celery.service.template" "${CELERY_SERVICE}"
cp "${APP_HOME}/deploy/daphne.service.template" "${DAPHNE_SERVICE}"

# Create empty ACME challenge config (populated by ACME automation when needed)
touch "${APP_HOME}/deploy/acme-challenge.conf"
chown "${APP_USER}:${APP_USER}" "${APP_HOME}/deploy/acme-challenge.conf"

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
# Frontend vendor assets (Bulma CSS + FontAwesome)
# ---------------------------------------------------------------------------

echo "==> Downloading frontend assets..."
VENDOR_STATIC="${APP_HOME}/static/vendor"
mkdir -p "${VENDOR_STATIC}/css" "${VENDOR_STATIC}/webfonts"

# Bulma
if [[ ! -f "${VENDOR_STATIC}/css/bulma.min.css" ]]; then
    if wget -q --timeout=30 \
        "https://cdn.jsdelivr.net/npm/bulma@0.9.4/css/bulma.min.css" \
        -O "${VENDOR_STATIC}/css/bulma.min.css" 2>/dev/null; then
        echo "    Downloaded Bulma CSS."
    else
        echo "    WARNING: Could not download Bulma CSS (no internet?)"
        echo "    For air-gapped installs: place bulma.min.css at ${VENDOR_STATIC}/css/"
        # Write minimal placeholder so the app doesn't 404
        echo "/* Bulma not downloaded - place bulma.min.css here */" \
            > "${VENDOR_STATIC}/css/bulma.min.css"
    fi
fi

# FontAwesome
FA_VER="6.5.1"
FA_BASE="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/${FA_VER}"
if [[ ! -f "${VENDOR_STATIC}/css/all.min.css" ]]; then
    if wget -q --timeout=30 "${FA_BASE}/css/all.min.css" \
        -O "${VENDOR_STATIC}/css/all.min.css" 2>/dev/null; then
        for font in fa-brands-400 fa-regular-400 fa-solid-900 fa-v4compatibility; do
            wget -q --timeout=30 \
                "${FA_BASE}/webfonts/${font}.woff2" \
                -O "${VENDOR_STATIC}/webfonts/${font}.woff2" 2>/dev/null || true
        done
        # Fix CSS to reference local webfonts path
        sed -i 's|../webfonts/|/static/vendor/webfonts/|g' \
            "${VENDOR_STATIC}/css/all.min.css"
        echo "    Downloaded FontAwesome."
    else
        echo "    WARNING: Could not download FontAwesome (no internet?)"
        echo "    For air-gapped installs: place FA files at ${VENDOR_STATIC}/"
        echo "/* FontAwesome not downloaded - place all.min.css here */" \
            > "${VENDOR_STATIC}/css/all.min.css"
    fi
fi

chown -R "${APP_USER}:${APP_USER}" "${VENDOR_STATIC}"

# Re-run collectstatic to pick up vendor assets
sudo -u "${APP_USER}" \
    DJANGO_SETTINGS_MODULE=proxmigrate.settings.production \
    "${PYTHON}" "${APP_HOME}/manage.py" collectstatic --noinput \
    --settings=proxmigrate.settings.production \
    2>&1 | tail -2

# ---------------------------------------------------------------------------
# Admin superuser
# ---------------------------------------------------------------------------

echo "==> Creating admin user..."
echo ""

# Allow non-interactive install via environment variables
DEFAULT_ADMIN_PASS="Password!"
FORCE_CHANGE=false

if [[ -n "${PROXMIGRATE_ADMIN_USER:-}" && -n "${PROXMIGRATE_ADMIN_PASS:-}" ]]; then
    ADMIN_USER="${PROXMIGRATE_ADMIN_USER}"
    ADMIN_PASS="${PROXMIGRATE_ADMIN_PASS}"
    echo "    Using credentials from environment variables."
else
    read -rp "    Admin username [admin]: " ADMIN_USER
    ADMIN_USER="${ADMIN_USER:-admin}"

    echo "    Leave password blank to use default '${DEFAULT_ADMIN_PASS}' with forced change on first login."
    read -rsp "    Admin password: " ADMIN_PASS
    echo ""
    if [[ -z "${ADMIN_PASS}" ]]; then
        ADMIN_PASS="${DEFAULT_ADMIN_PASS}"
        FORCE_CHANGE=true
        echo "    Using default password — user will be required to change it on first login."
    else
        while true; do
            read -rsp "    Confirm password: " ADMIN_PASS2
            echo ""
            if [[ "${ADMIN_PASS}" == "${ADMIN_PASS2}" ]]; then
                break
            else
                echo "    Passwords do not match. Please try again."
                read -rsp "    Admin password: " ADMIN_PASS
                echo ""
            fi
        done
    fi
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

if [[ "${FORCE_CHANGE}" == "true" ]]; then
    sudo -u "${APP_USER}" \
        DJANGO_SETTINGS_MODULE=proxmigrate.settings.production \
        "${PYTHON}" "${APP_HOME}/manage.py" set_must_change_password "${ADMIN_USER}" \
        --settings=proxmigrate.settings.production \
        2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Enable and start services
# ---------------------------------------------------------------------------

echo "==> Enabling and starting services..."
systemctl daemon-reload

for SVC in "${REDIS_SVC}" nginx proxmigrate-gunicorn proxmigrate-celery proxmigrate-daphne; do
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
