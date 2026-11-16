# Data Mover: Live deployment diagnostics on Posit Connect

Jira epic [CDO-5030](https://idstjira.socom.mil/jira/browse/CDO-5030) · [Issue export](../../artifacts/jira/data-mover-live-diagnostics-issues.csv) · [Implementation plan](../plans/live-deployment-diagnostics.md)

## Why this epic matters

Developers can test Data Mover on a Mac and in GitHub CI, but those environments do not have the
same routes, credentials, and provider access as the Posit Connect deployment. A successful local
test therefore cannot tell an administrator whether Connect can reach the organization's live
data sources.

This epic gives administrators a way to check readiness from the deployed app and share useful,
safe results with developers who may not have access to the same network.

## User outcome

As a Posit Connect administrator, I want to run diagnostics from the deployment using my saved
connections so I can see which parts are ready, identify what needs attention, and send developers
a useful report without exposing credentials or customer data.

At the end of the epic, administrators can check deployment readiness, run a representative
synthetic transfer, verify what arrived at its destination, and review coverage across supported
routes and write modes. Results show transfer, verification, and cleanup outcomes separately.

## What administrators will be able to do

- Run checks from Posit Connect, where the app uses the deployment's network access and the
  administrator's own saved connections.
- See whether the app, its private transfer storage, and each configured provider are ready.
- Tell a missing connection, disabled feature, access problem, and provider failure apart.
- Review the results later and share a plain text report with developers.
- Once the later transfer stories are delivered, test approved synthetic data in approved scratch
  locations and confirm that the expected data arrived and was cleaned up.

## Story pages

| Issue | User benefit | Guide |
| --- | --- | --- |
| [CDO-5031](https://idstjira.socom.mil/jira/browse/CDO-5031) | Check saved connections from the deployment and understand readiness results. | [Admin access and live connection checks](live-deployment-diagnostics-admin-access-and-live-connection-checks.md) |
| LD-2 | Return to a diagnostic run after a refresh or restart without duplicating work. | Durable diagnostic execution is planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-durable-diagnostic-execution-ld-2). |
| LD-3 | Keep diagnostic writes within operator-approved fixtures and scratch locations. | Scratch resource safeguards are planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-approve-and-isolate-scratch-resources-ld-3). |
| LD-4 | Copy or download a safe report that developers can use to troubleshoot. | Report sharing is planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-copy-a-sanitized-support-report-ld-4). |
| LD-5 | Prove one small end-to-end transfer and verify its destination rows. | The first smoke transfer is planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-run-and-verify-the-csv-to-postgresql-smoke-transfer-ld-5). |
| LD-6 | Check supported provider directions and write modes. | Expanded coverage is planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-expand-route-and-write-mode-coverage-ld-6). |
| LD-7 | Catch data mismatches and confirm sensitive-data guardrails behave as expected. | Integrity and guardrail checks are planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-verify-data-integrity-and-guardrail-behavior-ld-7). |
| LD-8 | Follow a clear operator guide and see which live checks have been completed. | Release and operating guidance is planned in the [implementation plan](../plans/live-deployment-diagnostics.md#data-mover-verify-release-behavior-and-document-operations-ld-8). |

## What the current experience checks

The current Diagnostics page lets an administrator run read-only checks for the deployed app,
saved connections, and provider catalogs. It records a result for each check, keeps a history the
administrator can reopen, and offers a copyable or downloadable readiness report. It does not
write to provider data or start compute. Synthetic smoke transfers and the broader route matrix
are later parts of this epic.

## Safety and expectations

- Diagnostics run from the deployed app and use the signed-in administrator's saved credentials.
- The current checks are read-only. Later transfer checks are limited to operator-approved
  synthetic fixtures and scratch locations.
- Reports are intended for developer troubleshooting. They do not include credentials, raw source
  values, or customer data.
- A passing connection check shows what the deployment could reach at that time. It does not by
  itself prove that a future transfer can write, verify every row, or clean up every destination.

## Desired/expected result

An administrator can use the Posit Connect app to check live deployment readiness, test supported
data paths safely with synthetic records, understand what passed or needs attention, and share a
sanitized report that helps developers troubleshoot without access to NIPR.
