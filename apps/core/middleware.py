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
