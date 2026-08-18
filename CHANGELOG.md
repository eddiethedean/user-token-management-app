# Changelog

All notable changes to Data Mover are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Semblance simulators for Foundry (MSS/MCS-COP) and Advana/Databricks HTTP contracts in tests.
- Ephemeral PostgreSQL connector tests via [testing.postgresql](https://pypi.org/project/testing.postgresql/).
- Ephemeral MongoDB contract tests via [pytest-mongo](https://pypi.org/project/pytest-mongo/) (not a product connector).
- Real MSS, MCS-COP, PostgreSQL, and CSV connectors with Polars batches, a durable pipeline worker,
  and HTMX polling against persisted run events.
- Versioned locators/write policies, run leases, cancellation, retention janitor, and feature flags
  for Foundry writers.
- Provider protocol notes and sanitized fixtures under `docs/providers/` and `tests/fixtures/providers/`.
- Offline demo mode with fake connectors; production refuses `DATA_MOVER_MODE=demo`.

### Fixed

- PostgreSQL session timeouts are set with literals (utility `SET` does not accept parameters), and
  COPY treats empty CSV fields as NULL so numeric/date nulls load.

### Changed

- Connections are limited to MSS, MCS-COP, and PostgreSQL. Advana and MongoDB leave the UI; existing
  encrypted rows are preserved.
- Save stores credentials as untested; Test is a distinct connector health check. Wake cluster is
  removed.
- Pipeline runs enqueue to the worker instead of using a browser-side simulator.
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
