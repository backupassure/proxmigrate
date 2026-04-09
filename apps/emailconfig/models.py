import logging

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

logger = logging.getLogger(__name__)


class EmailConfig(models.Model):
    """Singleton email delivery configuration.

    Supports two backends:
      - SMTP: standard mail server (Office 365, Gmail, Postfix, etc.)
      - Graph: Microsoft Graph API using client credentials (application mail send)
    """

    BACKEND_SMTP = "smtp"
    BACKEND_GRAPH = "graph"
    BACKEND_CHOICES = [
        (BACKEND_SMTP, "SMTP"),
        (BACKEND_GRAPH, "Microsoft Graph API"),
    ]

    is_enabled = models.BooleanField(default=False)
    backend_type = models.CharField(
        max_length=10,
        choices=BACKEND_CHOICES,
        default=BACKEND_SMTP,
    )
    from_email = models.CharField(
        max_length=255,
        blank=True,
        help_text="Sender address shown in outgoing email (e.g. proxorchestrator@example.com)",
    )

    # ── SMTP fields ──────────────────────────────────────────────────────────
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.IntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password = EncryptedCharField(max_length=500, blank=True)
    smtp_use_tls = models.BooleanField(default=True)
    smtp_use_ssl = models.BooleanField(default=False)

    # ── Microsoft Graph API fields ────────────────────────────────────────────
    graph_tenant_id = models.CharField(max_length=255, blank=True)
    graph_client_id = models.CharField(max_length=255, blank=True)
    graph_client_secret = EncryptedCharField(max_length=500, blank=True)

    class Meta:
        verbose_name = "Email Configuration"

    def __str__(self):
        return f"EmailConfig(backend={self.backend_type}, enabled={self.is_enabled})"

    @property
    def smtp_password_set(self):
        return bool(self.smtp_password)

    @property
    def graph_client_secret_set(self):
        return bool(self.graph_client_secret)

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config
