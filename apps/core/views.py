import logging
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth import logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render

logger = logging.getLogger(__name__)

HELP_DIR = getattr(settings, "HELP_DIR", Path(settings.BASE_DIR) / "help")


@login_required
def dashboard(request):
    """Main dashboard view."""
    import os
    from apps.wizard.models import ProxmoxConfig
    from apps.importer.models import ImportJob
    from apps.vmcreator.models import VmCreateJob
    from apps.proxmox.api import ProxmoxAPIError

    config = ProxmoxConfig.objects.first()
    wizard_complete = bool(config and config.is_configured)

    proxmox_host = ""
    api_ok = False
    ssh_key_ok = False
    recent_jobs = []

    if wizard_complete:
        proxmox_host = f"{config.host}:{config.api_port}" if config.host else ""

        # Check SSH key on disk (fast — no network)
        ssh_key_paths = [
            "/opt/proxmigrate/.ssh/id_rsa",
            os.path.expanduser("~/.ssh/id_rsa"),
        ]
        ssh_key_ok = any(os.path.exists(p) for p in ssh_key_paths)

        # Quick API liveness check
        try:
            api = config.get_api_client()
            api.get_nodes()
            api_ok = True
        except Exception:
            api_ok = False

        import_jobs = list(ImportJob.objects.order_by("-created_at")[:8])
        create_jobs = list(VmCreateJob.objects.order_by("-created_at")[:8])

        # Tag each with job_type and normalise fields for the template
        for j in import_jobs:
            j.job_type = "import"
            j.display_name = j.upload_filename or j.vm_name
        for j in create_jobs:
            j.job_type = "create"
            j.display_name = j.iso_filename if j.source_type == VmCreateJob.SOURCE_ISO else "Blank VM"

        combined = sorted(import_jobs + create_jobs, key=lambda j: j.created_at, reverse=True)
        recent_jobs = combined[:10]

    context = {
        "wizard_complete": wizard_complete,
        "proxmox_host": proxmox_host,
        "api_ok": api_ok,
        "ssh_key_ok": ssh_key_ok,
        "recent_jobs": recent_jobs,
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

    entra_enabled = False
    ldap_enabled = False
    try:
        from apps.authconfig.models import EntraIDConfig, LDAPConfig
        ldap_cfg = LDAPConfig.objects.first()
        entra_cfg = EntraIDConfig.objects.first()
        ldap_enabled = bool(ldap_cfg and ldap_cfg.is_enabled)
        entra_enabled = bool(entra_cfg and entra_cfg.is_enabled)
    except Exception:
        pass

    context = {
        "error": error,
        "next": request.GET.get("next", "/"),
        "entra_enabled": entra_enabled,
        "ldap_enabled": ldap_enabled,
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


@login_required
def change_password_view(request):
    """Force-password-change page shown after first login with the default password."""
    error = None
    if request.method == "POST":
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if len(new_password) < 8:
            error = "Password must be at least 8 characters."
        elif new_password != confirm_password:
            error = "Passwords do not match."
        elif new_password == "Password!":
            error = "Please choose a password different from the default."
        else:
            request.user.set_password(new_password)
            request.user.save()
            # Clear the force-change flag
            try:
                request.user.profile.must_change_password = False
                request.user.profile.save()
            except Exception:
                pass
            # Keep the user logged in with the new password
            update_session_auth_hash(request, request.user)
            messages.success(request, "Password updated successfully.")
            return redirect("/")

    return render(request, "core/change_password.html", {"error": error})
