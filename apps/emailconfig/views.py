import logging
import smtplib
import socket

import requests
from django.apps import apps
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.emailconfig.models import EmailConfig

logger = logging.getLogger(__name__)


def _staff_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_staff:
            return HttpResponse("Forbidden — staff access required.", status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped


def _reload_email_config():
    """Re-apply email settings from DB into the running Django process."""
    apps.get_app_config("emailconfig")._load_email_config()


@_staff_required
def email_settings(request):
    config = EmailConfig.objects.first()
    active_tab = request.GET.get("tab", "smtp")
    return render(request, "emailconfig/settings.html", {
        "config": config,
        "active_tab": active_tab,
        "help_slug": "email-settings",
    })


@_staff_required
@require_POST
def email_settings_save(request, backend_type):
    config, _ = EmailConfig.objects.get_or_create(pk=1)
    config.from_email = request.POST.get("from_email", "").strip()

    config.is_enabled = "is_enabled" in request.POST

    if backend_type == "smtp":
        config.backend_type = EmailConfig.BACKEND_SMTP
        config.smtp_host = request.POST.get("smtp_host", "").strip()
        try:
            config.smtp_port = int(request.POST.get("smtp_port", "587"))
        except ValueError:
            config.smtp_port = 587
        config.smtp_username = request.POST.get("smtp_username", "").strip()
        new_password = request.POST.get("smtp_password", "").strip()
        if new_password:
            config.smtp_password = new_password
        config.smtp_use_tls = "smtp_use_tls" in request.POST
        config.smtp_use_ssl = "smtp_use_ssl" in request.POST
        config.save()
        _reload_email_config()
        logger.info("SMTP email config saved by %s (enabled=%s)", request.user, config.is_enabled)
        messages.success(request, "SMTP settings saved.")
        return redirect("email_settings")

    elif backend_type == "graph":
        config.backend_type = EmailConfig.BACKEND_GRAPH
        config.graph_tenant_id = request.POST.get("graph_tenant_id", "").strip()
        config.graph_client_id = request.POST.get("graph_client_id", "").strip()
        new_secret = request.POST.get("graph_client_secret", "").strip()
        if new_secret:
            config.graph_client_secret = new_secret
        config.save()
        _reload_email_config()
        logger.info("Graph API email config saved by %s (enabled=%s)", request.user, config.is_enabled)
        messages.success(request, "Microsoft Graph API settings saved.")
        return redirect("email_settings")

    messages.error(request, f"Unknown backend type: {backend_type}")
    return redirect("email_settings")


@_staff_required
@require_POST
def email_settings_test(request, backend_type):
    to_address = request.POST.get("test_to", "").strip()
    if not to_address:
        return _test_response("warning", "Enter a recipient address to send a test email.")

    if backend_type == "smtp":
        host = request.POST.get("smtp_host", "").strip()
        try:
            port = int(request.POST.get("smtp_port", "587"))
        except ValueError:
            port = 587
        username = request.POST.get("smtp_username", "").strip()
        password = request.POST.get("smtp_password", "").strip()
        use_tls = "smtp_use_tls" in request.POST
        use_ssl = "smtp_use_ssl" in request.POST
        from_email = request.POST.get("from_email", "").strip()

        # Fall back to stored password if left blank
        if not password:
            stored = EmailConfig.objects.first()
            if stored:
                password = stored.smtp_password or ""

        if not host:
            return _test_response("warning", "Enter an SMTP host first.")

        try:
            if use_ssl:
                conn = smtplib.SMTP_SSL(host, port, timeout=10)
            else:
                conn = smtplib.SMTP(host, port, timeout=10)
                if use_tls:
                    conn.starttls()
            if username:
                conn.login(username, password)

            conn.sendmail(
                from_email or username or "proxorchestrator@localhost",
                to_address,
                f"Subject: ProxOrchestrator Test Email\r\n\r\n"
                f"This is a test email from ProxOrchestrator to confirm your SMTP configuration is working.",
            )
            conn.quit()
            return _test_response("success", f"Test email sent successfully to {to_address}.")
        except (smtplib.SMTPException, socket.error, OSError) as exc:
            logger.warning("email_settings_test SMTP: %s", exc)
            return _test_response("danger", f"SMTP error: {exc}")

    elif backend_type == "graph":
        tenant_id = request.POST.get("graph_tenant_id", "").strip()
        client_id = request.POST.get("graph_client_id", "").strip()
        client_secret = request.POST.get("graph_client_secret", "").strip()
        from_email = request.POST.get("from_email", "").strip()

        if not client_secret:
            stored = EmailConfig.objects.first()
            if stored:
                client_secret = stored.graph_client_secret or ""

        if not tenant_id or not client_id:
            return _test_response("warning", "Enter Tenant ID and Client ID first.")
        if not from_email:
            return _test_response("warning", "Enter a From address first.")

        try:
            token_resp = requests.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=15,
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]

            payload = {
                "message": {
                    "subject": "ProxOrchestrator Test Email",
                    "body": {
                        "contentType": "Text",
                        "content": "This is a test email from ProxOrchestrator to confirm your Microsoft Graph API configuration is working.",
                    },
                    "toRecipients": [{"emailAddress": {"address": to_address}}],
                    "from": {"emailAddress": {"address": from_email}},
                },
                "saveToSentItems": False,
            }
            send_resp = requests.post(
                f"https://graph.microsoft.com/v1.0/users/{from_email}/sendMail",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            send_resp.raise_for_status()
            return _test_response("success", f"Test email sent successfully to {to_address}.")
        except requests.HTTPError as exc:
            try:
                detail = exc.response.json().get("error", {}).get("message", str(exc))
            except Exception:
                detail = str(exc)
            logger.warning("email_settings_test Graph: %s", exc)
            return _test_response("danger", f"Graph API error: {detail}")
        except Exception as exc:
            logger.warning("email_settings_test Graph: %s", exc)
            return _test_response("danger", f"Error: {exc}")

    return _test_response("warning", f"Unknown backend type: {backend_type}")


def _test_response(level, message):
    icon = {
        "success": "fa-check-circle",
        "danger": "fa-times-circle",
        "warning": "fa-exclamation-triangle",
    }.get(level, "fa-info-circle")
    return HttpResponse(
        f'<div class="notification is-{level} is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
        f'<i class="fas {icon}" style="margin-right:0.3rem;"></i>{message}</div>'
    )
