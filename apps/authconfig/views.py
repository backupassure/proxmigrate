import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.authconfig.backend_loader import load_auth_backends_from_db
from apps.authconfig.models import EntraIDConfig
from apps.authconfig.models import LDAPConfig

logger = logging.getLogger(__name__)

User = get_user_model()


def _staff_required(view_func):
    """Decorator: require login and is_staff."""
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_staff:
            return HttpResponse("Forbidden — staff access required.", status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped


def _login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _render_user_row(request, user):
    return render(request, "authconfig/partials/user_row.html", {
        "u": user,
        "current_user": request.user,
    })


@_staff_required
def auth_settings(request):
    from apps.core.models import MFAConfig
    ldap_config = LDAPConfig.objects.first()
    entra_config = EntraIDConfig.objects.first()
    mfa_config = MFAConfig.get_config()
    active_tab = request.GET.get("tab", "local")
    return render(request, "authconfig/settings.html", {
        "ldap_config": ldap_config,
        "entra_config": entra_config,
        "ldap_enabled": ldap_config.is_enabled if ldap_config else False,
        "entra_enabled": entra_config.is_enabled if entra_config else False,
        "mfa_enforced": mfa_config.enforce_mfa,
        "mfa_email_recovery": mfa_config.allow_email_recovery,
        "active_tab": active_tab,
    })


@_staff_required
@require_POST
def auth_settings_save(request, auth_type):
    if auth_type == "ldap":
        config, _ = LDAPConfig.objects.get_or_create(pk=1)
        config.server_uri = request.POST.get("server_uri", "").strip() or "ldap://localhost"
        config.bind_dn = request.POST.get("bind_dn", "").strip()
        config.user_search_base = request.POST.get("user_search_base", "").strip()
        config.user_search_filter = request.POST.get("user_search_filter", "(uid=%(user)s)").strip()
        config.require_group = request.POST.get("require_group", "").strip()
        config.admin_group = request.POST.get("admin_group", "").strip()
        config.use_tls = "use_tls" in request.POST
        config.skip_cert_verify = "skip_cert_verify" in request.POST
        config.ca_cert = request.POST.get("ca_cert", "").strip()
        new_pw = request.POST.get("bind_password", "").strip()
        if new_pw:
            config.bind_password = new_pw
        config.save()
        load_auth_backends_from_db()
        logger.info("LDAP config saved by %s", request.user)
        messages.success(request, "LDAP settings saved.")
        return redirect(reverse("auth_settings") + "?tab=ldap")

    elif auth_type == "entra":
        config, _ = EntraIDConfig.objects.get_or_create(pk=1)
        config.tenant_id = request.POST.get("tenant_id", "").strip()
        config.client_id = request.POST.get("client_id", "").strip()
        config.allowed_domains = request.POST.get("allowed_domains", "").strip()
        config.require_group_id = request.POST.get("require_group_id", "").strip()
        config.admin_group_id = request.POST.get("admin_group_id", "").strip()
        new_secret = request.POST.get("client_secret", "").strip()
        if new_secret:
            config.client_secret = new_secret
        config.save()
        load_auth_backends_from_db()
        logger.info("Entra ID config saved by %s", request.user)
        messages.success(request, "Entra ID settings saved.")
        return redirect(reverse("auth_settings") + "?tab=entra")

    messages.error(request, f"Unknown auth type: {auth_type}")
    return redirect("auth_settings")


@_staff_required
@require_POST
def auth_settings_toggle(request, auth_type):
    if auth_type == "ldap":
        config, _ = LDAPConfig.objects.get_or_create(pk=1)
        config.is_enabled = not config.is_enabled
        config.save()
        load_auth_backends_from_db()
        state = "enabled" if config.is_enabled else "disabled"
        logger.info("LDAP %s by %s", state, request.user)
        messages.success(request, f"LDAP authentication {state}.")
        return redirect(reverse("auth_settings") + "?tab=ldap")
    elif auth_type == "entra":
        config, _ = EntraIDConfig.objects.get_or_create(pk=1)
        config.is_enabled = not config.is_enabled
        config.save()
        load_auth_backends_from_db()
        state = "enabled" if config.is_enabled else "disabled"
        logger.info("Entra ID %s by %s", state, request.user)
        messages.success(request, f"Entra ID authentication {state}.")
        return redirect(reverse("auth_settings") + "?tab=entra")
    elif auth_type == "mfa":
        from apps.core.models import MFAConfig
        config = MFAConfig.get_config()
        config.enforce_mfa = not config.enforce_mfa
        config.save()
        state = "enforced" if config.enforce_mfa else "optional"
        logger.info("MFA %s by %s", state, request.user)
        messages.success(request, f"MFA is now {state} for all local and LDAP users.")
        return redirect(reverse("auth_settings") + "?tab=mfa")
    elif auth_type == "mfa_email":
        from apps.core.models import MFAConfig
        config = MFAConfig.get_config()
        config.allow_email_recovery = not config.allow_email_recovery
        config.save()
        state = "enabled" if config.allow_email_recovery else "disabled"
        logger.info("MFA email recovery %s by %s", state, request.user)
        messages.success(request, f"MFA email recovery is now {state}.")
        return redirect(reverse("auth_settings") + "?tab=mfa")
    else:
        return HttpResponse("Unknown auth type", status=400)


@_staff_required
@require_POST
def auth_settings_test(request, auth_type):
    if auth_type == "entra":
        return HttpResponse(
            '<div class="notification is-info is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
            '<i class="fas fa-info-circle" style="margin-right:0.3rem;"></i>'
            "Save settings and use the <strong>Sign in with Microsoft</strong> button on the login page to verify.</div>"
        )

    if auth_type != "ldap":
        return HttpResponse("Unknown auth type", status=400)

    server_uri = request.POST.get("server_uri", "").strip()
    bind_dn = request.POST.get("bind_dn", "").strip()
    bind_password = request.POST.get("bind_password", "").strip()
    user_search_base = request.POST.get("user_search_base", "").strip()
    use_tls = "use_tls" in request.POST
    skip_cert_verify = "skip_cert_verify" in request.POST
    ca_cert = request.POST.get("ca_cert", "").strip()

    stored = LDAPConfig.objects.first()
    # Fall back to stored password if none submitted (field was left blank)
    if not bind_password and stored:
        bind_password = stored.bind_password or ""
    # Fall back to stored CA cert if not in form
    if not ca_cert and stored:
        ca_cert = stored.ca_cert or ""

    if not server_uri:
        return HttpResponse(
            '<div class="notification is-warning is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
            "Enter an LDAP server URI first.</div>"
        )

    if not user_search_base:
        return HttpResponse(
            '<div class="notification is-warning is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
            '<i class="fas fa-exclamation-triangle" style="margin-right:0.4rem;"></i>'
            "<strong>User Search Base is required.</strong> For Active Directory, use the root of your domain — "
            "e.g. <code>DC=example,DC=com</code>.</div>"
        )

    try:
        import ldap
        import tempfile

        ldap.set_option(ldap.OPT_REFERRALS, 0)
        if skip_cert_verify:
            ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
            ldap.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
        elif ca_cert:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
                f.write(ca_cert)
                ca_cert_path = f.name
            ldap.set_option(ldap.OPT_X_TLS_CACERTFILE, ca_cert_path)
            ldap.set_option(ldap.OPT_X_TLS_NEWCTX, 0)

        conn = ldap.initialize(server_uri)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 5)
        conn.set_option(ldap.OPT_TIMEOUT, 5)

        # STARTTLS only applies to plain ldap:// — ldaps:// is already TLS
        if use_tls and not server_uri.lower().startswith("ldaps://"):
            conn.start_tls_s()

        conn.simple_bind_s(bind_dn or "", bind_password or "")
        conn.unbind_s()
        return HttpResponse(
            '<div class="notification is-success is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
            '<i class="fas fa-check-circle" style="margin-right:0.3rem;"></i>LDAP connection successful.</div>'
        )
    except ImportError:
        return HttpResponse(
            '<div class="notification is-danger is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
            "python-ldap not installed. Run: pip install python-ldap</div>"
        )
    except Exception as exc:
        logger.warning("auth_settings_test LDAP: %s", exc)
        return HttpResponse(
            f'<div class="notification is-danger is-light" style="font-size:0.82rem;padding:0.5rem 0.75rem;margin:0;">'
            f'<i class="fas fa-times-circle" style="margin-right:0.3rem;"></i>Connection failed: {exc}</div>'
        )


