# Authentication Settings — Microsoft Entra ID

ProxOrchestrator supports single sign-on (SSO) with Microsoft Entra ID (formerly Azure Active Directory). Users can sign in with their Microsoft work or school accounts via the standard OAuth2/OIDC flow.

## How Entra ID authentication works

1. User clicks "Sign in with Microsoft" on the ProxOrchestrator login page
2. Browser redirects to Microsoft's login page
3. User authenticates with their Microsoft credentials (MFA applies if configured in your tenant)
4. Microsoft redirects back to ProxOrchestrator with an authorization code
5. ProxOrchestrator exchanges the code for user identity information
6. ProxOrchestrator creates or updates the user's local account and logs them in

## Prerequisites

You must have access to the Microsoft Entra ID portal and permission to create App Registrations. Usually requires the **Application Administrator** or **Global Administrator** role in your tenant.

## Azure App Registration setup

### Step 1: Create the App Registration
1. Open [portal.azure.com](https://portal.azure.com)
2. Navigate to **Microsoft Entra ID** → **App registrations** → **New registration**
3. **Name:** ProxOrchestrator (or any descriptive name)
4. **Supported account types:** Choose based on your needs:
   - "Single tenant" — Only your organization's users (most common)
   - "Multi-tenant" — Users from any Microsoft tenant
5. **Redirect URI:** Select "Web" and paste the URI shown in ProxOrchestrator's settings page

### Step 2: Get the IDs
After creating the registration, the Overview page shows:
- **Application (client) ID** — Copy this into ProxOrchestrator's "Client ID" field
- **Directory (tenant) ID** — Copy this into ProxOrchestrator's "Tenant ID" field

### Step 3: Create a client secret
1. Go to **Certificates & secrets** in the left sidebar
2. Click **New client secret**
3. Add a description (e.g., "ProxOrchestrator") and set an expiry
4. Click **Add**
5. **Copy the "Value" column immediately** — it's only shown once

Paste the secret value into ProxOrchestrator's "Client Secret" field.

### Step 4: Configure API permissions (optional)
For group-based admin assignment, ProxOrchestrator needs to read group membership. In your App Registration:
1. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**
2. Add `GroupMember.Read.All`
3. Click **Grant admin consent**

## Redirect URI

The redirect URI must match exactly what's registered in Azure. ProxOrchestrator shows you the correct URI on the settings page. It follows the format:
`https://YOUR-HOST/accounts/microsoft/login/callback/`

If you change ProxOrchestrator's port or hostname, you'll need to update this URI in Azure too.

## Allowed Domains

Optionally restrict sign-in to specific email domains. For example, entering `contoso.com` means only users with `@contoso.com` Microsoft accounts can sign in. Leave blank to allow all users in your tenant.

## Admin Group Object ID

Members of this Azure AD group will automatically be granted ProxOrchestrator admin access on each login. To find a group's Object ID:
1. In Entra ID portal → **Groups**
2. Find your group and click it
3. Copy the "Object ID" from the overview page

## Client secret expiry

Azure client secrets have an expiry date (max 2 years). When your secret expires, Entra ID login will fail. Set a calendar reminder before expiry to create a new secret and update ProxOrchestrator's settings.

## Common issues

**"AADSTS70011: The provided request must include a 'redirect_uri' input"** — The redirect URI in ProxOrchestrator's Entra ID settings page wasn't registered in Azure, or there's a mismatch (trailing slash, http vs https). Copy the exact URI from ProxOrchestrator and paste it into Azure.

**Users can log in but lose access after Azure group changes** — Entra ID group membership is checked at login time. Users must log out and back in for group membership changes to take effect.

**"AADSTS50194: Application is not configured as a multi-tenant application"** — You registered as single-tenant but users from other tenants are trying to log in. Change the supported account types in Azure or enable "multi-tenant" mode.

**Client secret expired** — Create a new client secret in Azure (Certificates & secrets → New client secret), copy the value, and update it in ProxOrchestrator's Entra ID settings. You don't need to change the tenant ID or client ID.
