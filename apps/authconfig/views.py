import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.authconfig.forms import EntraIDConfigForm
from apps.authconfig.forms import LDAPConfigForm
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
    """Main auth settings page showing LDAP and Entra ID config."""
    ldap_config = LDAPConfig.objects.first()
    entra_config = EntraIDConfig.objects.first()

    ldap_form = LDAPConfigForm(instance=ldap_config)
    entra_form = EntraIDConfigForm(instance=entra_config)

    return render(
        request,
        "authconfig/settings.html",
        {
            "ldap_form": ldap_form,
            "entra_form": entra_form,
            "ldap_config": ldap_config,
            "entra_config": entra_config,
        },
    )


@_staff_required
@require_POST
def save_ldap(request):
    """Save LDAP settings. Returns HTMX partial."""
    ldap_config = LDAPConfig.objects.first()
    form = LDAPConfigForm(request.POST, instance=ldap_config)

    if form.is_valid():
        form.save()
        logger.info("LDAP config saved by %s", request.user)
        return HttpResponse(
            '<div class="alert alert-success">LDAP settings saved.</div>'
        )
    else:
        return render(
            request,
            "authconfig/partials/ldap_form.html",
            {"ldap_form": form},
            status=422,
        )


@_staff_required
@require_POST
def save_entra(request):
    """Save Entra ID settings. Returns HTMX partial."""
    entra_config = EntraIDConfig.objects.first()
    form = EntraIDConfigForm(request.POST, instance=entra_config)

    if form.is_valid():
        form.save()
        logger.info("Entra ID config saved by %s", request.user)
        return HttpResponse(
            '<div class="alert alert-success">Entra ID settings saved.</div>'
        )
    else:
        return render(
            request,
            "authconfig/partials/entra_form.html",
            {"entra_form": form},
            status=422,
        )


@_staff_required
@require_POST
def test_ldap(request):
    """Attempt an LDAP bind and return result as HTMX partial."""
    ldap_config = LDAPConfig.objects.first()
    if not ldap_config or not ldap_config.is_enabled:
        return HttpResponse(
            '<div class="alert alert-warning">LDAP is not configured or not enabled.</div>'
        )

    try:
        import ldap3

        server = ldap3.Server(
            ldap_config.server_uri,
            use_ssl=ldap_config.server_uri.startswith("ldaps://"),
            get_info=ldap3.ALL,
        )
        conn = ldap3.Connection(
            server,
            user=ldap_config.bind_dn or None,
            password=ldap_config.bind_password or None,
            auto_bind=ldap3.AUTO_BIND_TLS_BEFORE_BIND if ldap_config.use_tls else True,
        )
        if conn.bound:
            conn.unbind()
            return HttpResponse(
                '<div class="alert alert-success">LDAP connection successful.</div>'
            )
        else:
            return HttpResponse(
                f'<div class="alert alert-danger">LDAP bind failed: {conn.last_error}</div>'
            )

    except ImportError:
        return HttpResponse(
            '<div class="alert alert-danger">ldap3 library not installed. '
            "Run: pip install ldap3</div>"
        )
    except Exception as exc:
        logger.warning("test_ldap failed: %s", exc)
        return HttpResponse(
            f'<div class="alert alert-danger">LDAP connection failed: {exc}</div>'
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
