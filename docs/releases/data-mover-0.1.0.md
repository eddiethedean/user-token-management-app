# Data Mover 0.1.0

**Status:** Historical repository baseline (no Git tag or external package publication asserted)
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

This note describes the 0.1.0 repository snapshot. The current runtime is in-process; the
separate-worker wording and command examples below are retained only as historical context.

## Security boundaries

Credential values are encrypted at rest and are not rendered in plans, reports, audit events, or
browser fragments. In this snapshot, real-mode workers decrypted selected credentials only inside a
claimed run. Current catalog browsing and connection tests are also bounded, owner-authorized
credential-use actions. See
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
```

The `pipeline-worker` command shown in earlier release notes is no longer part of the current CLI;
the web process owns transfer execution and recovery.
