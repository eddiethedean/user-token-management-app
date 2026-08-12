# Posit Workbench Docker integration

Local stack that boots a licensed [Posit Workbench](https://hub.docker.com/r/posit/workbench)
image beside Access Registry, plus a small proxy that reproduces the SOCOM
`Location: /s/…` → `/proxy/8000/s/…` rewrite.

## Prerequisites

1. Docker Desktop / Engine
2. A Workbench trial/eval key in `.env` (never commit it):

```bash
POSIT_WORKBENCH_KEY=XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
```

3. Project venv with app deps installed (`make install`)

## Commands

```bash
make workbench-up      # pull/build and wait for healthy
make workbench-test    # opt-in pytest module
make workbench-logs
make workbench-down    # graceful stop (license key deactivation)
```

## Posit Connect deployment smoke test

With a `CONNECT_LICENSE` key in the repository `.env`, run:

```bash
make connect-smoke
```

The harness starts an isolated Connect 2025.06.0 container with Python 3.11.7 on a private Docker
network. Only an Nginx sidecar is exposed; it overwrites `X-Access-Registry-Cookie` from the browser's
real Cookie header. The harness creates a temporary SQLite bundle, publishes the main FastAPI app
with its explicitly gated cookie bridge, and exercises health, login, cookie path, authenticated
profile, and safe content-log diagnostics. Its exit trap explicitly deactivates the license before
stopping Connect, then verifies removal of both containers, the private network, Docker data volume,
and temporary bundle. It never prints the license key, application secrets, passwords, API keys, or
cookie values.

This proxy is necessary because Connect 2025.06.0 accepts the application's `Set-Cookie` response
but removes the application cookie before the next request reaches FastAPI. The bridge retains the
application's user management; it does not consume Connect credentials as application identity.

Ports (defaults):

| Service | Host |
|---------|------|
| Workbench UI | http://127.0.0.1:8787 |
| Access Registry | http://127.0.0.1:8000 |
| Location-rewrite proxy | http://127.0.0.1:8788 |

Workbench login defaults: user `posit` / password `Xk9#mQ2$vL8!nR4p` (PAM-safe).
Access Registry admin seed: `admin@example.gov` / `Tr0pic-Maple!River92`.

## What the tests cover

1. Real Workbench `/health-check` shows an activated license
2. Sign-in page + `/auth-public-key` RSA material
3. `rstudio-server version` and redacted license-manager status inside the container
4. `/home` redirects into a real `/s/<id>/workspaces/` mount
5. RSA-encrypted login as `PWB_TESTUSER` reaches an authenticated surface
6. Access Registry health/ready, mounted login HTML/CSS/JS, CSRF cookie path
7. Real admin login (`ADMIN_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD`), wrong-password rejection, logout
8. Authenticated profile/security pages; profile update; Advana secret save/delete
9. Admin users + audit pages (auth gate + authenticated access)
10. Public register / forgot-password pages; forgot-password queues outbox mail
11. Invite → accept → login; register → verify → approve → login; disable user
12. Disallowed invitation domain rejected; login through Location-rewrite proxy
13. Scheme-absolute redirects survive the SOCOM-style Location rewrite simulator

Default password is ``Xk9#mQ2$vL8!nR4p`` (PAM rejects short / dictionary / username-containing
passwords). Override with ``PWB_TESTUSER_PASSWD`` if needed.

`make workbench-test` resets the Workbench **home** volume by default so the test user
is recreated with that password (`ACCESS_REGISTRY_WORKBENCH_RESET=0` to keep it).
License volumes are preserved. Always `make workbench-down` (not `docker kill`).

## License caution

Posit warns that license **keys** can leak activations on `docker kill` / crashes.
Prefer a license file for long-lived use; always `make workbench-down` (120s grace).
See [Posit Workbench container docs](https://github.com/posit-dev/images-workbench/blob/main/workbench/README.md).
