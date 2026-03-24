"""Social account adapter for Entra ID / Microsoft login.

Bypasses the allauth signup form by auto-generating a username from
the Microsoft account email or UPN. Without this, allauth shows a
signup form when it can't determine a unique username automatically.
"""

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class AutoSignupSocialAdapter(DefaultSocialAccountAdapter):

    def is_auto_signup_allowed(self, request, sociallogin):
        """Always allow auto-signup — skip the signup form."""
        return True

    def populate_user(self, request, sociallogin, data):
        """Generate a username from the Microsoft account data."""
        user = super().populate_user(request, sociallogin, data)

        if not user.username:
            # Try email prefix, then UPN, then fallback
            email = data.get("email", "") or ""
            if email:
                user.username = email.split("@")[0]
            else:
                # Use the social account UID (Microsoft object ID)
                user.username = f"entra_{sociallogin.account.uid[:8]}"

        return user
