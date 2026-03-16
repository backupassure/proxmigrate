import datetime
import ipaddress
import logging
import os
import re
import subprocess

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

CERT_DIR = "/opt/proxmigrate/certs"
CERT_FILE = os.path.join(CERT_DIR, "proxmigrate.crt")
KEY_FILE = os.path.join(CERT_DIR, "proxmigrate.key")
CSR_KEY_FILE = os.path.join(CERT_DIR, "proxmigrate.csr.key")
PENDING_CSR_FILE = os.path.join(CERT_DIR, "pending.csr")
ENV_FILE = "/opt/proxmigrate/.env"

NGINX_CONF_PATHS = [
    "/etc/nginx/sites-available/proxmigrate",
    "/etc/nginx/conf.d/proxmigrate.conf",
]


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


def _get_cert_info():
    """Parse the current certificate and return a rich info dict, or None."""
    if not os.path.exists(CERT_FILE):
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        with open(CERT_FILE, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read(), default_backend())

        san_parts = []
        try:
            san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            for name in san_ext.value:
                if isinstance(name, x509.DNSName):
                    san_parts.append(f"DNS:{name.value}")
                elif isinstance(name, x509.IPAddress):
                    san_parts.append(f"IP:{name.value}")
        except x509.ExtensionNotFound:
            pass

        return {
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "not_before": cert.not_valid_before_utc,
            "not_after": cert.not_valid_after_utc,
            "serial": format(cert.serial_number, "X"),
            "san": ", ".join(san_parts) or "—",
            "is_self_signed": cert.subject == cert.issuer,
        }
    except Exception as exc:
        logger.warning("_get_cert_info: %s", exc)
        return {"error": str(exc)}


def _get_pending_csr():
    """Return the pending CSR PEM text, or None."""
    if not os.path.exists(PENDING_CSR_FILE):
        return None
    try:
        with open(PENDING_CSR_FILE, "rb") as f:
            return f.read().decode("utf-8")
    except OSError:
        return None


def _find_nginx_conf():
    for p in NGINX_CONF_PATHS:
        if os.path.exists(p):
            return p
    return None


def _get_current_port():
    try:
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE) as f:
                for line in f:
                    m = re.match(r"^WEB_PORT=(\d+)", line.strip())
                    if m:
                        return int(m.group(1))
    except Exception:
        pass
    return 8443


