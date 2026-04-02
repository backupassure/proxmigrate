import datetime
import ipaddress
import logging
import os
import re
import subprocess
from functools import wraps

import redis
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.certificates.helpers import CERT_DIR
from apps.certificates.helpers import CERT_FILE
from apps.certificates.helpers import CSR_KEY_FILE
from apps.certificates.helpers import ENV_FILE
from apps.certificates.helpers import KEY_FILE
from apps.certificates.helpers import PENDING_CSR_FILE
from apps.certificates.helpers import find_nginx_conf
from apps.certificates.helpers import get_cert_info
from apps.certificates.helpers import get_current_port
from apps.certificates.helpers import get_pending_csr
from apps.certificates.helpers import install_cert_and_key
from apps.certificates.helpers import reload_nginx
from apps.certificates.helpers import validate_cert_key_pair
from apps.certificates.models import DIRECTORY_URLS
from apps.certificates.models import AcmeConfig
from apps.certificates.models import AcmeLog

logger = logging.getLogger(__name__)

REDIS_DNS_CONFIRM_KEY = "acme:dns_confirmed"


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def _staff_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_staff:
            return HttpResponse("Forbidden — staff access required.", status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped


# ---------------------------------------------------------------------------
# Certificate settings (main page)
# ---------------------------------------------------------------------------

@_staff_required
def cert_settings(request):
    cert_info = get_cert_info()

    cert_days_remaining = None
    if cert_info and "not_after" in cert_info:
        delta = cert_info["not_after"] - datetime.datetime.now(datetime.timezone.utc)
        cert_days_remaining = delta.days

    acme_config = AcmeConfig.get_config()
    acme_logs = AcmeLog.objects.all()[:10]

    return render(request, "certificates/settings.html", {
        "cert_info": cert_info,
        "cert_days_remaining": cert_days_remaining,
        "pending_csr": get_pending_csr(),
        "has_csr_key": os.path.exists(CSR_KEY_FILE),
        "current_port": get_current_port(),
        "cert_file": CERT_FILE,
        "key_file": KEY_FILE,
        "acme": acme_config,
        "acme_logs": acme_logs,
    })


# ---------------------------------------------------------------------------
# CSR generation
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def generate_csr(request):
    """Generate an RSA private key and CSR. The key stays on the server."""
    cn = request.POST.get("cn", "").strip()
    org = request.POST.get("org", "").strip()
    country = request.POST.get("country", "").strip().upper()[:2]
    dns_raw = request.POST.get("dns_sans", "").strip()
    ip_raw = request.POST.get("ip_sans", "").strip()

    if not cn:
        messages.error(request, "Common Name (CN) is required.")
        return redirect("cert_settings")

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        name_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
        if org:
            name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
        if country:
            name_attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country))

        builder = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name(name_attrs))
        )

        san_list = []
        if cn and not cn.replace(".", "").replace("-", "").isdigit():
            san_list.append(x509.DNSName(cn))

        for d in [s.strip() for s in dns_raw.replace(",", "\n").splitlines() if s.strip()]:
            entry = x509.DNSName(d)
            if entry not in san_list:
                san_list.append(entry)

        for ip_str in [s.strip() for s in ip_raw.replace(",", "\n").splitlines() if s.strip()]:
            try:
                san_list.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
            except ValueError:
                messages.warning(request, f"Skipped invalid IP SAN: {ip_str}")

        if san_list:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(san_list), critical=False,
            )

        csr = builder.sign(key, hashes.SHA256())

        os.makedirs(CERT_DIR, exist_ok=True)

        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM)

        with open(CSR_KEY_FILE, "wb") as f:
            f.write(key_pem)
        os.chmod(CSR_KEY_FILE, 0o600)

        with open(PENDING_CSR_FILE, "wb") as f:
            f.write(csr_pem)

        logger.info("CSR generated by %s for CN=%s", request.user, cn)
        messages.success(
            request,
            f"CSR generated for {cn}. Submit it to your CA, "
            f"then upload the signed certificate.",
        )

    except Exception as exc:
        logger.error("generate_csr: %s", exc, exc_info=True)
        messages.error(request, f"CSR generation failed: {exc}")

    return redirect("cert_settings")


# ---------------------------------------------------------------------------
# Certificate upload
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def upload_signed_cert(request):
    """Upload a CA-signed certificate that matches the pending CSR private key."""
    if not os.path.exists(CSR_KEY_FILE):
        messages.error(request, "No pending CSR key found. Generate a CSR first.")
        return redirect("cert_settings")

    cert_file = request.FILES.get("signed_cert")
    cert_pem_text = request.POST.get("cert_pem", "").strip()

    if not cert_file and not cert_pem_text:
        messages.error(request, "Please upload or paste the signed certificate.")
        return redirect("cert_settings")

    try:
        if cert_file:
            cert_content = cert_file.read()
        else:
            cert_content = cert_pem_text.encode("utf-8")
        with open(CSR_KEY_FILE, "rb") as f:
            key_content = f.read()

        validate_cert_key_pair(cert_content, key_content)
        install_cert_and_key(cert_content, key_content)

        for path in (CSR_KEY_FILE, PENDING_CSR_FILE):
            try:
                os.unlink(path)
            except OSError:
                pass

        logger.info("Signed certificate installed by %s", request.user)

        try:
            reload_nginx()
            messages.success(
                request,
                "Certificate installed and nginx reloaded successfully.",
            )
        except RuntimeError as exc:
            messages.warning(
                request,
                f"Certificate installed but nginx reload failed: {exc}",
            )

    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        logger.error("upload_signed_cert: %s", exc, exc_info=True)
        messages.error(request, f"Upload failed: {exc}")

    return redirect("cert_settings")


