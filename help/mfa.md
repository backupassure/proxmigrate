# Multi-Factor Authentication (MFA)

## Overview

MFA adds a second layer of security to your ProxOrchestrator account. After entering your password, you'll be asked for a 6-digit code from an authenticator app.

## Setting Up MFA

1. Go to **MFA Setup** (or you'll be redirected if MFA is enforced)
2. Scan the QR code with your authenticator app (Google Authenticator, Authy, Microsoft Authenticator)
3. Enter the 6-digit code from the app to verify
4. **Save your recovery codes** — these are one-time backup codes in case you lose your device

## Signing In with MFA

1. Enter your username/email and password as normal
2. You'll be prompted for a 6-digit authenticator code
3. Enter the code from your app — it changes every 30 seconds

## Recovery Options

If you lose access to your authenticator app:

- **Recovery codes** — enter one of the 8 codes you saved during setup (each works once)
- **Email recovery** — click "Email me a recovery code" to receive a one-time code via email (requires email to be configured)
- **Admin reset** — an administrator can reset your MFA from the user management page

## Global Enforcement

Administrators can require all local and LDAP users to set up MFA. When enforced:
- New users are redirected to MFA setup after their first login
- Users cannot disable MFA
- Entra ID (Azure AD) users are excluded — MFA is handled by their identity provider

## Disabling MFA

If MFA is not globally enforced, you can disable it from the MFA Setup page. You'll need to confirm with your password.

## Important Notes

- **Entra ID users** — MFA is managed by your Azure AD tenant, not ProxOrchestrator
- **LDAP users** — MFA in ProxOrchestrator is separate from any MFA on your directory server
- Recovery codes are shown **once** at setup — save them immediately
- Each recovery code can only be used **once**
