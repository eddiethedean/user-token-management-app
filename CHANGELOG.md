# Changelog

All notable changes to Access Registry are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-08-06

### Added

- Initial public packaging: MIT license, adopter-first README, and operator docs under `docs/`
  (auth modes, deploy, troubleshooting, FAQ, architecture).
- `CONTRIBUTING.md`, expanded migrations README, and day-to-day Makefile targets.
- Browser HTMX UI (FastAPI + Hedron) for administrator-approved government-email accounts.
- Local password and trusted-header authentication modes.
- Encrypted Advana / ADE / MSS API token slots, admin directory, invitations, audit log.
- Queued email delivery with supervised worker CLI.
- Alembic migrations with optional `--adopt-existing` for verified legacy schemas.
- Security architecture and decision register (`SECURITY.md`) with production gate and
  vulnerability reporting guidance.

[0.1.0]: https://github.com/eddiethedean/user-token-management-app/releases/tag/v0.1.0
