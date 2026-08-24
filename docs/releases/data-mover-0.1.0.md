# Data Mover 0.1.0

**Status:** Published repository baseline  
**Date:** 2026-08-06  
**Package:** `access-registry==0.1.0`

## Highlights

- FastAPI + Hedron server-rendered HTMX application with no Node.js build requirement.
- Administrator-approved government-email accounts, local-password and trusted-header modes,
  invitations, audit history, and account security controls.
- Encrypted owner-scoped connection credentials for MSS, MCS-COP, PostgreSQL, and CSV workflows.
- Saved pipelines with catalog-backed source/destination locators and write policies.
- Durable pipeline runs with a separate worker, leases, cancellation, persisted events, and a
  retention janitor.
- Offline demo mode backed by fake connectors that never contact remote endpoints.
- Deployment, authentication, troubleshooting, provider, security, and worker runbooks.

## Security boundaries

Credential values are encrypted at rest and are not rendered in plans, reports, audit events, or
browser fragments. Real-mode workers decrypt selected credentials only inside a claimed run. See
[SECURITY.md](../../SECURITY.md) for the production gate and assurance boundary.

## Upgrade notes

- Existing deployments must run the Alembic migration path before application startup.
- `DATA_MOVER_MODE=demo` is for local exploration; real mode requires the documented provider and
  worker configuration.
- Advana and MongoDB are not first-class transfer providers in this release.
- Review [docs/deploy.md](../deploy.md) and [docs/troubleshooting.md](../troubleshooting.md) before
  upgrading a deployed instance.

## Verification

```bash
make check
python -m app pipeline-worker --once
```

The worker command is safe to run only against the intended environment and database.
