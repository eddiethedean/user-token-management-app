# Changelog

## [Unreleased / Hedron 1.0.0] — 2026-08-30

- Upgraded the runtime and Posit integration to the compatible Hedron 1.0.0 train.
- Migrated dependency-heavy fragment endpoints from the removed `component` route role to
  `HedronRouter.view`.
- Added a narrow Workbench middleware compatibility bridge for the published 1.0.0 Posit adapter
  and its 1.0.1 `fastapi-workbench` dependency.
- Added bounded 0.65 scoped motion/style registration and 0.66 typography presentation profiles.
- Restored the wide desktop canvas and aligned nested auth/workspace surfaces with the selected
  light or dark color mode.
- Refined the desktop art direction with native AmbientCanvas layers, Container width, typography
  tokens, glass surfaces, typed elevation, and bounded active navigation/workflow treatments.
- Persisted each user's light or dark preference and restore it on password and federated sign-in.

## [Unreleased / Hedron 0.60.0] — 2026-08-23

- Upgraded the runtime and Posit integration to Hedron 0.60.0.
- Added validated 0.60 ThemeSpec authoring with modern color input, accessibility modes, aliases,
  typed flow recipe-family metadata, and native theme preference selection.
- Replaced the remaining brand, toast, connector-canvas, and run-log compatibility CSS with
  Hedron 0.60 built-ins; only server-retargeted request-error placement remains application CSS.

## Hedron 0.59.0 — 2026-08-22

### Changed
- Migrated the shared shell, typed controls, navigation, sizing, and pipeline presentation to
  the Hedron 0.59 contract while preserving Data Mover routes, CSRF, HTMX behavior, and domain
  execution ownership.

All notable changes to Data Mover are documented in this file.

Release-note detail is split into the [release-notes index](docs/releases/README.md), including the
[Data Mover 0.1.0 baseline](docs/releases/data-mover-0.1.0.md), the
[Hedron 0.60 repository note](docs/releases/data-mover-hedron-0.60.md), the
[HTMX Framework 0.16 draft](docs/releases/hedron-0.16.0.md), and the
[ETL Beta review](docs/releases/etl-beta-review.md).

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

- Replaced the separate email worker CLI with in-process FastAPI background delivery; retained
  `send-email` and `retry-email` for operator recovery.
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
- Transactional queued email delivery with console and SMTP backends.
- Alembic migrations with optional `--adopt-existing` for verified legacy schemas.
- Security architecture and decision register (`SECURITY.md`) with production gate and
  vulnerability reporting guidance.

[0.1.0]: https://github.com/eddiethedean/user-token-management-app/releases/tag/v0.1.0
