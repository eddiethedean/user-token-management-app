# Authentication modes

Data Mover supports two values for `AUTHENTICATION_MODE`. Choose one per deployment; do not
mix client trust models.

| | `local_password` | `trusted_header` |
|--|------------------|------------------|
| Who authenticates | The app verifies an Argon2 (or configured) password hash | An approved identity-aware proxy authenticates (CAC/MFA/federation); the app trusts a single injected header |
| Header involved | None for identity | `TRUSTED_IDENTITY_HEADER` (default `x-access-registry-user`) must carry the normalized account email |
| Password recovery | Forgot-password + reset emails | Disabled in the UI; identity recovery is owned by the proxy IdP |
| Invitation / registration | Can set an initial password | Password fields omitted; account binds to federated email |
| Production posture | Requires documented risk acceptance (`PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=true`) if AAL2/phishing-resistant auth is required for the data in scope | Preferred when privacy/security officials require phishing-resistant authentication |
| When to choose | Local labs, isolated networks with accepted residual risk, or transitional cutover | Production behind Connect/gateway with CAC/MFA already approved |

## Production requirements (both modes)

- HTTPS `PUBLIC_BASE_URL`, `COOKIE_SECURE=true`, PostgreSQL, SMTP, rate limits enabled
- Exact `ALLOWED_EMAIL_DOMAINS`
- High-entropy JWT / session / CSRF / connection-credential encryption secrets from an approved
  store (`API_TOKEN_ENCRYPTION_KEYS` retains its legacy configuration name)

See the [production security gate](../SECURITY.md#production-security-gate) for the full checklist.

## `trusted_header` deployment controls

The proxy **must**:

1. Strip every client-supplied copy of the identity header.
2. Authenticate the user with the approved method.
3. Inject exactly one normalized email into `TRUSTED_IDENTITY_HEADER`.
4. Be the only path that can reach the application (no direct app-server access).

Spoofing that header past the proxy is a complete authentication bypass. Test that clients cannot
set it.

## Switching modes

Changing `AUTHENTICATION_MODE` is a configuration change with operational impact:

- Moving to `trusted_header` disables password login and password-change UI.
- Moving to `local_password` requires every active user to have a password (use invitations or
  `create-admin` / admin flows as appropriate).
- Revoke existing sessions after a mode change (`create-admin` on a user also revokes sessions).

Directory lookup (`DIRECTORY_LOOKUP_*`) is **eligibility**, not authentication, in either mode.
