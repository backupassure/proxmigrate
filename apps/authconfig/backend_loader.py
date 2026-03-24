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
        from django_auth_ldap.config import ActiveDirectoryGroupType, LDAPSearch
    except ImportError:
        logger.warning("_configure_ldap: django-auth-ldap / python-ldap not installed")
        return

    # TLS options must be set at the module level BEFORE any ldap.initialize()
    # call — ldaps:// TLS negotiation happens at connect time.
    # OPT_X_TLS_NEWCTX forces OpenLDAP to create a fresh TLS context NOW.
    # IMPORTANT: do NOT include OPT_X_TLS_NEWCTX in AUTH_LDAP_GLOBAL_OPTIONS —
    # django-auth-ldap re-applies that dict before every connection, and
    # re-triggering a TLS context rebuild mid-session drops the authenticated
    # bind state, causing OPERATIONS_ERROR on the subsequent search.
    # AD frequently sends LDAP referrals; following them unauthenticated causes
    # OPERATIONS_ERROR on the search. Disable referral chasing globally.
    ldap.set_option(ldap.OPT_REFERRALS, 0)

    if config.skip_cert_verify:
        ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
        ldap.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
        settings.AUTH_LDAP_GLOBAL_OPTIONS = {
            ldap.OPT_REFERRALS: 0,
            ldap.OPT_X_TLS_REQUIRE_CERT: ldap.OPT_X_TLS_NEVER,
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
                ldap.OPT_REFERRALS: 0,
                ldap.OPT_X_TLS_CACERTFILE: _LDAP_CA_CERT_PATH,
            }
        except Exception as exc:
            logger.warning("_configure_ldap: failed to write CA cert: %s", exc)
    else:
        settings.AUTH_LDAP_GLOBAL_OPTIONS = {
            ldap.OPT_REFERRALS: 0,
        }

    settings.AUTH_LDAP_SERVER_URI = config.server_uri
    settings.AUTH_LDAP_BIND_DN = config.bind_dn or ""
    settings.AUTH_LDAP_BIND_PASSWORD = config.bind_password or ""
    settings.AUTH_LDAP_USER_SEARCH = LDAPSearch(
        config.user_search_base or "",
        ldap.SCOPE_SUBTREE,
        config.user_search_filter or "(uid=%(user)s)",
    )
    settings.AUTH_LDAP_ALWAYS_UPDATE_USER = True

    # Sync common LDAP attributes to the Django user on each login.
    # This enables email-based login for LDAP users when their directory
    # has a mail attribute populated.
    settings.AUTH_LDAP_USER_ATTR_MAP = {
        "email": "mail",
        "first_name": "givenName",
        "last_name": "sn",
    }

    # STARTTLS only applies to plain ldap:// — ldaps:// is already TLS
    settings.AUTH_LDAP_START_TLS = (
        config.use_tls and not config.server_uri.lower().startswith("ldaps://")
    )

    if config.require_group or config.admin_group:
        # GROUP_TYPE and GROUP_SEARCH are required whenever group checks are configured.
        # Use the same base DN as the user search so all groups in the domain are visible.
        settings.AUTH_LDAP_GROUP_TYPE = ActiveDirectoryGroupType()
        settings.AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
            config.user_search_base or "",
            ldap.SCOPE_SUBTREE,
            "(objectClass=group)",
        )

    # Always set/clear these so stale values don't persist after config changes.
    # If admin_group is set but require_group is blank, restrict login to admin
    # group members only — it makes no sense to allow arbitrary LDAP users in
    # when you only want admins.
    effective_require = config.require_group or config.admin_group
    settings.AUTH_LDAP_REQUIRE_GROUP = effective_require or None

    if config.admin_group:
        settings.AUTH_LDAP_USER_FLAGS_BY_GROUP = {
            "is_staff": config.admin_group,
            "is_superuser": config.admin_group,
        }
    else:
        settings.AUTH_LDAP_USER_FLAGS_BY_GROUP = {}


def _configure_entra(config):
    providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})
    # Request GroupMember.Read.All whenever either group field is configured so
    # the pre_social_login handler can check group membership via the Graph API.
    scopes = ["User.Read"]
    require_group_id = getattr(config, "require_group_id", "")
    if (require_group_id or "").strip() or (config.admin_group_id or "").strip():
        scopes.append("GroupMember.Read.All")
    providers["microsoft"] = {
        "APP": {
            "client_id": config.client_id,
            "secret": config.client_secret,
            "settings": {
                "tenant": config.tenant_id or "common",
            },
        },
        "SCOPE": scopes,
    }
    settings.SOCIALACCOUNT_PROVIDERS = providers
