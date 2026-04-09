"""
Certificate management helper functions.

Shared by views.py and tasks.py — file I/O, nginx operations,
certificate parsing. No HTTP request/response handling.
"""

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

CERT_DIR = "/opt/proxorchestrator/certs"
CERT_FILE = os.path.join(CERT_DIR, "proxorchestrator.crt")
KEY_FILE = os.path.join(CERT_DIR, "proxorchestrator.key")
CSR_KEY_FILE = os.path.join(CERT_DIR, "proxorchestrator.csr.key")
PENDING_CSR_FILE = os.path.join(CERT_DIR, "pending.csr")
ENV_FILE = "/opt/proxorchestrator/.env"

NGINX_CONF_PATHS = [
    "/etc/nginx/sites-available/proxorchestrator",
    "/etc/nginx/conf.d/proxorchestrator.conf",
]


def get_cert_info():
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
            san_ext = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName,
            )
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
            "san": ", ".join(san_parts) or "\u2014",
            "is_self_signed": cert.subject == cert.issuer,
        }
    except Exception as exc:
        logger.warning("get_cert_info: %s", exc)
        return {"error": str(exc)}


def get_pending_csr():
    """Return the pending CSR PEM text, or None."""
    if not os.path.exists(PENDING_CSR_FILE):
        return None
    try:
        with open(PENDING_CSR_FILE, "rb") as f:
            return f.read().decode("utf-8")
    except OSError:
        return None


def find_nginx_conf():
    """Return the first nginx config path that exists, or None."""
    for p in NGINX_CONF_PATHS:
        if os.path.exists(p):
            return p
    return None


def get_current_port():
    """Read the WEB_PORT from .env. Defaults to 8443."""
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


def reload_nginx():
    """Reload nginx. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["sudo", "nginx", "-s", "reload"],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nginx reload failed: {result.stderr.strip()}")


def install_cert_and_key(cert_content, key_content):
    """Write certificate and key files to disk."""
    os.makedirs(CERT_DIR, exist_ok=True)
    with open(CERT_FILE, "wb") as f:
        f.write(cert_content)
    with open(KEY_FILE, "wb") as f:
        f.write(key_content)
    os.chmod(KEY_FILE, 0o600)


def validate_cert_key_pair(cert_content, key_content):
    """Raise ValueError if cert and key public keys do not match."""
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    cert = x509.load_pem_x509_certificate(cert_content, default_backend())
    key = load_pem_private_key(key_content, password=None)

    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise ValueError("Certificate and private key do not match.")