def _reload_nginx():
    result = subprocess.run(
        ["sudo", "nginx", "-s", "reload"],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nginx reload failed: {result.stderr.strip()}")


def _install_cert_and_key(cert_content, key_content):
    os.makedirs(CERT_DIR, exist_ok=True)
    with open(CERT_FILE, "wb") as f:
        f.write(cert_content)
    with open(KEY_FILE, "wb") as f:
        f.write(key_content)
    os.chmod(KEY_FILE, 0o600)


def _validate_cert_key_pair(cert_content, key_content):
    """Raise ValueError if cert and key public keys do not match."""
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    cert = x509.load_pem_x509_certificate(cert_content, default_backend())
    key = load_pem_private_key(key_content, password=None)

    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise ValueError("Certificate and private key do not match.")


@_staff_required
def cert_settings(request):
    cert_info = _get_cert_info()

    cert_days_remaining = None
    if cert_info and "not_after" in cert_info:
        delta = cert_info["not_after"] - datetime.datetime.now(datetime.timezone.utc)
        cert_days_remaining = delta.days

    return render(request, "certificates/settings.html", {
        "cert_info": cert_info,
        "cert_days_remaining": cert_days_remaining,
        "pending_csr": _get_pending_csr(),
        "has_csr_key": os.path.exists(CSR_KEY_FILE),
        "current_port": _get_current_port(),
        "cert_file": CERT_FILE,
        "key_file": KEY_FILE,
    })


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
        from cryptography.hazmat.primitives import hashes, serialization
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

        # Build SAN list — always include CN as a DNS SAN if it looks like a hostname
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
                x509.SubjectAlternativeName(san_list), critical=False
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
        messages.success(request, f"CSR generated for {cn}. Submit it to your CA, then upload the signed certificate.")

    except Exception as exc:
        logger.error("generate_csr: %s", exc, exc_info=True)
        messages.error(request, f"CSR generation failed: {exc}")

    return redirect("cert_settings")


@_staff_required
@require_POST
def upload_signed_cert(request):
    """Upload a CA-signed certificate that matches the pending CSR private key."""
    if not os.path.exists(CSR_KEY_FILE):
        messages.error(request, "No pending CSR key found. Generate a CSR first.")
        return redirect("cert_settings")

    cert_file = request.FILES.get("signed_cert")
    if not cert_file:
        messages.error(request, "Please select the signed certificate file.")
        return redirect("cert_settings")

    try:
        cert_content = cert_file.read()
        with open(CSR_KEY_FILE, "rb") as f:
            key_content = f.read()

        _validate_cert_key_pair(cert_content, key_content)
        _install_cert_and_key(cert_content, key_content)

        for path in (CSR_KEY_FILE, PENDING_CSR_FILE):
            try:
                os.unlink(path)
            except OSError:
                pass

        logger.info("Signed certificate installed by %s", request.user)

        try:
            _reload_nginx()
            messages.success(request, "Certificate installed and nginx reloaded successfully.")
        except RuntimeError as exc:
            messages.warning(request, f"Certificate installed but nginx reload failed: {exc}")

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
        messages.error(request, "Both certificate and private key files are required.")
        return redirect("cert_settings")

    try:
        cert_content = cert_file.read()
        key_content = key_file.read()

        _validate_cert_key_pair(cert_content, key_content)
        _install_cert_and_key(cert_content, key_content)

        logger.info("Certificate and key uploaded by %s", request.user)

        try:
            _reload_nginx()
            messages.success(request, "Certificate installed and nginx reloaded successfully.")
        except RuntimeError as exc:
            messages.warning(request, f"Certificate installed but nginx reload failed: {exc}")

    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        logger.error("upload_own_cert: %s", exc, exc_info=True)
        messages.error(request, f"Upload failed: {exc}")

    return redirect("cert_settings")


@_staff_required
@require_POST
def generate_self_signed(request):
    """Generate a fresh self-signed certificate (10-year validity)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
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
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

        _install_cert_and_key(cert_pem, key_pem)
        logger.info("Self-signed certificate regenerated by %s", request.user)

        try:
            _reload_nginx()
            messages.success(request, "New self-signed certificate generated and nginx reloaded.")
        except RuntimeError as exc:
            messages.warning(request, f"Certificate generated but nginx reload failed: {exc}")

    except Exception as exc:
        logger.error("generate_self_signed: %s", exc, exc_info=True)
        messages.error(request, f"Certificate generation failed: {exc}")

    return redirect("cert_settings")


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

    current_port = _get_current_port()
    if new_port == current_port:
        messages.info(request, f"Port is already {new_port}.")
        return redirect("cert_settings")

    nginx_conf = _find_nginx_conf()
    if not nginx_conf:
        messages.error(request, "Cannot find nginx configuration file.")
        return redirect("cert_settings")

    try:
        with open(nginx_conf) as f:
            original = f.read()

        updated = re.sub(r"(listen\s+)\d+(\s+ssl)", rf"\g<1>{new_port}\2", original)

        if updated == original:
            messages.warning(request, "Could not locate the listen directive in the nginx config.")
            return redirect("cert_settings")

        # Write via sudo tee
        write = subprocess.run(
            ["sudo", "tee", nginx_conf],
            input=updated.encode(),
            capture_output=True, shell=False,
        )
        if write.returncode != 0:
            messages.error(request, f"Failed to write nginx config: {write.stderr.decode().strip()}")
            return redirect("cert_settings")

        # Validate — roll back on failure
        test = subprocess.run(
            ["sudo", "nginx", "-t"],
            capture_output=True, text=True, shell=False,
        )
        if test.returncode != 0:
            subprocess.run(["sudo", "tee", nginx_conf],
                           input=original.encode(), capture_output=True, shell=False)
            messages.error(request, f"nginx config test failed (rolled back): {test.stderr.strip()}")
            return redirect("cert_settings")

        # Update .env
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE) as f:
                env = f.read()
            new_env = re.sub(r"^WEB_PORT=\d+", f"WEB_PORT={new_port}", env, flags=re.MULTILINE)
            if new_env == env:
                new_env = env.rstrip("\n") + f"\nWEB_PORT={new_port}\n"
            with open(ENV_FILE, "w") as f:
                f.write(new_env)

        _reload_nginx()
        logger.info("HTTPS port changed %d → %d by %s", current_port, new_port, request.user)

        host = request.get_host().split(":")[0]
        return redirect(f"https://{host}:{new_port}/settings/certificates/")

    except Exception as exc:
        logger.error("change_port: %s", exc, exc_info=True)
        messages.error(request, f"Port change failed: {exc}")
        return redirect("cert_settings")
