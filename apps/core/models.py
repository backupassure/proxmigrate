from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from encrypted_model_fields.fields import EncryptedCharField


class UserProfile(models.Model):
    """One-to-one extension of the built-in User model."""

    AUTH_SOURCE_LOCAL = "local"
    AUTH_SOURCE_LDAP = "ldap"
    AUTH_SOURCE_ENTRA = "entra"
    AUTH_SOURCE_CHOICES = [
        (AUTH_SOURCE_LOCAL, "Local"),
        (AUTH_SOURCE_LDAP, "LDAP"),
        (AUTH_SOURCE_ENTRA, "Entra ID"),
    ]

    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="profile",
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text="Force the user to set a new password on next login.",
    )
    auth_source = models.CharField(
        max_length=20,
        choices=AUTH_SOURCE_CHOICES,
        default=AUTH_SOURCE_LOCAL,
    )

    # MFA / TOTP fields
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = EncryptedCharField(max_length=64, blank=True, default="")
    mfa_recovery_codes = EncryptedCharField(max_length=500, blank=True, default="")
    mfa_confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Profile({self.user.username})"


class MFAConfig(models.Model):
    """Singleton: global MFA enforcement setting."""

    enforce_mfa = models.BooleanField(
        default=False,
        help_text="Require all local and LDAP users to set up MFA.",
    )
    allow_email_recovery = models.BooleanField(
        default=True,
        help_text="Allow users to receive a one-time MFA bypass code via email.",
    )

    class Meta:
        verbose_name = "MFA Configuration"

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    def __str__(self):
        return f"MFAConfig(enforce={self.enforce_mfa})"


@receiver(post_save, sender=get_user_model())
def _create_user_profile(sender, instance, created, **kwargs):
    """Auto-create a UserProfile whenever a new User is saved."""
    if created:
        UserProfile.objects.get_or_create(user=instance)
