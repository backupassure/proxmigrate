#!/usr/bin/env bash
# ProxOrchestrator updater
# Usage: sudo ./update.sh
#
# Run from the repo root after pulling the latest code.  Updates application
# files, installs any new Python/Node dependencies, runs database migrations,
# syncs systemd units (daphne), patches nginx for WebSocket support if needed,
# and restarts services.  Does NOT change TLS certs or SSH keys.
set -euo pipefail

APP_USER="proxorchestrator"
APP_HOME="/opt/proxorchestrator"
VENV="${APP_HOME}/venv"
PYTHON="${VENV}/bin/python"
PIP="${VENV}/bin/pip"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATED_FROM_PROXMIGRATE=false

# ---------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: update.sh must be run as root (sudo ./update.sh)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Migration: proxmigrate → proxorchestrator
# ---------------------------------------------------------------------------
OLD_HOME="/opt/proxmigrate"
OLD_USER="proxmigrate"
OLD_LOG="/var/log/proxmigrate"

if [[ -d "${OLD_HOME}" && ! -d "${APP_HOME}" ]]; then
    echo ""
    echo "============================================================"
    echo "  Migrating ProxMigrate → ProxOrchestrator"
    echo "============================================================"
    echo ""

    # 1. Create new system user
    echo "==> Creating system user '${APP_USER}'..."
    if ! id "${APP_USER}" &>/dev/null; then
        useradd --system --home "${APP_HOME}" --shell /sbin/nologin "${APP_USER}"
        echo "    User '${APP_USER}' created."
    fi

    # 2. Copy application data to new path
    echo "==> Copying ${OLD_HOME} → ${APP_HOME}..."
    cp -a "${OLD_HOME}" "${APP_HOME}"
    chown -R "${APP_USER}:${APP_USER}" "${APP_HOME}"
    echo "    Application data copied."

    # 3. Rename certificate files
    echo "==> Renaming certificate files..."
    CERT_DIR="${APP_HOME}/certs"
    for ext in crt key; do
        if [[ -f "${CERT_DIR}/proxmigrate.${ext}" ]]; then
            mv "${CERT_DIR}/proxmigrate.${ext}" "${CERT_DIR}/proxorchestrator.${ext}"
            echo "    proxmigrate.${ext} → proxorchestrator.${ext}"
        fi
    done
    if [[ -f "${CERT_DIR}/proxmigrate.csr.key" ]]; then
        mv "${CERT_DIR}/proxmigrate.csr.key" "${CERT_DIR}/proxorchestrator.csr.key"
        echo "    proxmigrate.csr.key → proxorchestrator.csr.key"
    fi

    # 4. Update .env file paths
    echo "==> Updating .env file..."
    ENV_FILE="${APP_HOME}/.env"
    if [[ -f "${ENV_FILE}" ]]; then
        sed -i 's|/opt/proxmigrate|/opt/proxorchestrator|g' "${ENV_FILE}"
        chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
        echo "    .env paths updated."
    fi

    # 5. Create new log and runtime directories
    echo "==> Creating directories..."
    mkdir -p /var/log/proxorchestrator /run/proxorchestrator
    chown "${APP_USER}:${APP_USER}" /var/log/proxorchestrator /run/proxorchestrator
    # Copy existing logs if present
    if [[ -d "${OLD_LOG}" ]]; then
        cp -a "${OLD_LOG}"/* /var/log/proxorchestrator/ 2>/dev/null || true
        chown -R "${APP_USER}:${APP_USER}" /var/log/proxorchestrator
    fi

    # 6. Stop old services
    echo "==> Stopping old services..."
    for svc in proxmigrate-gunicorn proxmigrate-celery proxmigrate-daphne; do
        systemctl stop "${svc}" 2>/dev/null || true
        systemctl disable "${svc}" 2>/dev/null || true
    done

    # 7. Remove old service files
    rm -f /etc/systemd/system/proxmigrate-gunicorn.service
    rm -f /etc/systemd/system/proxmigrate-celery.service
    rm -f /etc/systemd/system/proxmigrate-daphne.service

    # 8. Remove old nginx config (new config generated after rsync below)
    echo "==> Removing old nginx configuration..."
    rm -f /etc/nginx/sites-enabled/proxmigrate
    rm -f /etc/nginx/sites-available/proxmigrate
    rm -f /etc/nginx/conf.d/proxmigrate.conf

    # 9. Update sudoers
    echo "==> Updating sudoers rules..."
    rm -f /etc/sudoers.d/proxmigrate-nginx
    cat > /etc/sudoers.d/proxorchestrator-nginx <<SUDOERS
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -s reload
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/sites-available/proxorchestrator
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/proxorchestrator.conf
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /opt/proxorchestrator/deploy/acme-challenge.conf
SUDOERS
    chmod 440 /etc/sudoers.d/proxorchestrator-nginx

    # 10. Rename the old Django project directory inside the app
    if [[ -d "${APP_HOME}/proxmigrate" && ! -d "${APP_HOME}/proxorchestrator" ]]; then
        mv "${APP_HOME}/proxmigrate" "${APP_HOME}/proxorchestrator"
        echo "    Django project directory renamed."
    fi

    # 11. Update internal references in copied Python/config files
    echo "==> Updating internal references..."
    # Settings module references
    find "${APP_HOME}" -name "*.py" -exec sed -i \
        -e 's|proxmigrate\.settings|proxorchestrator.settings|g' \
        -e 's|proxmigrate\.wsgi|proxorchestrator.wsgi|g' \
        -e 's|proxmigrate\.asgi|proxorchestrator.asgi|g' \
        -e 's|proxmigrate\.urls|proxorchestrator.urls|g' \
        -e 's|proxmigrate\.celery|proxorchestrator.celery|g' \
        -e 's|Celery("proxmigrate")|Celery("proxorchestrator")|g' \
        -e 's|/opt/proxmigrate|/opt/proxorchestrator|g' \
        -e 's|/var/tmp/proxmigrate|/var/tmp/proxorchestrator|g' \
        -e 's|/var/log/proxmigrate|/var/log/proxorchestrator|g' \
        -e 's|proxmigrate\.crt|proxorchestrator.crt|g' \
        -e 's|proxmigrate\.key|proxorchestrator.key|g' \
        -e 's|proxmigrate\.csr\.key|proxorchestrator.csr.key|g' \
        -e 's|proxmigrate\.local|proxorchestrator.local|g' \
        -e 's|sites-available/proxmigrate|sites-available/proxorchestrator|g' \
        -e 's|proxmigrate\.conf|proxorchestrator.conf|g' \
        {} + 2>/dev/null || true

    # Rename CSS file if needed
    if [[ -f "${APP_HOME}/static/css/proxmigrate.css" && ! -f "${APP_HOME}/static/css/proxorchestrator.css" ]]; then
        mv "${APP_HOME}/static/css/proxmigrate.css" "${APP_HOME}/static/css/proxorchestrator.css"
    fi

    # Update template references to CSS file
    find "${APP_HOME}/templates" -name "*.html" -exec sed -i \
        -e 's|proxmigrate\.css|proxorchestrator.css|g' \
        -e 's|proxmigrate-card|proxorchestrator-card|g' \
        -e 's|proxmigrate-sidebar|proxorchestrator-sidebar|g' \
        -e 's|proxmigrate_theme|proxorchestrator_theme|g' \
        -e 's|ProxMigrate|ProxOrchestrator|g' \
        -e 's|/opt/proxmigrate|/opt/proxorchestrator|g' \
        {} + 2>/dev/null || true

    # Update CSS class names
    if [[ -f "${APP_HOME}/static/css/proxorchestrator.css" ]]; then
        sed -i \
            -e 's|proxmigrate-card|proxorchestrator-card|g' \
            -e 's|proxmigrate-sidebar|proxorchestrator-sidebar|g' \
            -e 's|ProxMigrate|ProxOrchestrator|g' \
            "${APP_HOME}/static/css/proxorchestrator.css"
    fi

    # Update help files
    find "${APP_HOME}/help" -name "*.md" -exec sed -i \
        -e 's|ProxMigrate|ProxOrchestrator|g' \
        -e 's|/opt/proxmigrate|/opt/proxorchestrator|g' \
        -e 's|proxmigrate-gunicorn|proxorchestrator-gunicorn|g' \
        -e 's|proxmigrate-celery|proxorchestrator-celery|g' \
        -e 's|proxmigrate-daphne|proxorchestrator-daphne|g' \
        {} + 2>/dev/null || true

    # Update email templates
    find "${APP_HOME}/templates" -name "*.txt" -exec sed -i \
        -e 's|ProxMigrate|ProxOrchestrator|g' \
        {} + 2>/dev/null || true

    chown -R "${APP_USER}:${APP_USER}" "${APP_HOME}"

    # 12. Service files will be installed after rsync brings in new templates

    MIGRATED_FROM_PROXMIGRATE=true

    echo ""
    echo "  Migration complete. Old installation preserved at ${OLD_HOME}."
    echo "  Once you verify everything works, you can remove it:"
    echo "    sudo rm -rf ${OLD_HOME}"
    echo ""
    echo "============================================================"
    echo ""
fi

# If the new path already exists, proceed with the normal update
echo "==> Copying application files to ${APP_HOME}..."
COPY_DIRS=(apps proxorchestrator templates static help deploy scripts)
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

# ---------------------------------------------------------------------------
# Regenerate nginx config after migration (uses new template from rsync)
# ---------------------------------------------------------------------------
if [[ "${MIGRATED_FROM_PROXMIGRATE:-}" == "true" ]]; then
    echo "==> Generating nginx config from new template..."
    ENV_FILE="${APP_HOME}/.env"
    WEB_PORT=$(grep -oP 'WEB_PORT=\K.*' "${ENV_FILE}" 2>/dev/null || echo 8443)

    if [[ -d "/etc/nginx/sites-available" ]]; then
        NGINX_CONF_FILE="/etc/nginx/sites-available/proxorchestrator"
        sed \
            -e "s|{{ WEB_PORT }}|${WEB_PORT}|g" \
            -e "s|{{ UPLOAD_ROOT }}|${APP_HOME}/uploads|g" \
            "${APP_HOME}/deploy/nginx.conf.template" \
            > "${NGINX_CONF_FILE}"
        ln -sf "${NGINX_CONF_FILE}" /etc/nginx/sites-enabled/proxorchestrator
    else
        NGINX_CONF_FILE="/etc/nginx/conf.d/proxorchestrator.conf"
        sed \
            -e "s|{{ WEB_PORT }}|${WEB_PORT}|g" \
            -e "s|{{ UPLOAD_ROOT }}|${APP_HOME}/uploads|g" \
            "${APP_HOME}/deploy/nginx.conf.template" \
            > "${NGINX_CONF_FILE}"
    fi
    nginx -t 2>/dev/null && echo "    Nginx config valid." && systemctl reload nginx && echo "    Nginx reloaded."
fi

# ---------------------------------------------------------------------------
# Fix stale nginx config that still references old proxmigrate paths
# ---------------------------------------------------------------------------
NGINX_LIVE=""
for p in /etc/nginx/sites-enabled/proxorchestrator /etc/nginx/sites-available/proxorchestrator /etc/nginx/conf.d/proxorchestrator.conf; do
    if [[ -f "${p}" ]]; then
        NGINX_LIVE="${p}"
        break
    fi
done

# ---------------------------------------------------------------------------
# Clean up old proxmigrate services if they exist
# ---------------------------------------------------------------------------
OLD_SVCS_FOUND=false
for old_svc in proxmigrate-gunicorn proxmigrate-celery proxmigrate-daphne; do
    if [[ -f "/etc/systemd/system/${old_svc}.service" ]]; then
        OLD_SVCS_FOUND=true
        systemctl stop "${old_svc}" 2>/dev/null || true
        systemctl disable "${old_svc}" 2>/dev/null || true
        rm -f "/etc/systemd/system/${old_svc}.service"
    fi
done
if [[ "${OLD_SVCS_FOUND}" == "true" ]]; then
    systemctl daemon-reload
    echo "==> Removed old proxmigrate service files."
fi

# Fix stale systemd service files that still reference old proxmigrate paths
GUNICORN_SVC="/etc/systemd/system/proxorchestrator-gunicorn.service"
if [[ -f "${GUNICORN_SVC}" ]] && grep -q "proxmigrate" "${GUNICORN_SVC}" 2>/dev/null; then
    echo "==> Fixing stale systemd service files (still reference proxmigrate)..."
    cp "${APP_HOME}/deploy/gunicorn.service.template" /etc/systemd/system/proxorchestrator-gunicorn.service
    cp "${APP_HOME}/deploy/celery.service.template" /etc/systemd/system/proxorchestrator-celery.service
    cp "${APP_HOME}/deploy/daphne.service.template" /etc/systemd/system/proxorchestrator-daphne.service
    systemctl daemon-reload
    echo "    Service files updated from new templates."
fi

if [[ -n "${NGINX_LIVE}" ]] && grep -q "proxmigrate" "${NGINX_LIVE}" 2>/dev/null; then
    echo "==> Fixing stale nginx config (still references proxmigrate)..."
    ENV_FILE="${APP_HOME}/.env"
    WEB_PORT=$(grep -oP 'WEB_PORT=\K.*' "${ENV_FILE}" 2>/dev/null || echo 8443)

    if [[ -d "/etc/nginx/sites-available" ]]; then
        sed \
            -e "s|{{ WEB_PORT }}|${WEB_PORT}|g" \
            -e "s|{{ UPLOAD_ROOT }}|${APP_HOME}/uploads|g" \
            "${APP_HOME}/deploy/nginx.conf.template" \
            > /etc/nginx/sites-available/proxorchestrator
        ln -sf /etc/nginx/sites-available/proxorchestrator /etc/nginx/sites-enabled/proxorchestrator
    else
        sed \
            -e "s|{{ WEB_PORT }}|${WEB_PORT}|g" \
            -e "s|{{ UPLOAD_ROOT }}|${APP_HOME}/uploads|g" \
            "${APP_HOME}/deploy/nginx.conf.template" \
            > /etc/nginx/conf.d/proxorchestrator.conf
    fi
    # Remove old sudoers and install new
    rm -f /etc/sudoers.d/proxmigrate-nginx
    cat > /etc/sudoers.d/proxorchestrator-nginx <<SUDOERS
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -s reload
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/sites-available/proxorchestrator
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/proxorchestrator.conf
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /opt/proxorchestrator/deploy/acme-challenge.conf
SUDOERS
    chmod 440 /etc/sudoers.d/proxorchestrator-nginx
    nginx -t 2>/dev/null && systemctl reload nginx && echo "    Nginx config fixed and reloaded."
fi

echo "==> Installing/upgrading Python dependencies..."
sudo -u "${APP_USER}" "${PIP}" install -q -r "${APP_HOME}/requirements.txt"

echo "==> Running database migrations..."
sudo -u "${APP_USER}" \
    DJANGO_SETTINGS_MODULE=proxorchestrator.settings.production \
    "${PYTHON}" "${APP_HOME}/manage.py" migrate --noinput \
    --settings=proxorchestrator.settings.production

echo "==> Installing frontend dependencies..."
if command -v npm &>/dev/null; then
    if sudo -u "${APP_USER}" bash -c "cd ${APP_HOME} && npm install --omit=dev 2>/dev/null | tail -1"; then
        echo "    npm dependencies installed."
    else
        echo "    npm install failed (non-fatal) — vendored static files will be used."
    fi
else
    echo "    npm not found — using vendored static files."
fi

echo "==> Collecting static files..."
sudo -u "${APP_USER}" \
    DJANGO_SETTINGS_MODULE=proxorchestrator.settings.production \
    "${PYTHON}" "${APP_HOME}/manage.py" collectstatic --noinput \
    --settings=proxorchestrator.settings.production

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
for p in /etc/nginx/sites-enabled/proxorchestrator /etc/nginx/sites-available/proxorchestrator /etc/nginx/conf.d/proxorchestrator.conf; do
    if [[ -f "${p}" ]]; then
        NGINX_CONF="${p}"
        break
    fi
done

if [[ -n "${NGINX_CONF}" ]] && ! grep -q "acme-challenge.conf" "${NGINX_CONF}" 2>/dev/null; then
    echo "" >> "${NGINX_CONF}"
    echo "# ACME HTTP-01 challenge server (managed by ProxOrchestrator)" >> "${NGINX_CONF}"
    echo "include ${ACME_CONF};" >> "${NGINX_CONF}"
    echo "    Added ACME include to ${NGINX_CONF}"
    nginx -t 2>/dev/null && nginx -s reload 2>/dev/null && echo "    nginx reloaded"
fi

# Add ACME sudoers rule if not present
SUDOERS_FILE="/etc/sudoers.d/proxorchestrator-nginx"
if [[ -f "${SUDOERS_FILE}" ]] && ! grep -q "acme-challenge" "${SUDOERS_FILE}" 2>/dev/null; then
    echo "${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee ${ACME_CONF}" >> "${SUDOERS_FILE}"
    echo "    Added ACME sudoers rule"
fi

# Update celery service to include Beat scheduler (-B flag)
CELERY_SERVICE="/etc/systemd/system/proxorchestrator-celery.service"
if [[ -f "${CELERY_SERVICE}" ]] && ! grep -q "\-B" "${CELERY_SERVICE}" 2>/dev/null; then
    cp "${APP_HOME}/deploy/celery.service.template" "${CELERY_SERVICE}"
    systemctl daemon-reload
    echo "    Updated celery service with Beat scheduler"
fi

# ---------------------------------------------------------------------------
# Daphne WebSocket service
# ---------------------------------------------------------------------------
echo "==> Updating Daphne WebSocket service..."
cp "${APP_HOME}/deploy/daphne.service.template" /etc/systemd/system/proxorchestrator-daphne.service
systemctl daemon-reload
systemctl enable proxorchestrator-daphne 2>/dev/null
echo "    Daphne service installed."

# Ensure gunicorn service also preserves the shared RuntimeDirectory
if [[ -f /etc/systemd/system/proxorchestrator-gunicorn.service ]] && ! grep -q 'RuntimeDirectoryPreserve' /etc/systemd/system/proxorchestrator-gunicorn.service 2>/dev/null; then
    sed -i '/^RuntimeDirectoryMode=/a RuntimeDirectoryPreserve=yes' \
        /etc/systemd/system/proxorchestrator-gunicorn.service
    systemctl daemon-reload
    echo "    Added RuntimeDirectoryPreserve to gunicorn service."
fi

# Auto-update nginx config with WebSocket proxy block if missing
if [[ -n "$NGINX_CONF" ]] && ! grep -q 'proxorchestrator_ws' "$NGINX_CONF"; then
    echo "==> Adding WebSocket proxy config to nginx..."
    # Add the daphne upstream block after the gunicorn upstream
    sed -i '/^upstream proxorchestrator_app {/,/^}/{
        /^}/a\
\
upstream proxorchestrator_ws {\
    server unix:\/run\/proxorchestrator\/daphne.sock fail_timeout=0;\
}
    }' "$NGINX_CONF"
    # Add the /ws/ location block before the catch-all location /
    sed -i '/location \/ {/i\
    location /ws/ {\
        proxy_pass          http://proxorchestrator_ws;\
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

# ---------------------------------------------------------------------------
# Ensure service files exist (install them if missing)
# ---------------------------------------------------------------------------
for svc_pair in \
    "proxorchestrator-gunicorn:gunicorn.service.template" \
    "proxorchestrator-celery:celery.service.template" \
    "proxorchestrator-daphne:daphne.service.template"; do
    svc_name="${svc_pair%%:*}"
    tpl_name="${svc_pair##*:}"
    svc_file="/etc/systemd/system/${svc_name}.service"
    if [[ ! -f "${svc_file}" ]]; then
        echo "==> Installing missing service: ${svc_name}..."
        cp "${APP_HOME}/deploy/${tpl_name}" "${svc_file}"
        systemctl daemon-reload
        systemctl enable "${svc_name}" 2>/dev/null || true
    fi
done

echo "==> Restarting services..."
systemctl restart proxorchestrator-gunicorn proxorchestrator-celery
sleep 2
systemctl restart proxorchestrator-daphne
systemctl is-active proxorchestrator-gunicorn proxorchestrator-celery proxorchestrator-daphne

echo ""
echo "ProxOrchestrator updated successfully."
