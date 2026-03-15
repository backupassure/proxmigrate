"""Load authentication backends from the database into Django settings.

Called from AuthConfigConfig.ready().  Must never raise — any exception is
caught by the caller so Django can start even before migrations have run.
"""

from django.conf import settings


def load_auth_backends_from_db():
    """Append any enabled authentication backends stored in the DB.

    The base backend list (ModelBackend) is defined in settings.base and is
    always present.  This function reads the AuthBackendConfig model and
    appends LDAP / allauth backends that have been enabled by the operator
    through the UI.
    """
    try:
        from apps.authconfig.models import AuthBackendConfig  # noqa: PLC0415
    except Exception:
        # Models not yet available (pre-migration first run).
        return

    try:
        configs = AuthBackendConfig.objects.filter(enabled=True)
    except Exception:
        # Table may not exist yet.
        return

    backends = list(settings.AUTHENTICATION_BACKENDS)

    for config in configs:
        if config.backend_path and config.backend_path not in backends:
            backends.append(config.backend_path)

    settings.AUTHENTICATION_BACKENDS = backends