@_staff_required
@require_POST
def upload_own_cert(request):
    """Upload a certificate and private key supplied by the user."""
    cert_file = request.FILES.get("cert_file")
    key_file = request.FILES.get("key_file")

    if not cert_file or not key_file:
        messages.error(
            request, "Both certificate and private key files are required.",
        )
        return redirect("cert_settings")

    try:
        cert_content = cert_file.read()
        key_content = key_file.read()

        validate_cert_key_pair(cert_content, key_content)
        install_cert_and_key(cert_content, key_content)

        logger.info("Certificate and key uploaded by %s", request.user)

        try:
            reload_nginx()
            messages.success(
                request,
                "Certificate installed and nginx reloaded successfully.",
            )
        except RuntimeError as exc:
            messages.warning(
                request,
                f"Certificate installed but nginx reload failed: {exc}",
            )

    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        logger.error("upload_own_cert: %s", exc, exc_info=True)
        messages.error(request, f"Upload failed: {exc}")

    return redirect("cert_settings")


# ---------------------------------------------------------------------------
# Self-signed certificate generation
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def generate_self_signed(request):
    """Generate a fresh self-signed certificate (10-year validity)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "ProxMigrate"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ProxMigrate"),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True,
            )
            .sign(key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

        install_cert_and_key(cert_pem, key_pem)
        logger.info("Self-signed certificate regenerated by %s", request.user)

        try:
            reload_nginx()
            messages.success(
                request,
                "New self-signed certificate generated and nginx reloaded.",
            )
        except RuntimeError as exc:
            messages.warning(
                request,
                f"Certificate generated but nginx reload failed: {exc}",
            )

    except Exception as exc:
        logger.error("generate_self_signed: %s", exc, exc_info=True)
        messages.error(request, f"Certificate generation failed: {exc}")

    return redirect("cert_settings")


# ---------------------------------------------------------------------------
# Port change
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def change_port(request):
    """Change the HTTPS port nginx listens on and update .env."""
    new_port_str = request.POST.get("web_port", "").strip()

    try:
        new_port = int(new_port_str)
        if not (1 <= new_port <= 65535):
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, "Invalid port number. Must be between 1 and 65535.")
        return redirect("cert_settings")

    current_port = get_current_port()
    if new_port == current_port:
        messages.info(request, f"Port is already {new_port}.")
        return redirect("cert_settings")

    nginx_conf = find_nginx_conf()
    if not nginx_conf:
        messages.error(request, "Cannot find nginx configuration file.")
        return redirect("cert_settings")

    try:
        with open(nginx_conf) as f:
            original = f.read()

        updated = re.sub(
            r"(listen\s+)\d+(\s+ssl)", rf"\g<1>{new_port}\2", original,
        )

        if updated == original:
            messages.warning(
                request,
                "Could not locate the listen directive in the nginx config.",
            )
            return redirect("cert_settings")

        write = subprocess.run(
            ["sudo", "tee", nginx_conf],
            input=updated.encode(),
            capture_output=True, shell=False,
        )
        if write.returncode != 0:
            messages.error(
                request,
                f"Failed to write nginx config: {write.stderr.decode().strip()}",
            )
            return redirect("cert_settings")

        test = subprocess.run(
            ["sudo", "nginx", "-t"],
            capture_output=True, text=True, shell=False,
        )
        if test.returncode != 0:
            subprocess.run(
                ["sudo", "tee", nginx_conf],
                input=original.encode(), capture_output=True, shell=False,
            )
            messages.error(
                request,
                f"nginx config test failed (rolled back): {test.stderr.strip()}",
            )
            return redirect("cert_settings")

        if os.path.exists(ENV_FILE):
            with open(ENV_FILE) as f:
                env = f.read()
            new_env = re.sub(
                r"^WEB_PORT=\d+", f"WEB_PORT={new_port}", env,
                flags=re.MULTILINE,
            )
            if new_env == env:
                new_env = env.rstrip("\n") + f"\nWEB_PORT={new_port}\n"
            with open(ENV_FILE, "w") as f:
                f.write(new_env)

        reload_nginx()
        logger.info(
            "HTTPS port changed %d -> %d by %s",
            current_port, new_port, request.user,
        )

        host = request.get_host().split(":")[0]
        return redirect(f"https://{host}:{new_port}/settings/certificates/")

    except Exception as exc:
        logger.error("change_port: %s", exc, exc_info=True)
        messages.error(request, f"Port change failed: {exc}")
        return redirect("cert_settings")


# ---------------------------------------------------------------------------
# ACME views
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def acme_configure(request):
    """Save ACME settings and register an account with the CA."""
    from apps.certificates import acme
    from apps.certificates.tasks import _get_verify
    from apps.certificates.tasks import _cleanup_ca_bundle

    config = AcmeConfig.get_config()

    provider = request.POST.get("provider", "letsencrypt")
    domain = request.POST.get("domain", "").strip()
    email = request.POST.get("email", "").strip()
    challenge_type = request.POST.get("challenge_type", "http-01")
    ca_bundle = request.POST.get("ca_bundle", "").strip()
    skip_tls = request.POST.get("skip_tls_verify") == "on"

    if not domain:
        messages.error(request, "Domain is required.")
        return redirect("cert_settings")

    # Auto-fill directory URL from provider preset
    if provider in DIRECTORY_URLS:
        directory_url = DIRECTORY_URLS[provider]
    else:
        directory_url = request.POST.get("directory_url", "").strip()
        if not directory_url:
            messages.error(request, "Directory URL is required for custom providers.")
            return redirect("cert_settings")

    config.provider = provider
    config.directory_url = directory_url
    config.domain = domain
    config.email = email
    config.challenge_type = challenge_type
    config.ca_bundle = ca_bundle
    config.skip_tls_verify = skip_tls
    config.save()

    # Register ACME account
    if not config.acme_account_key_pem:
        key_pem = acme.generate_account_key()
        config.acme_account_key_pem = key_pem.decode("utf-8")
        config.save(update_fields=["acme_account_key_pem", "updated_at"])

    verify = _get_verify(config)
    try:
        account_url = acme.register_account(
            config.acme_account_key_pem,
            config.directory_url,
            email=config.email or None,
            verify=verify,
        )
        config.acme_account_url = account_url
        config.save(update_fields=["acme_account_url", "updated_at"])
        AcmeLog.log("account_registered", f"Account: {account_url}")
        AcmeLog.log("config_changed", f"Configured by {request.user}")
        messages.success(
            request,
            f"ACME configured and account registered with "
            f"{config.get_provider_display()}.",
        )
    except Exception as exc:
        logger.error("ACME account registration failed: %s", exc)
        messages.error(request, f"Account registration failed: {exc}")
    finally:
        _cleanup_ca_bundle(verify)

    return redirect("cert_settings")


@_staff_required
@require_POST
def acme_issue(request):
    """Trigger ACME certificate issuance as a background task."""
    from apps.certificates.tasks import issue_acme_certificate

    config = AcmeConfig.get_config()
    if not config.domain or not config.acme_account_url:
        messages.error(request, "ACME is not configured. Configure it first.")
        return redirect("cert_settings")

    issue_acme_certificate.delay()
    AcmeLog.log("renewal_triggered", f"Manual issuance by {request.user}")
    messages.info(
        request,
        "Certificate issuance started. This page will update when complete.",
    )
    return redirect("cert_settings")


@_staff_required
def acme_status(request):
    """HTMX endpoint: return current ACME status as an HTML partial."""
    config = AcmeConfig.get_config()
    cert_info = get_cert_info()

    cert_days_remaining = None
    if cert_info and "not_after" in cert_info:
        delta = cert_info["not_after"] - datetime.datetime.now(datetime.timezone.utc)
        cert_days_remaining = delta.days

    return render(request, "certificates/partials/acme_status.html", {
        "acme": config,
        "cert_days_remaining": cert_days_remaining,
        "acme_logs": AcmeLog.objects.all()[:5],
    })


@_staff_required
@require_POST
def acme_dns_confirm(request):
    """User confirms the DNS TXT record has been created."""
    broker_url = getattr(settings, "CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    r = redis.Redis.from_url(broker_url)
    r.set(REDIS_DNS_CONFIRM_KEY, "1", ex=3600)

    messages.info(request, "DNS confirmation sent. Waiting for validation...")
    return redirect("cert_settings")


@_staff_required
@require_POST
def acme_disable(request):
    """Disable ACME automation. Keeps the current certificate in place."""
    config = AcmeConfig.get_config()
    config.is_enabled = False
    config.save(update_fields=["is_enabled", "updated_at"])
    AcmeLog.log("acme_disabled", f"Disabled by {request.user}")
    messages.success(request, "ACME automation disabled. Current certificate is unchanged.")
    return redirect("cert_settings")


@_staff_required
@require_POST
def acme_reset(request):
    """Reset ACME configuration so the user can reconfigure from scratch."""
    config = AcmeConfig.get_config()
    config.is_enabled = False
    config.provider = "letsencrypt"
    config.directory_url = DIRECTORY_URLS["letsencrypt"]
    config.domain = ""
    config.email = ""
    config.challenge_type = "http-01"
    config.acme_account_key_pem = ""
    config.acme_account_url = ""
    config.ca_bundle = ""
    config.skip_tls_verify = False
    config.dns_txt_value = ""
    config.dns_challenge_pending = False
    config.last_renewal_error = ""
    config.save()
    AcmeLog.log("config_changed", f"Configuration reset by {request.user}")
    messages.success(request, "ACME configuration reset. You can now reconfigure.")
    return redirect("cert_settings")
