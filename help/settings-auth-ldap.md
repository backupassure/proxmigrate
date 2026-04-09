# Authentication Settings — LDAP / Active Directory

ProxOrchestrator supports LDAP authentication so your team can sign in with their existing directory credentials (Active Directory, OpenLDAP, FreeIPA, etc.).

## How LDAP authentication works

1. User enters their AD/LDAP username and password on the ProxOrchestrator login page
2. ProxOrchestrator connects to your LDAP server and searches for the user record
3. ProxOrchestrator attempts to bind (authenticate) as the user with their password
4. If successful, the user is logged into ProxOrchestrator
5. If group restrictions are configured, ProxOrchestrator checks group membership before allowing access

## LDAP Server URI

The URI must follow the format: `ldap://hostname:port` or `ldaps://hostname:port`

- `ldap://dc.example.com:389` — unencrypted or STARTTLS
- `ldaps://dc.example.com:636` — SSL/TLS (LDAPS)

For Active Directory, use your domain controller's hostname or IP. In clustered environments, use a load-balanced LDAP endpoint or your AD domain name (e.g., `ldap://example.com`).

## Bind DN and Password

The Bind DN is the Distinguished Name of a service account that ProxOrchestrator uses to search the directory. This account needs read access to user and group objects.

Example Active Directory bind DN:
`CN=proxorchestrator-svc,OU=Service Accounts,DC=example,DC=com`

Example OpenLDAP bind DN:
`uid=proxorchestrator,ou=service,dc=example,dc=com`

Use a dedicated service account with minimal permissions — read-only access to users and groups is sufficient.

## User Search Base and Filter

**User Search Base** — The Distinguished Name of the container to search for users. To search the entire directory: `DC=example,DC=com`. To restrict to a specific OU: `OU=Staff,DC=example,DC=com`.

**User Search Filter** — LDAP filter to find a user by their login name. For Active Directory, the standard filter is:
`(sAMAccountName=%(user)s)`

For OpenLDAP, typically:
`(uid=%(user)s)`

`%(user)s` is replaced by the username the person typed at login.

## Group restrictions

**Require Group** — Only users who are members of this LDAP group will be allowed to log in. If blank, all users found in the search base can log in. Enter the full Distinguished Name of the group.

Example: `CN=ProxOrchestratorUsers,OU=Groups,DC=example,DC=com`

**Admin Group** — Users who are members of this group will automatically be granted ProxOrchestrator administrator access. If you make someone an admin in AD by adding them to this group, their ProxOrchestrator access updates on next login.

## TLS options

**STARTTLS** — Upgrades an unencrypted LDAP connection to encrypted using STARTTLS. Use with `ldap://` URI on port 389 when your AD supports STARTTLS (most modern AD does). This is preferred over LDAPS for non-SSL ports.

**Skip certificate verification** — Disables TLS certificate validation. **Only use this for testing.** In production, ensure your LDAP server's certificate is trusted by ProxOrchestrator's system certificate store.

## Testing the connection

Click "Test Connection" after filling in the settings (without saving). This will attempt to bind to the LDAP server with the service account credentials and perform a test user search. The result shows whether the bind succeeded and how many users are in the search scope.

## Common issues

**"No such object"** — The User Search Base DN doesn't exist. Check for typos in the OU/DC structure.

**"Invalid credentials"** — The bind DN or bind password is wrong. Verify the service account credentials in your AD.

**"Can't contact LDAP server"** — Network issue. Verify the LDAP server is reachable on the specified port from the ProxOrchestrator server.

**Users can log in but aren't getting admin** — Verify the Admin Group DN is exact (case doesn't matter, but structure must be correct). Use an LDAP browser to confirm the group DN.

**"Certificate verify failed"** — Your LDAP server's certificate isn't trusted. Either add the CA to ProxOrchestrator's trust store, use LDAP (non-SSL) with STARTTLS, or enable "Skip certificate verification" temporarily for testing.
