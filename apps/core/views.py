import hashlib
import io
import logging
import secrets
import time
from base64 import b64encode
from pathlib import Path

import markdown
import pyotp
import qrcode
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
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

HELP_DIR = getattr(settings, "HELP_DIR", Path(settings.BASE_DIR) / "help")


@login_required
def dashboard(request):
    """Main dashboard view."""
    import os
    from apps.wizard.models import ProxmoxConfig
    from apps.importer.models import ImportJob
    from apps.vmcreator.models import VmCommunityScriptJob, VmCreateJob
    from apps.lxc.models import LxcCloneJob, LxcCreateJob, LxcSnapshotLog, CommunityScriptJob
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

        community_jobs = list(CommunityScriptJob.objects.order_by("-created_at")[:5])
        for j in community_jobs:
            j.job_type = "community_script"
            j.display_name = j.app_name
            j.vm_name = j.app_name

        vm_community_jobs = list(VmCommunityScriptJob.objects.order_by("-created_at")[:5])
        for j in vm_community_jobs:
            j.job_type = "vm_community_script"
            j.display_name = j.app_name
            j.vm_name = j.app_name

        combined = sorted(import_jobs + create_jobs + lxc_jobs + lxc_clone_jobs + snapshot_logs + community_jobs + vm_community_jobs, key=lambda j: j.created_at, reverse=True)
        recent_jobs = combined[:8]

    # Certificate expiry warning
    cert_days_remaining = None
    try:
        from apps.certificates.helpers import CERT_FILE, get_cert_info

        if os.path.exists(CERT_FILE):
            cert_info = get_cert_info()
            if cert_info and "not_after" in cert_info:
                delta = cert_info["not_after"] - timezone.now()
                cert_days_remaining = delta.days
    except Exception:
        pass

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
        "cert_days_remaining": cert_days_remaining,
        "cert_expiry_warning": cert_days_remaining is not None and cert_days_remaining <= 30,
        "help_slug": "dashboard",
    }
    return render(request, "core/dashboard.html", context)


@login_required
def dashboard_job_status(request, job_type, job_id):
    """HTMX endpoint: return an updated job row partial for dashboard polling."""
    from apps.importer.models import ImportJob
    from apps.vmcreator.models import VmCreateJob
    from apps.lxc.models import LxcCloneJob, LxcCreateJob

    snap_action_labels = {"create": "Create", "rollback": "Rollback", "delete": "Delete"}

    if job_type == "import":
        from django.shortcuts import get_object_or_404
        job = get_object_or_404(ImportJob, pk=job_id)
        job.job_type = "import"
        job.display_name = job.upload_filename or job.vm_name
    elif job_type == "create":
        from django.shortcuts import get_object_or_404
        job = get_object_or_404(VmCreateJob, pk=job_id)
        job.job_type = "create"
        job.display_name = job.iso_filename if job.source_type == VmCreateJob.SOURCE_ISO else "Blank VM"
    elif job_type == "lxc_create":
        from django.shortcuts import get_object_or_404
        job = get_object_or_404(LxcCreateJob, pk=job_id)
        job.job_type = "lxc_create"
        job.display_name = job.template or job.ct_name
        job.vm_name = job.ct_name
    elif job_type == "lxc_clone":
        from django.shortcuts import get_object_or_404
        job = get_object_or_404(LxcCloneJob, pk=job_id)
        job.job_type = "lxc_clone"
        job.display_name = f"Clone of {job.source_name or job.source_vmid}"
        job.vm_name = job.ct_name
    else:
        return HttpResponse(status=404)

    return render(request, "core/partials/dashboard_job_row.html", {"job": job})


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
            next_url = request.POST.get("next") or request.GET.get("next") or "/"
            if not next_url.startswith("/"):
                next_url = "/"

            # Check if MFA is enabled for this user
            try:
                profile = user.profile
                if profile.mfa_enabled and profile.auth_source != "entra":
                    # Don't login yet — redirect to MFA challenge
                    request.session["mfa_user_id"] = user.pk
                    request.session["mfa_next"] = next_url
                    request.session["mfa_expires"] = time.time() + 300  # 5 min
                    request.session["mfa_attempts"] = 0
                    return redirect("mfa_verify")
            except Exception:
                pass

            login(request, user)
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


# ── MFA / TOTP ──────────────────────────────────────────────────────────────


