# Password Recovery

## Requirements

Password recovery requires **email to be configured** in ProxOrchestrator. Without email, reset links cannot be sent.

To configure email, go to **Settings > Email** and set up either:
- **SMTP** — standard email server
- **Microsoft Graph API** — Azure AD / Microsoft 365

## How It Works

1. Click **"Forgot your password?"** on the sign-in page
2. Enter the email address associated with your account
3. If a matching local account exists, a reset link is sent to that email
4. Click the link in the email to set a new password
5. The link expires after a short time and can only be used once

## Important Notes

- **Local accounts only** — LDAP and Entra ID (Azure AD) users manage their passwords through their directory. The reset link will not be sent for these accounts.
- **Security** — the page always shows the same success message regardless of whether the email exists, to prevent account enumeration.
- **Expired links** — if a link has expired, you can request a new one from the forgot password page.
