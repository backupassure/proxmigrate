# Certificate Management

ProxOrchestrator serves its web interface over HTTPS. This page lets you view the current TLS certificate and replace it with your own.

## Current certificate information

The certificate details panel shows:
- **Subject** — The certificate's Common Name (CN), typically the hostname or IP address the certificate was issued for
- **Issuer** — Who signed the certificate. "Self-Signed" means the certificate was signed by itself (common for internal tools). A CA name means it was issued by a certificate authority.
- **Valid From / Until** — The certificate's validity window. The expiry date is color-coded:
  - **Green** — More than 30 days remaining
  - **Orange/Yellow** — Fewer than 30 days remaining — plan to renew soon
  - **Red** — Expired — browsers will show security warnings to all users

## Self-signed vs CA-signed certificates

**Self-signed certificates** (default) are generated automatically during installation. They provide encryption but browsers will show a "Your connection is not private" warning because the certificate isn't signed by a trusted CA. Users must manually accept the warning.

**CA-signed certificates** from a trusted certificate authority (Let's Encrypt, DigiCert, your internal PKI, etc.) are trusted by browsers without warnings. Strongly recommended for production use.

## Replacing the certificate

Upload both:
1. **Certificate file** — PEM-encoded certificate, preferably with the full chain (server certificate + any intermediate certificates). File extensions: `.pem`, `.crt`, `.cer`
2. **Private key file** — PEM-encoded RSA or ECDSA private key. The key must match the certificate. File extensions: `.pem`, `.key`

**Important requirements:**
- The private key must NOT be password-protected (no passphrase)
- PEM format only (not DER/binary format)
- The certificate must cover the hostname users use to access ProxOrchestrator

After uploading, ProxOrchestrator validates the certificate/key pair and restarts Nginx. You may need to wait 5–10 seconds and reload the page.

## Getting a free certificate from Let's Encrypt

If your ProxOrchestrator server is publicly accessible, you can get a free, browser-trusted certificate from Let's Encrypt:

```
certbot certonly --standalone -d proxorchestrator.yourdomain.com
```

The certificate and key will be in `/etc/letsencrypt/live/proxorchestrator.yourdomain.com/`. Use:
- `fullchain.pem` as the certificate file (includes chain)
- `privkey.pem` as the private key file

Let's Encrypt certificates are valid for 90 days. Set up auto-renewal with `certbot renew` in a cron job.

## Internal PKI / Active Directory Certificate Services

If your organization runs its own CA (common in enterprise environments):
1. Request a certificate for the ProxOrchestrator server's hostname
2. Export the certificate and private key as PEM files
3. Upload them here

Ensure the root and intermediate CAs are trusted by your users' browsers (they usually are in domain-joined environments via GPO).

## Generating a new self-signed certificate

Click "Generate Self-Signed Certificate" to create a fresh self-signed certificate. This is useful if the current self-signed certificate has expired. The new certificate will be valid for 10 years.

## Certificate covers hostname/IP mismatch

If users access ProxOrchestrator by IP address but the certificate's Subject only covers the hostname (or vice versa), browsers will show a warning. Ensure the certificate's Subject Alternative Names (SANs) include all hostnames and IP addresses users access.

## Common issues

**Certificate and key don't match** — ProxOrchestrator validates this before saving. If they don't match, you'll see an error. Ensure you're uploading the private key that corresponds to the uploaded certificate.

**"Certificate is not valid for this hostname"** — The certificate was issued for a different hostname. Request a new certificate for the correct hostname, or add the current hostname as a SAN.

**Nginx fails to restart after certificate upload** — Check the ProxOrchestrator logs. The certificate or key may have invalid formatting even if it appears correct. Try converting with: `openssl x509 -in cert.crt -out cert.pem` and `openssl rsa -in key.key -out key.pem`.
