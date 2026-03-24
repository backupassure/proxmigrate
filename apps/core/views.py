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
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)

HELP_DIR = getattr(settings, "HELP_DIR", Path(settings.BASE_DIR) / "help")


@login_required
def dashboard(request):
    """Main dashboard view."""
    import os
    from apps.wizard.models import ProxmoxConfig
    from apps.importer.models import ImportJob
    from apps.vmcreator.models import VmCreateJob
    from apps.lxc.models import LxcCloneJob, LxcCreateJob, LxcSnapshotLog
    from apps.proxmox.api import ProxmoxAPIError

    config = ProxmoxConfig.objects.first()
    wizard_complete = bool(config and config.is_configured)

    proxmox_host = ""
    api_ok = False
    ssh_key_ok = False
    recent_jobs = []
    vm_total = vm_running = vm_stopped = 0
    ct_total = ct_running = ct_stopped = 0

    if wizard_complete:
        proxmox_host = f"{config.host}:{config.api_port}" if config.host else ""

        # Check SSH key on disk (fast — no network)
        ssh_key_paths = [
            "/opt/proxmigrate/.ssh/id_rsa",
            os.path.expanduser("~/.ssh/id_rsa"),
        ]
        ssh_key_ok = any(os.path.exists(p) for p in ssh_key_paths)

        # Quick API liveness check + gather counts
        try:
            api = config.get_api_client()
            api.get_nodes()
            api_ok = True

            # VM counts
            vms = api.get_vms(config.default_node)
            vm_total = len(vms)
            vm_running = sum(1 for v in vms if v.get("status") == "running")
            vm_stopped = vm_total - vm_running

            # Container counts
            cts = api.get_lxcs(config.default_node)
            ct_total = len(cts)
            ct_running = sum(1 for c in cts if c.get("status") == "running")
            ct_stopped = ct_total - ct_running
        except Exception:
            api_ok = False

        import_jobs = list(ImportJob.objects.order_by("-created_at")[:5])
        create_jobs = list(VmCreateJob.objects.order_by("-created_at")[:5])
        lxc_jobs = list(LxcCreateJob.objects.order_by("-created_at")[:5])
        lxc_clone_jobs = list(LxcCloneJob.objects.order_by("-created_at")[:5])

        # Tag each with job_type and normalise fields for the template
        for j in import_jobs:
            j.job_type = "import"
            j.display_name = j.upload_filename or j.vm_name
        for j in create_jobs:
            j.job_type = "create"
            j.display_name = j.iso_filename if j.source_type == VmCreateJob.SOURCE_ISO else "Blank VM"
        for j in lxc_jobs:
            j.job_type = "lxc_create"
            j.display_name = j.template or j.ct_name
            j.vm_name = j.ct_name
        for j in lxc_clone_jobs:
            j.job_type = "lxc_clone"
            j.display_name = f"Clone of {j.source_name or j.source_vmid}"
            j.vm_name = j.ct_name

        snap_action_labels = {"create": "Create", "rollback": "Rollback", "delete": "Delete"}
        snapshot_logs = list(LxcSnapshotLog.objects.order_by("-created_at")[:5])
        for j in snapshot_logs:
            j.job_type = "lxc_snapshot"
            j.display_name = f"{snap_action_labels.get(j.action, j.action)} \u2014 {j.snapname}"
            j.vm_name = j.ct_name

        combined = sorted(import_jobs + create_jobs + lxc_jobs + lxc_clone_jobs + snapshot_logs, key=lambda j: j.created_at, reverse=True)
        recent_jobs = combined[:8]

    context = {
        "wizard_complete": wizard_complete,
        "proxmox_host": proxmox_host,
        "api_ok": api_ok,
        "ssh_key_ok": ssh_key_ok,
        "recent_jobs": recent_jobs,
        "vm_total": vm_total,
        "vm_running": vm_running,
        "vm_stopped": vm_stopped,
        "ct_total": ct_total,
        "ct_running": ct_running,
        "ct_stopped": ct_stopped,
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

        # Allow login with email address — look up the username
        if "@" in username:
            try:
                username = User.objects.get(email__iexact=username).username
            except User.DoesNotExist:
                pass  # Let authenticate() fail normally

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


def password_reset_request(request):
    """Ask the user for their email and send a password reset link."""
    from apps.emailconfig.models import EmailConfig

    email_config = EmailConfig.get_config()
    email_enabled = email_config.is_enabled if email_config else False

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        if not email_enabled:
            messages.error(request, "Password recovery is unavailable — email is not configured.")
            return redirect("password_reset_request")

        # Only send for local auth users with a matching email
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
            # Tell directory users to reset via their provider
            auth_source = getattr(getattr(user, "profile", None), "auth_source", "local")
            if auth_source != "local":
                logger.info("password_reset: directory user %s (source=%s)", user.username, auth_source)
                messages.warning(
                    request,
                    "This is an external directory account. "
                    "Password recovery is disabled for this account — "
                    "please reset your password through your organisation's directory (LDAP or Entra ID).",
                )
                return redirect("password_reset_request")

            # Build reset link
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            protocol = "https" if request.is_secure() else "http"
            reset_url = f"{protocol}://{request.get_host()}/reset-password/{uid}/{token}/"

            subject = "ProxMigrate — Password Reset"
            body = render_to_string("core/email/password_reset.txt", {
                "user": user,
                "reset_url": reset_url,
            })

            send_mail(subject, body, None, [user.email], fail_silently=False)
            logger.info("password_reset: sent reset email to %s for user %s", email, user.username)

        except User.DoesNotExist:
            logger.info("password_reset: no user found for email %s", email)
        except Exception as exc:
            logger.error("password_reset: failed to send email: %s", exc)

        # Generic message for local users and unknown emails (no account enumeration)
        messages.success(
            request,
            "If a local account exists with that email address, a password reset link has been sent.",
        )
        return redirect("password_reset_request")

    return render(request, "core/password_reset_request.html", {
        "email_enabled": email_enabled,
    })


def password_reset_confirm(request, uidb64, token):
    """Validate the reset token and let the user set a new password."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return render(request, "core/password_reset_invalid.html")

    error = None
    if request.method == "POST":
        password = request.POST.get("new_password", "")
        confirm = request.POST.get("confirm_password", "")

        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            user.set_password(password)
            user.save()
            # Clear must_change_password flag if set
            try:
                user.profile.must_change_password = False
                user.profile.save()
            except Exception:
                pass
            messages.success(request, "Your password has been reset. You can now sign in.")
            logger.info("password_reset: password changed for user %s", user.username)
            return redirect("login")

    return render(request, "core/password_reset_confirm.html", {
        "error": error,
        "uidb64": uidb64,
        "token": token,
    })
