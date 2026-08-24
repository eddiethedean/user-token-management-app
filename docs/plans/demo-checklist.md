# Access Registry + Pipeline demo checklist (DM-4)

## Before the demo

- [ ] Start the documented demo environment with `./scripts/run-demo.sh`.
- [ ] Confirm the printed demo credentials are used only in the local demo.
- [ ] Confirm the banner says remote endpoints are untouched.
- [ ] Confirm the three fake connections report ready.

## Happy path

1. Sign in as the demo user.
2. Open **Connections** and show provider status without revealing credential values.
3. Open **Pipeline → Route setup** and choose the source, destination, and write mode.
4. Save the route.
5. Open **Live transfer** and select **Run again** from the run pane.
6. Show queued, loading, verifying, and succeeded states.
7. Show persisted event history and row metrics.
8. Open **Activity** and show the audit trail.

## Recovery path

- If a run stays queued, check the worker process and the [worker runbook](../runbooks/pipeline-worker.md).
- If validation fails, correct the connection and rerun; do not edit the database.
- If a transfer requires reconciliation, stop and follow the operator runbook before retrying.

## Evidence to capture

- Timestamp and commit SHA.
- Screenshot of the succeeded run and persisted event feed.
- Test command and result.
- Any external provider or deployment dependency called out explicitly.
