"""Configure authentication backends from database at startup and on change.

Called from AuthConfigConfig.ready() and from views after any save/toggle.
Must never raise — any exception is caught so Django can start even before
migrations have run.
"""

import logging
import os
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)

_BASE_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
_LDAP_CA_CERT_PATH = "/opt/proxmigrate/certs/ldap-ca.pem"


def load_auth_backends_from_db():
    try:
        from apps.authconfig.models import EntraIDConfig, LDAPConfig
    except Exception:
        return  # pre-migration first run

    backends = list(_BASE_BACKENDS)

    # --- LDAP ---
    try:
        ldap_config = LDAPConfig.objects.first()
        if ldap_config and ldap_config.is_enabled:
            _configure_ldap(ldap_config)
            backend = "django_auth_ldap.backend.LDAPBackend"
            if backend not in backends:
                backends.append(backend)
    except Exception as exc:
        logger.warning("load_auth_backends_from_db: LDAP error: %s", exc)

    # --- Entra ID ---
    try:
        entra_config = EntraIDConfig.objects.first()
        if entra_config and entra_config.is_enabled:
            _configure_entra(entra_config)
            backend = "allauth.account.auth_backends.AuthenticationBackend"
            if backend not in backends:
                backends.append(backend)
    except Exception as exc:
        logger.warning("load_auth_backends_from_db: Entra error: %s", exc)

    settings.AUTHENTICATION_BACKENDS = backends


def _configure_ldap(config):
    try:
        import ldap
        from django_auth_ldap.config import LDAPSearch
    except ImportError:
        logger.warning("_configure_ldap: django-auth-ldap / python-ldap not installed")
        return

    # TLS options must be set at the module level BEFORE any ldap.initialize()
    # call — AUTH_LDAP_GLOBAL_OPTIONS alone is applied too late for ldaps://
    # because TLS negotiation happens at connect time. OPT_X_TLS_NEWCTX forces
    # OpenLDAP to create a fresh TLS context with the updated settings.
    if config.skip_cert_verify:
        ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
        ldap.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
        settings.AUTH_LDAP_GLOBAL_OPTIONS = {
            ldap.OPT_X_TLS_REQUIRE_CERT: ldap.OPT_X_TLS_NEVER,
            ldap.OPT_X_TLS_NEWCTX: 0,
        }
    elif config.ca_cert:
        # Write the CA cert PEM to disk so OpenLDAP can load it
        try:
            os.makedirs(os.path.dirname(_LDAP_CA_CERT_PATH), exist_ok=True)
            with open(_LDAP_CA_CERT_PATH, "w") as f:
                f.write(config.ca_cert)
            ldap.set_option(ldap.OPT_X_TLS_CACERTFILE, _LDAP_CA_CERT_PATH)
            ldap.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
            settings.AUTH_LDAP_GLOBAL_OPTIONS = {
                ldap.OPT_X_TLS_CACERTFILE: _LDAP_CA_CERT_PATH,
                ldap.OPT_X_TLS_NEWCTX: 0,
            }
        except Exception as exc:
            logger.warning("_configure_ldap: failed to write CA cert: %s", exc)

    settings.AUTH_LDAP_SERVER_URI = config.server_uri
    settings.AUTH_LDAP_BIND_DN = config.bind_dn or ""
    settings.AUTH_LDAP_BIND_PASSWORD = config.bind_password or ""
    settings.AUTH_LDAP_USER_SEARCH = LDAPSearch(
        config.user_search_base or "",
        ldap.SCOPE_SUBTREE,
        config.user_search_filter or "(uid=%(user)s)",
    )
    settings.AUTH_LDAP_ALWAYS_UPDATE_USER = True

    # STARTTLS only applies to plain ldap:// — ldaps:// is already TLS
    if config.use_tls and not config.server_uri.lower().startswith("ldaps://"):
        settings.AUTH_LDAP_START_TLS = True

    if config.require_group:
        settings.AUTH_LDAP_REQUIRE_GROUP = config.require_group

    if config.admin_group:
        settings.AUTH_LDAP_USER_FLAGS_BY_GROUP = {
            "is_staff": config.admin_group,
            "is_superuser": config.admin_group,
        }


def _configure_entra(config):
    providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})
    providers["microsoft"] = {
        "APP": {
            "client_id": config.client_id,
            "secret": config.client_secret,
            "settings": {
                "tenant": config.tenant_id or "common",
            },
        },
        "SCOPE": ["User.Read"],
    }
    settings.SOCIALACCOUNT_PROVIDERS = providers