def _get_mfa_pending_user(request):
    """Load the user from the MFA session. Returns None if expired or missing."""
    user_id = request.session.get("mfa_user_id")
    expires = request.session.get("mfa_expires", 0)
    if not user_id or time.time() > expires:
        # Clear stale session keys
        for key in ("mfa_user_id", "mfa_next", "mfa_expires", "mfa_attempts"):
            request.session.pop(key, None)
        return None
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return None


def _hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_recovery_codes(count=8):
    """Return (plaintext_list, hashed_json_string)."""
    import json
    codes = [secrets.token_hex(4) for _ in range(count)]
    hashed = json.dumps([_hash_code(c) for c in codes])
    return codes, hashed


def mfa_verify(request):
    """MFA challenge page shown after username/password succeeds."""
    user = _get_mfa_pending_user(request)
    if user is None:
        return redirect("login")

    error = None
    if request.method == "POST":
        code = request.POST.get("code", "").strip().replace(" ", "")
        attempts = request.session.get("mfa_attempts", 0) + 1
        request.session["mfa_attempts"] = attempts

        if attempts > 5:
            messages.error(request, "Too many failed attempts. Please sign in again.")
            for key in ("mfa_user_id", "mfa_next", "mfa_expires", "mfa_attempts"):
                request.session.pop(key, None)
            return redirect("login")

        verified = False

        # Try TOTP code (6 digits)
        if len(code) == 6 and code.isdigit():
            totp = pyotp.TOTP(user.profile.mfa_secret)
            if totp.verify(code, valid_window=1):
                verified = True

        # Try recovery code (8 hex chars)
        if not verified and len(code) == 8:
            import json
            try:
                stored = json.loads(user.profile.mfa_recovery_codes or "[]")
                code_hash = _hash_code(code.lower())
                if code_hash in stored:
                    stored.remove(code_hash)
                    user.profile.mfa_recovery_codes = json.dumps(stored)
                    user.profile.save(update_fields=["mfa_recovery_codes"])
                    verified = True
                    logger.info("mfa_verify: user %s used recovery code (%d remaining)",
                                user.username, len(stored))
            except (ValueError, TypeError):
                pass

        if verified:
            next_url = request.session.get("mfa_next", "/")
            for key in ("mfa_user_id", "mfa_next", "mfa_expires", "mfa_attempts"):
                request.session.pop(key, None)
            login(request, user)
            return redirect(next_url)
        else:
            error = "Invalid code. Please try again."

    from apps.core.models import MFAConfig
    mfa_config = MFAConfig.get_config()

    return render(request, "core/mfa_verify.html", {
        "error": error,
        "allow_email_recovery": mfa_config.allow_email_recovery,
    })


def mfa_email_recovery(request):
    """Send a one-time bypass code via email during MFA challenge."""
    from apps.core.models import MFAConfig
    from apps.emailconfig.models import EmailConfig

    # Check if admin has enabled email recovery
    mfa_config = MFAConfig.get_config()
    if not mfa_config.allow_email_recovery:
        messages.error(request, "Email recovery has been disabled by your administrator.")
        return redirect("mfa_verify")

    user = _get_mfa_pending_user(request)
    if user is None:
        return redirect("login")

    email_config = EmailConfig.get_config()
    email_enabled = email_config.is_enabled if email_config else False

    error = None

    if request.method == "POST":
        submitted = request.POST.get("code", "").strip()

        if submitted:
            # Verify the emailed code
            stored_hash = request.session.get("mfa_email_code_hash")
            code_expires = request.session.get("mfa_email_code_expires", 0)

            if not stored_hash or time.time() > code_expires:
                error = "Code has expired. Please request a new one."
            elif _hash_code(submitted) == stored_hash:
                next_url = request.session.get("mfa_next", "/")
                for key in ("mfa_user_id", "mfa_next", "mfa_expires", "mfa_attempts",
                            "mfa_email_code_hash", "mfa_email_code_expires", "mfa_email_sent_at"):
                    request.session.pop(key, None)
                login(request, user)
                messages.success(request, "Signed in via email recovery code.")
                return redirect(next_url)
            else:
                error = "Invalid code. Please check your email and try again."
        else:
            # Send the code
            if not email_enabled:
                error = "Email is not configured. Contact your administrator."
            elif not user.email:
                error = "No email address on file for this account. Contact your administrator."
            else:
                last_sent = request.session.get("mfa_email_sent_at", 0)
                if time.time() - last_sent < 60:
                    error = "A code was sent recently. Please wait before requesting another."
                else:
                    code = f"{secrets.randbelow(1000000):06d}"
                    request.session["mfa_email_code_hash"] = _hash_code(code)
                    request.session["mfa_email_code_expires"] = time.time() + 300
                    request.session["mfa_email_sent_at"] = time.time()

                    try:
                        send_mail(
                            "ProxMigrate — MFA Recovery Code",
                            render_to_string("core/email/mfa_bypass_code.txt", {
                                "user": user,
                                "code": code,
                            }),
                            None,
                            [user.email],
                            fail_silently=False,
                        )
                        messages.success(request, f"A recovery code has been sent to {user.email}.")
                        logger.info("mfa_email_recovery: sent code to %s for user %s", user.email, user.username)
                    except Exception as exc:
                        logger.error("mfa_email_recovery: failed to send: %s", exc)
                        error = "Failed to send email. Please try again or contact your administrator."

    return render(request, "core/mfa_email_recovery.html", {
        "error": error,
        "email_enabled": email_enabled,
        "user_email": user.email if user else "",
    })


