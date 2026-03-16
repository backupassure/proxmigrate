from django.apps import AppConfig


class AuthConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.authconfig"
    label = "authconfig"

    def ready(self):
        """Load auth backends and connect auth-source tracking signals."""
        try:
            from .backend_loader import load_auth_backends_from_db
            load_auth_backends_from_db()
        except Exception:
            # Database may not exist yet (first run / pre-migration).
            pass

        self._connect_signals()

    def _connect_signals(self):
        # LDAP: mark auth_source='ldap' whenever django-auth-ldap populates a user.
        try:
            from django_auth_ldap.backend import populate_user

            def _ldap_auth_source(sender, user, ldap_user, **kwargs):
                from apps.core.models import UserProfile
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if profile.auth_source != UserProfile.AUTH_SOURCE_LDAP:
                    profile.auth_source = UserProfile.AUTH_SOURCE_LDAP
                    profile.save(update_fields=["auth_source"])

            populate_user.connect(_ldap_auth_source, weak=False)
        except Exception:
            pass

        # Entra ID: mark auth_source='entra' on first and subsequent Microsoft logins.
        try:
            from allauth.socialaccount.signals import social_account_added, social_account_updated

            def _entra_auth_source(sender, request, sociallogin, **kwargs):
                try:
                    if sociallogin.account.provider == "microsoft":
                        from apps.core.models import UserProfile
                        profile, _ = UserProfile.objects.get_or_create(user=sociallogin.user)
                        if profile.auth_source != UserProfile.AUTH_SOURCE_ENTRA:
                            profile.auth_source = UserProfile.AUTH_SOURCE_ENTRA
                            profile.save(update_fields=["auth_source"])
                except Exception:
                    import logging as _logging
                    _logging.getLogger(__name__).exception("_entra_auth_source signal failed")

            social_account_added.connect(_entra_auth_source, weak=False)
            social_account_updated.connect(_entra_auth_source, weak=False)
        except Exception:
            pass
