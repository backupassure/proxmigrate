import logging
import os
import shlex
import subprocess

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

CERT_DIR = "/opt/proxmigrate/certs"
CERT_FILE = os.path.join(CERT_DIR, "proxmigrate.crt")
KEY_FILE = os.path.join(CERT_DIR, "proxmigrate.key")


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


def _get_cert_info():
    """Read the current certificate and return expiry and subject info.

    Returns a dict with keys: exists, subject, expiry, error.
    """
    if not os.path.exists(CERT_FILE):
        return {"exists": False, "subject": "", "expiry": "", "error": "Certificate file not found."}

    try:
        # Use cryptography library if available, fall back to openssl subprocess
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend

            with open(CERT_FILE, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            subject = cert.subject.rfc4514_string()
            expiry = cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
            return {"exists": True, "subject": subject, "expiry": expiry, "error": ""}

        except ImportError:
            # Fall back to openssl CLI
            cmd = ["openssl", "x509", "-in", CERT_FILE, "-noout", "-subject", "-enddate"]
            result = subprocess.run(cmd, capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                subject = lines[0].replace("subject=", "").strip() if lines else ""
                expiry = lines[1].replace("notAfter=", "").strip() if len(lines) > 1 else ""
                return {"exists": True, "subject": subject, "expiry": expiry, "error": ""}
            else:
                return {
                    "exists": True,
                    "subject": "",
                    "expiry": "",
                    "error": f"openssl failed: {result.stderr.strip()}",
                }

    except Exception as exc:
        logger.warning("_get_cert_info: %s", exc)
        return {"exists": True, "subject": "", "expiry": "", "error": str(exc)}


def _reload_nginx():
    """Send SIGHUP to nginx to reload configuration without downtime."""
    cmd = ["nginx", "-s", "reload"]
    logger.info("Reloading nginx: %s", shlex.join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if result.returncode != 0:
        raise RuntimeError(f"nginx reload failed: {result.stderr.strip()}")


@_staff_required
@require_http_methods(["GET", "POST"])
def cert_settings(request):
    """Certificate management page.

    GET: Show current certificate info.
    POST: Upload new cert and key files, validate, replace, reload nginx.
    """
    cert_info = _get_cert_info()
    error = None
    success = None

    if request.method == "POST":
        cert_file = request.FILES.get("cert_file")
        key_file = request.FILES.get("key_file")

        if not cert_file or not key_file:
            error = "Both certificate and private key files are required."
        else:
            try:
                cert_content = cert_file.read()
                key_content = key_file.read()

                # Validate cert/key with openssl
                import tempfile

                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".crt"
                ) as tmp_cert, tempfile.NamedTemporaryFile(
                    delete=False, suffix=".key"
                ) as tmp_key:
                    tmp_cert.write(cert_content)
                    tmp_cert.flush()
                    tmp_key.write(key_content)
                    tmp_key.flush()
                    tmp_cert_path = tmp_cert.name
                    tmp_key_path = tmp_key.name

                try:
                    # Check certificate is valid PEM
                    verify_cmd = [
                        "openssl", "verify", tmp_cert_path,
                    ]
                    # Check private key is readable
                    key_check_cmd = [
                        "openssl", "rsa", "-in", tmp_key_path, "-check", "-noout",
                    ]
                    cert_result = subprocess.run(
                        verify_cmd, capture_output=True, text=True, shell=False
                    )
                    key_result = subprocess.run(
                        key_check_cmd, capture_output=True, text=True, shell=False
                    )

                    if key_result.returncode != 0:
                        raise ValueError(f"Invalid private key: {key_result.stderr.strip()}")

                    # Install files
                    os.makedirs(CERT_DIR, exist_ok=True)
                    with open(CERT_FILE, "wb") as f:
                        f.write(cert_content)
                    with open(KEY_FILE, "wb") as f:
                        f.write(key_content)

                    # Secure key file permissions
                    os.chmod(KEY_FILE, 0o600)

                    logger.info("Certificate replaced by %s", request.user)

                    # Reload nginx
                    try:
                        _reload_nginx()
                        success = "Certificate installed and nginx reloaded successfully."
                    except RuntimeError as exc:
                        success = (
                            f"Certificate installed, but nginx reload failed: {exc}. "
                            "You may need to reload nginx manually."
                        )

                    cert_info = _get_cert_info()

                finally:
                    try:
                        os.unlink(tmp_cert_path)
                    except OSError:
                        pass
                    try:
                        os.unlink(tmp_key_path)
                    except OSError:
                        pass

            except (ValueError, OSError) as exc:
                error = str(exc)
                logger.warning("cert_settings upload error: %s", exc)
            except Exception as exc:
                error = f"Unexpected error: {exc}"
                logger.error("cert_settings: %s", exc, exc_info=True)

    return render(
        request,
        "certificates/settings.html",
        {
            "cert_info": cert_info,
            "error": error,
            "success": success,
            "cert_file": CERT_FILE,
            "key_file": KEY_FILE,
        },
    )
