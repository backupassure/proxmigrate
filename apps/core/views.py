import logging
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render

logger = logging.getLogger(__name__)

HELP_DIR = getattr(settings, "HELP_DIR", Path(settings.BASE_DIR) / "help")


@login_required
def dashboard(request):
    """Main dashboard view."""
    from apps.wizard.models import ProxmoxConfig

    wizard_complete = ProxmoxConfig.objects.filter(is_configured=True).exists()
    context = {
        "wizard_complete": wizard_complete,
        "help_slug": "dashboard",
    }
    return render(request, "core/dashboard.html", context)


def login_view(request):
    """Login page. Wraps Django auth login."""
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.POST.get("next") or request.GET.get("next") or "/"
            # Safety: only allow relative redirects
            if not next_url.startswith("/"):
                next_url = "/"
            return redirect(next_url)
        else:
            error = "Invalid username or password."
            logger.warning("Failed login attempt for username: %s", username)

    context = {
        "error": error,
        "next": request.GET.get("next", "/"),
    }
    return render(request, "core/login.html", context)


def logout_view(request):
    """Log out the current user. POST only."""
    if request.method == "POST":
        logout(request)
    return redirect("/login/")


def help_view(request, slug):
    """Return a rendered markdown help partial.

    Never returns 404 — returns a friendly message if the help file is missing.
    """
    help_path = Path(HELP_DIR) / f"{slug}.md"
    try:
        raw_md = help_path.read_text(encoding="utf-8")
        html_content = markdown.markdown(
            raw_md,
            extensions=["extra", "toc", "fenced_code"],
        )
    except FileNotFoundError:
        logger.debug("Help file not found: %s", help_path)
        html_content = "<p>No help available for this page yet.</p>"
    except Exception as exc:
        logger.warning("Error reading help file %s: %s", help_path, exc)
        html_content = "<p>No help available for this page yet.</p>"

    return HttpResponse(html_content)
