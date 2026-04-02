import logging

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

logger = logging.getLogger(__name__)

PROVIDER_LETSENCRYPT = "letsencrypt"
PROVIDER_LETSENCRYPT_STAGING = "letsencrypt_staging"
PROVIDER_CUSTOM = "custom"

PROVIDER_CHOICES = [
    (PROVIDER_LETSENCRYPT, "Let's Encrypt"),
    (PROVIDER_LETSENCRYPT_STAGING, "Let's Encrypt (Staging)"),
    (PROVIDER_CUSTOM, "Custom / Internal CA"),
]

CHALLENGE_HTTP01 = "http-01"
CHALLENGE_DNS01 = "dns-01"

CHALLENGE_CHOICES = [
    (CHALLENGE_HTTP01, "HTTP-01"),
    (CHALLENGE_DNS01, "DNS-01"),
]

DNS_PROVIDER_NONE = "none"
DNS_PROVIDER_CLOUDFLARE = "cloudflare"
DNS_PROVIDER_ROUTE53 = "route53"
DNS_PROVIDER_AZURE = "azure"
DNS_PROVIDER_GODADDY = "godaddy"
DNS_PROVIDER_DIGITALOCEAN = "digitalocean"
DNS_PROVIDER_MANUAL = "manual"

DNS_PROVIDER_CHOICES = [
    (DNS_PROVIDER_NONE, "None (HTTP-01 only)"),
    (DNS_PROVIDER_CLOUDFLARE, "Cloudflare"),
    (DNS_PROVIDER_ROUTE53, "AWS Route 53"),
    (DNS_PROVIDER_AZURE, "Azure DNS"),
    (DNS_PROVIDER_GODADDY, "GoDaddy"),
    (DNS_PROVIDER_DIGITALOCEAN, "DigitalOcean"),
    (DNS_PROVIDER_MANUAL, "Manual (email TXT record to admins)"),
]

DIRECTORY_URLS = {
    PROVIDER_LETSENCRYPT: "https://acme-v02.api.letsencrypt.org/directory",
    PROVIDER_LETSENCRYPT_STAGING: "https://acme-staging-v02.api.letsencrypt.org/directory",
}


class AcmeConfig(models.Model):
    """Singleton ACME certificate automation configuration."""

    is_enabled = models.BooleanField(default=False)

    provider = models.CharField(
        max_length=30,
        choices=PROVIDER_CHOICES,
        default=PROVIDER_LETSENCRYPT,
    )
    directory_url = models.CharField(
        max_length=500,
        default=DIRECTORY_URLS[PROVIDER_LETSENCRYPT],
        help_text="ACME directory URL for the certificate authority.",
    )
    domain = models.CharField(
        max_length=255,
        blank=True,
        help_text="Fully qualified domain name for the certificate.",
    )
    email = models.EmailField(
        blank=True,
        help_text="Contact email for the ACME account. Required by Let's Encrypt.",
    )
    challenge_type = models.CharField(
        max_length=10,
        choices=CHALLENGE_CHOICES,
        default=CHALLENGE_HTTP01,
    )

    # DNS provider for automated DNS-01 challenges
    dns_provider = models.CharField(
        max_length=20,
        choices=DNS_PROVIDER_CHOICES,
        default=DNS_PROVIDER_NONE,
    )
    dns_api_token = EncryptedCharField(
        max_length=500, blank=True,
        help_text="API token for the DNS provider.",
    )
    dns_api_secret = EncryptedCharField(
        max_length=500, blank=True,
        help_text="API secret (Route 53 secret key, GoDaddy API secret).",
    )
    dns_zone_id = models.CharField(
        max_length=255, blank=True,
        help_text="Zone ID or hosted zone ID (Cloudflare, Route 53, Azure).",
    )

    # ACME account credentials (encrypted at rest)
    acme_account_key_pem = EncryptedCharField(max_length=2000, blank=True)
    acme_account_url = models.CharField(max_length=500, blank=True)

    # Internal CA TLS settings
    ca_bundle = models.TextField(
        blank=True,
        help_text="PEM-encoded CA certificate for verifying the ACME server's TLS.",
    )
    skip_tls_verify = models.BooleanField(
        default=False,
        help_text="Disable TLS verification for the ACME server. Testing only.",
    )

    # DNS-01 transient state
    dns_txt_value = models.CharField(max_length=500, blank=True)
    dns_challenge_pending = models.BooleanField(default=False)

    # Saved order/challenge state (so the task can resume the view's order)
    pending_order_url = models.CharField(max_length=500, blank=True)
    pending_challenge_url = models.CharField(max_length=500, blank=True)

    # Issuance tracking
    issuing_in_progress = models.BooleanField(default=False)
    issuing_stage = models.CharField(max_length=100, blank=True)

    # Renewal tracking
    last_renewed_at = models.DateTimeField(null=True, blank=True)
    last_renewal_error = models.TextField(blank=True)

    # Notification flags (reset on successful renewal)
    notify_30_sent = models.BooleanField(default=False)
    notify_14_sent = models.BooleanField(default=False)
    notify_7_sent = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ACME Configuration"

    def __str__(self):
        return f"AcmeConfig(provider={self.provider}, domain={self.domain})"

    @classmethod
    def get_config(cls):
        """Return the singleton instance, creating it if needed."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def get_verify(self):
        """Return the `verify` parameter for requests calls.

        Returns False (skip), a temp file path (custom CA bundle), or True (default).
        """
        if self.skip_tls_verify:
            return False
        if self.ca_bundle.strip():
            return self.ca_bundle.strip()
        return True


class AcmeLog(models.Model):
    """Audit trail for ACME certificate operations."""

    event = models.CharField(max_length=50)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AcmeLog({self.event}, {self.created_at})"

    @classmethod
    def log(cls, event, detail=""):
        """Create a log entry."""
        logger.info("ACME %s: %s", event, detail)
        return cls.objects.create(event=event, detail=detail)
