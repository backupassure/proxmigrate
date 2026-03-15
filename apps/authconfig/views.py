import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
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

    from django.shortcuts import redirect

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_staff:
            return HttpResponse("Forbidden — staff access required.", status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped


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
    return render(
        request,
        "authconfig/users.html",
        {"users": users},
    )
