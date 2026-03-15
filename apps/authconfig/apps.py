from django.apps import AppConfig


class AuthConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.authconfig"
    label = "authconfig"

    def ready(self):
        """Load auth backends stored in the database into Django's settings.

        Wrapped in a broad try/except so that the first run (before migrations
        have been applied) does not crash the Django startup sequence.
        """
        try:
            from .backend_loader import load_auth_backends_from_db
            load_auth_backends_from_db()
        except Exception:
            # Database may not exist yet (first run / pre-migration).
            pass
