# Changelog

All notable changes to Data Mover are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A data-movement workspace for configuring, saving, loading, and rerunning owner-scoped pipeline
  definitions.
- Real-time simulated transfer feedback with stage state, progress, record/byte metrics,
  throughput, elapsed time, batch activity, reconciliation, and run logs.
- Synthetic provider catalogs with selectable schemas/databases and tables/collections for Advana
  (Databricks), MSS (Palantir Foundry), PostgreSQL, and MongoDB.
- Provider-specific connection bundles, simulated save/retest validation, a consolidated status
  page, and a Databricks compute wake action for Advana.
- CSV pipeline sources with owner-scoped storage, UTF-8 and shape validation, column discovery,
  conservative data-type inference, completeness, and examples.
- PostgreSQL and MongoDB connection fields for host, port, database, username, password, and
  transport/authentication options.
- Connection-aware pipeline selectors and fail-closed save/run controls that exclude providers
  until the current user has saved and validated them.
- A development-only `seed-demo-connections` command and `make demo` launcher that populate four
  conspicuously fake encrypted connection bundles for local exploration.
- A complete [Data Mover user guide](docs/user-guide.md) and updated operator, deployment, security,
  troubleshooting, and contributor documentation.
- Workbench and disposable Connect deployment steps that seed all four fake connections before the
  demo starts or is bundled.

### Changed

- Reframed the product UI and documentation around Data Mover data movement rather than token
  management.
- Moved Password, Sessions, and user Activity from Connections into Account, leaving Connections
  focused on remote credentials and status.
- Removed the ADE connection type.

## [0.1.0] — 2026-08-06

### Added

- Initial public packaging: MIT license, adopter-first README, and operator docs under `docs/`
  (auth modes, deploy, troubleshooting, FAQ, architecture).
- `CONTRIBUTING.md`, expanded migrations README, and day-to-day Makefile targets.
- Browser HTMX UI (FastAPI + Hedron) for administrator-approved government-email accounts.
- Local password and trusted-header authentication modes.
- Encrypted Advana, MSS, PostgreSQL, and MongoDB credential slots, plus the admin directory,
  invitations, and audit log.
- Queued email delivery with supervised worker CLI.
- Alembic migrations with optional `--adopt-existing` for verified legacy schemas.
- Security architecture and decision register (`SECURITY.md`) with production gate and
  vulnerability reporting guidance.

[0.1.0]: https://github.com/eddiethedean/user-token-management-app/releases/tag/v0.1.0
