import logging

from django.shortcuts import redirect

logger = logging.getLogger(__name__)

# Paths that bypass the wizard redirect check
EXEMPT_PREFIXES = (
    "/wizard/",
    "/login/",
    "/logout/",
    "/accounts/",
    "/static/",
    "/admin/",
    "/help/",
    "/api/",
    "/change-password/",
    "/forgot-password/",
    "/reset-password/",
    "/mfa/",
)


class WizardRedirectMiddleware:
    """Redirect authenticated users to the setup wizard if configuration is incomplete.

    Exempt paths: /wizard/, /login/, /logout/, /accounts/, /static/, /admin/, /help/, /api/
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not self._is_exempt(request.path):
            if not self._wizard_complete():
                return redirect("/wizard/step/1/")

        return self.get_response(request)

    def _is_exempt(self, path):
        for prefix in EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return True
        return False

    def _wizard_complete(self):
        try:
            from apps.wizard.models import ProxmoxConfig

            return ProxmoxConfig.objects.filter(is_configured=True).exists()
        except Exception as exc:
            logger.warning("WizardRedirectMiddleware: could not check wizard state: %s", exc)
            # Fail open — do not redirect if we cannot determine state
            return True


class ForcePasswordChangeMiddleware:
    """Redirect users who have must_change_password=True to the change-password page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not self._is_exempt(request.path):
            try:
                if request.user.profile.must_change_password:
                    return redirect("/change-password/")
            except Exception:
                pass  # Profile not yet created — allow through

        return self.get_response(request)

    def _is_exempt(self, path):
        for prefix in EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return True
        return False


class ForceMFASetupMiddleware:
    """Redirect users to MFA setup if global enforcement is on and they haven't set up MFA.

    Only applies to local and LDAP users — Entra ID handles its own MFA.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not self._is_exempt(request.path):
            try:
                profile = request.user.profile
                if (
                    not profile.mfa_enabled
                    and profile.auth_source in ("local", "ldap")
                ):
                    from apps.core.models import MFAConfig
                    if MFAConfig.get_config().enforce_mfa:
                        return redirect("/mfa/setup/")
            except Exception:
                pass

        return self.get_response(request)

    def _is_exempt(self, path):
        for prefix in EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return True
        return False
