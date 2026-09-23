# Changelog

## [Unreleased]

## [230926.0] — 2026-09-23

### Changed

- Hardened duplicate session refresh recovery with a companion proof cookie and added safe retry
  behavior for proofless overlap requests.
- Added user attribution and detailed redacted diagnostics for successful and failed pipeline runs.
- Enabled MSS, MCS-COP, and PostgreSQL as both pipeline sources and destinations by default while
  keeping CSV source-only; operator writer flags can still disable individual remote destinations.
- Connection saves now test new or changed credential bundles automatically. Identical normalized
  submissions preserve the encrypted value and latest health result without retesting.
- Added user-feedback and troubleshooting outcomes for sign-in, account recovery, connection
  readiness, and pipeline execution. Added opaque support references, structured redacted events,
  JSON/level logging configuration, and migration `0017_connection_feedback`.
- Removed the product theme stylesheet and scoped workflow CSS in favor of stock Folio,
  native Hedron recipes, typography, grids, surfaces, process flows, and color-mode controls.
- Balanced shell branding with native container padding and account separation with a Divider.
- Made the workspace header scroll naturally, eliminating sticky-header content overlap.
- Retained only a navigation-collapse compatibility stylesheet because Hedron 1.0.18 does not
  expose toggle appearance/icon props.
- Simplified the sign-in workflow illustration into a compact native icon-and-caption row,
  removing oversized status cards, sequence numbers, and decorative tracks.

## [220926.0] — 2026-09-22

### Changed

- Bumped the application version for the desktop feedback improvements to sign-in support,
  invitation and audit details, and pipeline run status.

## [180926.3] — 2026-09-18

### Changed

- Bumped the release version for the Folio-native desktop visual refinement and verified
  production live-mode banner contrast in light and dark themes.

## [180926.2] — 2026-09-18

### Fixed

- Kept the sticky workspace header opaque while scrolling so page content cannot bleed through
  the header controls.

## [180926.1] — 2026-09-18

### Changed

- Bumped the release version for the Connect styling cache-busting deployment fix.

## [180926.0] — 2026-09-18

### Changed

- Upgraded the Hedron runtime to 1.0.18 while retaining the compatible 1.0 release line.
- Switched the application shell to Hedron's built-in Folio theme and refreshed desktop auth,
  workspace, connection, account, team, and audit surfaces around its typography and palette.
- Replaced bespoke navigation collapse, split-layout, password-field, form-control, login-surface,
  and dataset-feedback styling with Hedron's native components and presentation props where available.
- Refreshed documentation screenshots and the demo screenshot guide for the desktop visual update.
- Made dark mode the consistent default across sign-in, onboarding, and workspace pages while
  preserving saved user preferences; newly created accounts start in dark mode.
- Versioned application-owned CSS and JavaScript URLs with the release version so Connect redeploys
  cannot reuse stale cached styling assets.

## [170926.1] — 2026-09-17

### Added

- Invitation emails now copy the inviter when the inviter has an email address.

### Fixed

- Deployment instructions now work with older `rsconnect-python` clients that discover the root
  `requirements.txt` automatically.

## [170926.0] — 2026-09-17

### Fixed

- PostgreSQL source extraction now explicitly starts a repeatable-read transaction before opening its
  server-side cursor, so row batches use a stable source snapshot even when the database default is
  read committed.
- Posit Connect deployment now explicitly includes configured CA-bundle files, preventing
  production startup failures when those files live under the Git-ignored `deployment/` directory.
- Production startup no longer depends on the optional password blocklist; unavailable configured
  lists fall back to the built-in password checks.
- Connect redeployments clear omitted optional file settings so stale blocklist or CA paths are not
  retained by the content environment.
- Connect deployment no longer inspects or requires a password blocklist file and clears the legacy
  `PASSWORD_BLOCKLIST_PATH` setting.
- Connect deployment supports `CONNECT_TITLE` for the content name and `CONNECT_NEW=true` for a
  separate Connect content item.
- Connect deployment supports `CONNECT_NO_VERIFY=true` for Connect environments where the upload
  succeeds but the client cannot reach the deployed URL for verification.
- Connect runtime dependencies include `standard-pkg-resources` for the `pkg_resources` import
  used by the Connect 2025.06.0 FastAPI launcher, without requiring an unavailable older
  `setuptools` release.
- Removed the unused SMTP transport-security toggle and always connect using the approved relay's
  configured host and port.

## [160926.0] — 2026-09-16

### Changed

- Completed the SOLID refactor across connector, application, service, worker, and UI boundaries.
- Split the connector contract into focused source, destination, catalog, inspection, counting,
  connection-testing, and dataset-provisioning ports with capability-driven registry resolution.
