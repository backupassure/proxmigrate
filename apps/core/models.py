from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


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

    def __str__(self):
        return f"Profile({self.user.username})"


@receiver(post_save, sender=get_user_model())
def _create_user_profile(sender, instance, created, **kwargs):
    """Auto-create a UserProfile whenever a new User is saved."""
    if created:
        UserProfile.objects.get_or_create(user=instance)