@_staff_required
def user_list(request):
    """List all Django users."""
    users = User.objects.all().order_by("-date_joined")
    return render(request, "authconfig/users.html", {"users": users})


@_staff_required
@require_POST
def user_create(request):
    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    password1 = request.POST.get("password1", "")
    password2 = request.POST.get("password2", "")
    is_superuser = bool(request.POST.get("is_superuser"))

    if not username:
        messages.error(request, "Username is required.")
        return redirect("user_list")
    if User.objects.filter(username=username).exists():
        messages.error(request, f"Username '{username}' is already taken.")
        return redirect("user_list")
    if not password1:
        messages.error(request, "Password is required.")
        return redirect("user_list")
    if password1 != password2:
        messages.error(request, "Passwords do not match.")
        return redirect("user_list")
    if len(password1) < 8:
        messages.error(request, "Password must be at least 8 characters.")
        return redirect("user_list")

    user = User.objects.create_user(username=username, email=email, password=password1)
    user.is_staff = is_superuser
    user.is_superuser = is_superuser
    user.save()
    logger.info("User %s created by %s", username, request.user)
    messages.success(request, f"User '{username}' created successfully.")
    return redirect("user_list")


@_staff_required
@require_POST
def user_toggle_admin(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user == request.user:
        return HttpResponse("Cannot modify your own admin status.", status=400)
    user.is_superuser = not user.is_superuser
    user.is_staff = user.is_superuser
    user.save()
    logger.info("Admin status for %s toggled to %s by %s", user, user.is_superuser, request.user)
    return _render_user_row(request, user)


@_staff_required
@require_POST
def user_toggle_active(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user == request.user:
        return HttpResponse("Cannot deactivate your own account.", status=400)
    user.is_active = not user.is_active
    user.save()
    logger.info("Active status for %s toggled to %s by %s", user, user.is_active, request.user)
    return _render_user_row(request, user)


@_staff_required
@require_POST
def user_reset_password(request, user_id):
    """Admin resets another user's password — no current password required."""
    user = get_object_or_404(User, pk=user_id)
    if user == request.user:
        return HttpResponse(
            '<div class="notification is-warning is-light" style="font-size:0.875rem;margin:0;">'
            'Use "Change My Password" to update your own password.</div>',
            status=400,
        )

    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")

    if len(new_password) < 8:
        return HttpResponse(
            '<div class="notification is-danger is-light" style="font-size:0.875rem;margin:0;">'
            "Password must be at least 8 characters.</div>",
            status=422,
        )
    if new_password != confirm_password:
        return HttpResponse(
            '<div class="notification is-danger is-light" style="font-size:0.875rem;margin:0;">'
            "Passwords do not match.</div>",
            status=422,
        )

    user.set_password(new_password)
    user.save()
    logger.info("Password for %s reset by %s", user, request.user)
    return HttpResponse(
        f'<div class="notification is-success is-light" style="font-size:0.875rem;margin:0;">'
        f"Password for <strong>{user.username}</strong> updated successfully.</div>"
    )


@_staff_required
@require_POST
def user_reset_mfa(request, user_id):
    """Admin resets MFA for a user who has lost all recovery options."""
    user = get_object_or_404(User, pk=user_id)
    try:
        profile = user.profile
        profile.mfa_enabled = False
        profile.mfa_secret = ""
        profile.mfa_recovery_codes = ""
        profile.mfa_confirmed_at = None
        profile.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_recovery_codes", "mfa_confirmed_at"])
        logger.info("MFA reset for %s by %s", user.username, request.user)
        return HttpResponse(
            f'<div class="notification is-success is-light" style="font-size:0.875rem;margin:0;">'
            f"MFA has been reset for <strong>{user.username}</strong>.</div>"
        )
    except Exception as exc:
        logger.error("Failed to reset MFA for %s: %s", user.username, exc)
        return HttpResponse(
            '<div class="notification is-danger is-light" style="font-size:0.875rem;margin:0;">'
            "Failed to reset MFA.</div>",
            status=500,
        )


@_staff_required
@require_POST
def user_delete(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect("user_list")
    username = user.username
    user.delete()
    logger.info("User %s deleted by %s", username, request.user)
    messages.success(request, f"User '{username}' deleted.")
    return redirect("user_list")


@_login_required
@require_POST
def change_own_password(request):
    """Authenticated user changes their own password — requires current password."""
    current_password = request.POST.get("current_password", "")
    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")

    if not request.user.check_password(current_password):
        messages.error(request, "Current password is incorrect.")
        return redirect("user_list")
    if len(new_password) < 8:
        messages.error(request, "New password must be at least 8 characters.")
        return redirect("user_list")
    if new_password != confirm_password:
        messages.error(request, "New passwords do not match.")
        return redirect("user_list")

    request.user.set_password(new_password)
    request.user.save()
    update_session_auth_hash(request, request.user)  # keep user logged in
    logger.info("Password changed by %s", request.user)
    messages.success(request, "Your password has been updated.")
    return redirect("user_list")
