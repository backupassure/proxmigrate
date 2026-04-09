import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class EmailConfigApp(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.emailconfig"
    label = "emailconfig"
    verbose_name = "Email Configuration"

    def ready(self):
        self._load_email_config()

    def _load_email_config(self):
        """Read EmailConfig from DB and configure Django's email settings."""
        try:
            from django.conf import settings as django_settings
            from .models import EmailConfig

            config = EmailConfig.objects.first()
            if not config or not config.is_enabled:
                return

            django_settings.DEFAULT_FROM_EMAIL = config.from_email or "noreply@proxorchestrator.local"

            if config.backend_type == EmailConfig.BACKEND_SMTP:
                django_settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
                django_settings.EMAIL_HOST = config.smtp_host
                django_settings.EMAIL_PORT = config.smtp_port
                django_settings.EMAIL_HOST_USER = config.smtp_username
                django_settings.EMAIL_HOST_PASSWORD = config.smtp_password
                django_settings.EMAIL_USE_TLS = config.smtp_use_tls
                django_settings.EMAIL_USE_SSL = config.smtp_use_ssl
            elif config.backend_type == EmailConfig.BACKEND_GRAPH:
                django_settings.EMAIL_BACKEND = "apps.emailconfig.graph_backend.GraphEmailBackend"

        except Exception:
            # DB may not exist yet (first run / pre-migration)
            pass
