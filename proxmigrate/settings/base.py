import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from the project root (or /opt/proxmigrate/.env in production)
load_dotenv(BASE_DIR / ".env")
load_dotenv("/opt/proxmigrate/.env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get("SECRET_KEY", "insecure-default-change-me")

DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")

_raw_hosts = os.environ.get("ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(",") if h.strip()]

SITE_ID = 1

# ---------------------------------------------------------------------------
# Installed apps
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # django-allauth
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.microsoft",
    # ProxMigrate apps
    "apps.core",
    "apps.wizard",
    "apps.proxmox",
    "apps.converter",
    "apps.importer",
    "apps.inventory",
    "apps.vmmanager",
    "apps.exporter",
    "apps.authconfig",
    "apps.certificates",
    "apps.vmcreator",
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
    "apps.core.middleware.WizardRedirectMiddleware",
]

# ---------------------------------------------------------------------------
# URL / WSGI
# ---------------------------------------------------------------------------

ROOT_URLCONF = "proxmigrate.urls"

WSGI_APPLICATION = "proxmigrate.wsgi.application"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_db_path = os.environ.get("DB_PATH", "/opt/proxmigrate/db.sqlite3")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _db_path,
    }
}

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

UPLOAD_ROOT = os.environ.get("UPLOAD_ROOT", "/opt/proxmigrate/uploads")
MEDIA_URL = "/media/"
MEDIA_ROOT = UPLOAD_ROOT

# Where Django writes large upload temp files before the view saves them.
# Defaults to the OS temp dir (/tmp on Linux, often a small tmpfs).
# Set UPLOAD_TEMP_DIR in .env to a path on a disk with enough free space
# when importing large images (e.g. a 15 GB qcow2 needs 15 GB here).
_upload_temp = os.environ.get("UPLOAD_TEMP_DIR", "")
if _upload_temp:
    FILE_UPLOAD_TEMP_DIR = _upload_temp

# ---------------------------------------------------------------------------
# Default primary key
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

# ---------------------------------------------------------------------------
# Encrypted fields
# ---------------------------------------------------------------------------

FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

# ---------------------------------------------------------------------------
# Authentication backends
# NOTE: LDAP and allauth backends are added dynamically by apps.authconfig
# ---------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

# ---------------------------------------------------------------------------
# django-allauth
# ---------------------------------------------------------------------------

ACCOUNT_EMAIL_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "username"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# Help system
# ---------------------------------------------------------------------------

HELP_DIR = BASE_DIR / "help"

# ---------------------------------------------------------------------------
# CSRF / Security
# ---------------------------------------------------------------------------

# Tell Django it is behind an SSL-terminating reverse proxy (nginx).
# Nginx sets X-Forwarded-Proto: https; without this, Django sees the gunicorn
# connection as plain HTTP and its CSRF referer check fails.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

WEB_PORT = int(os.environ.get("WEB_PORT", "8443"))
