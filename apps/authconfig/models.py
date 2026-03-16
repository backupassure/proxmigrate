import logging

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

logger = logging.getLogger(__name__)


class LDAPConfig(models.Model):
    """LDAP authentication backend configuration."""

    server_uri = models.CharField(
        max_length=500,
        default="ldap://ldap.example.com",
        verbose_name="LDAP Server URI",
    )
    bind_dn = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Bind DN",
    )
    bind_password = EncryptedCharField(
        max_length=500,
        blank=True,
        verbose_name="Bind Password",
    )
    user_search_base = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="User Search Base DN",
    )
    user_search_filter = models.CharField(
        max_length=200,
        default="(uid=%(user)s)",
        verbose_name="User Search Filter",
    )
    require_group = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Required Group DN",
        help_text="DN of group required for login. Leave blank to allow all users.",
    )
    admin_group = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Admin Group DN",
        help_text="DN of group whose members receive Django staff/superuser access.",
    )
    use_tls = models.BooleanField(
        default=False,
        verbose_name="Use TLS (STARTTLS)",
    )
    skip_cert_verify = models.BooleanField(
        default=False,
        verbose_name="Skip Certificate Verification",
        help_text="Disable TLS certificate verification (insecure — dev only).",
    )
    ca_cert = models.TextField(
        blank=True,
        verbose_name="CA Certificate (PEM)",
        help_text="Paste the PEM certificate of the CA or LDAP server to trust. Leave blank to use system CAs.",
    )
    is_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable LDAP Authentication",
    )

    @property
    def bind_password_set(self):
        return bool(self.bind_password)

    class Meta:
        verbose_name = "LDAP Configuration"

    def __str__(self):
        status = "enabled" if self.is_enabled else "disabled"
        return f"LDAPConfig({self.server_uri}, {status})"


class EntraIDConfig(models.Model):
    """Microsoft Entra ID (Azure AD) authentication backend configuration."""

    tenant_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Tenant ID",
    )
    client_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Application (Client) ID",
    )
    client_secret = EncryptedCharField(
        max_length=500,
        blank=True,
        verbose_name="Client Secret",
    )
    allowed_domains = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Allowed Domains",
        help_text="Comma-separated list of allowed email domains. Leave blank to allow any Microsoft account.",
    )
    admin_group_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Admin Group Object ID",
        help_text="Azure AD group Object ID whose members receive Django admin access.",
    )
    is_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable Entra ID Authentication",
    )

    @property
    def client_secret_set(self):
        return bool(self.client_secret)

    class Meta:
        verbose_name = "Entra ID Configuration"

    def __str__(self):
        status = "enabled" if self.is_enabled else "disabled"
        return f"EntraIDConfig(tenant={self.tenant_id}, {status})"
