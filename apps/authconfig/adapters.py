"""Social account adapter for Entra ID / Microsoft login.

Handles two scenarios:
1. Existing user with matching email — connect the Microsoft account
   to the existing Django user (no signup form)
2. New user — auto-create a Django user from the Microsoft account
   data (no signup form)
"""

from django.contrib.auth.models import User

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class AutoSignupSocialAdapter(DefaultSocialAccountAdapter):

    def is_auto_signup_allowed(self, request, sociallogin):
        """Always allow auto-signup — skip the signup form."""
        return True

    def pre_social_login(self, request, sociallogin):
        """Connect Microsoft account to existing user if email matches.

        Without this, allauth shows a signup form when a user with
        the same email already exists but has no linked social account.
        """
        if sociallogin.is_existing:
            return

        email = sociallogin.account.extra_data.get("mail") or ""
        if not email:
            email = sociallogin.account.extra_data.get("userPrincipalName") or ""

        if email:
            try:
                user = User.objects.get(email__iexact=email)
                sociallogin.connect(request, user)
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                pass

    def populate_user(self, request, sociallogin, data):
        """Generate a username from the Microsoft account data."""
        user = super().populate_user(request, sociallogin, data)

        if not user.username:
            email = data.get("email", "") or ""
            if email:
                user.username = email.split("@")[0]
            else:
                user.username = f"entra_{sociallogin.account.uid[:8]}"

        return user