@login_required
def mfa_setup(request):
    """MFA setup page — show QR code and verify first TOTP code."""
    profile = request.user.profile
    if profile.auth_source == "entra":
        messages.info(request, "MFA is managed by your Entra ID provider.")
        return redirect("dashboard")

    # Generate or retrieve pending secret
    pending_secret = request.session.get("mfa_pending_secret")
    if not pending_secret:
        pending_secret = pyotp.random_base32()
        request.session["mfa_pending_secret"] = pending_secret

    # Build provisioning URI and QR code
    name = request.user.email or request.user.username
    totp = pyotp.TOTP(pending_secret)
    uri = totp.provisioning_uri(name=name, issuer_name="ProxMigrate")

    img = qrcode.make(uri, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_data_uri = "data:image/png;base64," + b64encode(buf.getvalue()).decode()

    from apps.core.models import MFAConfig
    enforced = MFAConfig.get_config().enforce_mfa

    return render(request, "core/mfa_setup.html", {
        "qr_data_uri": qr_data_uri,
        "secret": pending_secret,
        "already_enabled": profile.mfa_enabled,
        "enforced": enforced,
    })


@login_required
@require_POST
def mfa_setup_confirm(request):
    """Verify the first TOTP code and enable MFA."""
    pending_secret = request.session.get("mfa_pending_secret")
    if not pending_secret:
        messages.error(request, "MFA setup expired. Please try again.")
        return redirect("mfa_setup")

    code = request.POST.get("code", "").strip()
    totp = pyotp.TOTP(pending_secret)

    if not totp.verify(code, valid_window=1):
        messages.error(request, "Invalid code. Please scan the QR code again and enter the current code.")
        return redirect("mfa_setup")

    # Enable MFA
    profile = request.user.profile
    profile.mfa_secret = pending_secret
    profile.mfa_enabled = True
    profile.mfa_confirmed_at = timezone.now()

    # Generate recovery codes
    codes, hashed_json = _generate_recovery_codes()
    profile.mfa_recovery_codes = hashed_json
    profile.save(update_fields=["mfa_secret", "mfa_enabled", "mfa_confirmed_at", "mfa_recovery_codes"])

    request.session.pop("mfa_pending_secret", None)
    logger.info("mfa_setup: MFA enabled for user %s", request.user.username)

    return render(request, "core/mfa_recovery_codes.html", {
        "codes": codes,
    })


@login_required
@require_POST
def mfa_disable(request):
    """Disable MFA for the current user (requires password confirmation)."""
    from apps.core.models import MFAConfig

    if MFAConfig.get_config().enforce_mfa:
        messages.error(request, "MFA is enforced globally and cannot be disabled.")
        return redirect("dashboard")

    password = request.POST.get("password", "")
    if not request.user.check_password(password):
        messages.error(request, "Incorrect password. MFA was not disabled.")
        return redirect("mfa_setup")

    profile = request.user.profile
    profile.mfa_enabled = False
    profile.mfa_secret = ""
    profile.mfa_recovery_codes = ""
    profile.mfa_confirmed_at = None
    profile.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_recovery_codes", "mfa_confirmed_at"])

    logger.info("mfa_disable: MFA disabled for user %s", request.user.username)
    messages.success(request, "MFA has been disabled for your account.")
    return redirect("dashboard")