- Added explicit application commands and injectable policy, clock, and sleep dependencies for
  pipeline saves, run enqueueing, and transfer execution.
- Decomposed pipeline interaction routes and run-status presentation into focused modules, removing
  unused compatibility helpers and enforcing writer policy consistently in demo and live modes.

## [150926.0] — 2026-09-15

### Added

- Capability-driven pipeline routing now supports every registered source/destination combination,
  including same-system copies between different objects.
- Foundry source routes accept a manually entered dataset RID and one or more file paths, while
  retaining catalog suggestions for known files.

### Fixed

- Demo seeding now repairs recognized legacy demo bundles and revalidates stale current demo
  bundles, so repeated `make demo` runs keep all three demo connections ready without replacing
  unknown or real credentials.
- PostgreSQL credential examples use the username-shaped `user.name.ctr` placeholder.
- PostgreSQL option fields and adjacent controls use consistent heights and aligned label tracks.

### Changed

- Pipeline source and destination choices are derived from connector capabilities; exact source /
  destination overlaps are rejected.
- PostgreSQL upsert selection supports the available primary or unique keys from the destination
  catalog.
- The demo CLI reports seeded, refreshed, revalidated, and preserved connections separately.

## [140926.1] — 2026-09-14

### Fixed

- Corrected login keyboard order so email tabs directly to password before the recovery link.
- Repaired pipeline source/destination refresh behavior and route readiness transitions.

### Changed

- Added the user-visible application release version to the Data Mover shell and package metadata.

## [140926.0] — 2026-09-14

- Redesigned desktop sign-in with a focused access panel, an illustrated transfer workflow,
  clearer hierarchy, and deployment-aware demo/live messaging.
- Upgraded the desktop workspace with compact Hedron shell chrome, quieter native surfaces,
  readable headings, and side-by-side pipeline and connection panels.
- Restored native button appearances by removing conflicting generic component-bundle rules.
- Added connection setup guidance, including pgAdmin locations for PostgreSQL settings, and
  improved credential-field grouping, password widths, and confirmation-dialog alignment.

## Data Mover pre-calendar release train — 2026-08-06 to 2026-09-11

- Added real MSS, MCS-COP, PostgreSQL, and CSV connectors with Polars batches, durable runs,
  leases, cancellation, retention, persisted events, and feature-gated Foundry writers.
- Added provider protocol notes, sanitized HTTP fixtures, Semblance simulators, ephemeral
  PostgreSQL tests, and opt-in MongoDB contract tests.
- Replaced the separate email worker with in-process FastAPI background delivery while retaining
  `send-email` and `retry-email` for operator recovery.
- Removed unsupported Advana, MongoDB, and ADE product connection flows while preserving existing
  encrypted legacy rows.
- Added transactional PostgreSQL append/upsert/replace behavior, bounded extraction, reconciliation
  states, owner-scoped catalogs, and encrypted credential handling.
- Added offline fake-connector demo mode; production refuses `DATA_MOVER_MODE=demo`.

## Hedron 1.0.0 repository milestone — 2026-08-30

- Upgraded the runtime and Posit integration to the compatible Hedron 1.0.0 train.
- Aligned with Hedron's coding-agent guidance: scripts are declared on `Page`, interaction
  helpers own HTMX response headers, stable types use public imports, and production dependencies
  are bounded to the tested 1.0 feature line.
- Migrated dependency-heavy fragment endpoints from the removed `component` route role to
  `HedronRouter.view`.
- Updated the Hedron runtime minimum to 1.0.10, the Posit adapter minimum to 1.0.9, and the
  compatible `fastapi-workbench` dependency to 1.0.10.
- Added a narrow Workbench middleware compatibility bridge for the published 1.0 Posit adapter.
- Documented session-scoped operational Workbench deployment with SQLite/live and PostgreSQL/live
  modes, in-process email delivery, and an in-process pipeline runtime.
- Added bounded 0.65 scoped motion/style registration and 0.66 typography presentation profiles.
- Restored the wide desktop canvas and aligned nested auth/workspace surfaces with the selected
  light or dark color mode.
- Refined the desktop art direction with native AmbientCanvas layers, Container width, typography
  tokens, glass surfaces, typed elevation, and bounded active navigation/workflow treatments.
- Persisted each user's light or dark preference and restore it on password and federated sign-in.

## Hedron 0.60.0 repository milestone — 2026-08-23

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

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Product releases use `DDMMYY.X`: the first six digits are the release date and `X` increments for
additional releases on the same date. Historical Hedron and framework milestones retain their
original names and are not product-version releases.

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
