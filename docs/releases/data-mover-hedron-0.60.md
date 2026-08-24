# Data Mover — Unreleased / Hedron 0.60

**Status:** Repository release note  
**Runtime:** `hedron>=0.60.0,<0.61`, `hedron-posit>=0.60.0,<0.61`

## Added

- Validated Hedron ThemeSpec authoring with modern color inputs, accessibility modes, aliases, and
  typed flow recipe metadata.
- Native persisted theme selection with system/light/dark modes.
- Hedron-native AppShell, navigation, PageHeader, forms, tables, alerts, badges, connector flows,
  process flows, timelines, status, and bounded scroll regions.
- Pipeline workflow overview showing Connect → Configure → Run.
- Stage-specific run status copy and contextual stage descriptions.
- Animated native progress treatment while a worker is active.
- **Run again** action directly in the Live transfer pane; it is disabled while a run is active.
- Roadmap release artifacts, ETL/security contracts, ADE learning path, and no-Node workshop example.

## Changed

- Removed product CSS rules in favor of Hedron-native styling and recipes.
- Kept application-owned authentication, CSRF, sessions, response headers, and provider egress
  policy boundaries.
- Pipeline runs continue to poll persisted server-side events rather than browser-owned state.

## Fixed

- Run-pane actions no longer require navigating back to Route setup to rerun a saved pipeline.
- Active runs communicate their current stage and disable duplicate-run submission.

## Verification

- `make check`: passed.
- 224 tests passed, 30 deselected.
- Coverage: 83.79%.
- Desktop browser inspection covered queued, loading, verifying, and succeeded states.

## Upgrade notes

Review [docs/hedron.md](../hedron.md), [docs/maintainer-guide.md](../maintainer-guide.md), and the
[pipeline worker runbook](../runbooks/pipeline-worker.md). No database migration is required for
the presentation-only changes in this release note.
