# Data Mover

Data Mover is a self-hosted workspace for moving data between approved systems without requiring
users to assemble one-off transfer scripts or share credentials outside a controlled environment.
It brings connection setup, reusable transfer routes, run monitoring, and audit history into one
place while keeping access tied to each user's own permissions.

The project is designed for teams that need a repeatable, reviewable way to move operational data
between MSS, MCS-COP, PostgreSQL, Databricks Unity Catalog/Delta tables, and local CSV files. The
Databricks provider, browsing, and compute controls are available by default, including on Posit
Connect. Live source transfers and destination writes require explicit metadata-visibility
verification; live release and acceptance remain open. See the
[Databricks operator guide](docs/providers/databricks.md) for the exact gates.

> [!IMPORTANT]
> Data Mover supports an organization's security and operational controls, but it does not by
> itself provide an ATO, FedRAMP authorization, FIPS validation, identity proofing, or permission to
> access a connected system. Deployers remain responsible for authorization, network policy,
> credential lifecycle, and approval of each transfer path.

## Why Data Mover exists

Data transfers often begin as manual exports, local scripts, or credential handoffs. Those methods
are difficult to repeat consistently and can make it hard to answer basic operational questions:

- Who initiated a transfer?
- Which source and destination were used?
- What data object was selected?
- Did the transfer finish, fail, or require reconciliation?
- Can the same approved route be run again without rebuilding it?

Data Mover turns that work into a managed workflow. Users connect with their own credentials,
select the data they are authorized to access, save a reusable route, and follow the transfer from
request through completion. Administrators can manage access and review security-relevant activity
without being able to reveal users' saved credentials.

## What the project provides

- **User-owned connections** to supported data systems, with saved credentials protected at rest.
- **Change-aware connection validation** that tests new or changed bundles automatically while
  preserving the latest result for identical resubmissions.
- **Catalog browsing** using terminology appropriate to each provider, such as PostgreSQL tables or
  Foundry datasets, branches, and files.
- **Reusable pipelines** that capture a source, destination, object selection, and write policy.
- **Durable transfer runs** with status, progress, cancellation, metrics, and persisted events.
- **CSV intake** for controlled transfers from local files into supported destinations.
- **Account administration and audit history** for access decisions and operational review.
- **A safe demonstration mode** for training and evaluation without contacting remote systems.

## Supported movement paths

The current project supports these capability-compatible route families. Route compatibility is
capability-driven rather than a fixed pair-by-pair allowlist:

| Source | Destination | Availability |
|---|---|---|
| MSS | MSS, PostgreSQL, MCS-COP | Supported when the destination writer is enabled |
| MCS-COP | MSS, PostgreSQL, MCS-COP | Supported when the destination writer is enabled |
| PostgreSQL | PostgreSQL, MSS, MCS-COP | Supported when the destination writer is enabled |
| CSV upload | PostgreSQL, MSS, MCS-COP | Supported when the destination writer is enabled |
| Databricks (live) | PostgreSQL, MSS, MCS-COP, Databricks | Requires verified metadata visibility; live acceptance remains open |
| MSS, MCS-COP, PostgreSQL, CSV upload (live) | Databricks | Requires verified metadata visibility, writer prerequisites, and staging schema; live acceptance remains open |
| MSS, MCS-COP, PostgreSQL, CSV upload (offline demo) | Databricks | Simulated Delta create, append, and replace |
| Databricks (offline demo) | MSS, MCS-COP, PostgreSQL, Databricks | Simulated local source; same-system copies require distinct objects |

CSV is source-only. Same-system routes must use different objects; the builder rejects a PostgreSQL
table-to-itself route and Foundry outputs that overlap their source files. A route is shown only when
the source/destination capabilities are compatible, the required connection is ready, and the
destination writer is enabled.

“Supported” describes an implemented route, not its deployment state. PostgreSQL, MSS, and MCS-COP
writing are enabled by default; operators can disable a writer through its
`PIPELINE_ENABLE_*_WRITER` setting.

The local `make demo` launcher seeds four connections and enables the Databricks emulator with
create, append, and replace. No remote calls or compute charges occur. Demo table changes last for
the app process; built-in table identities remain stable across restarts. Set
`PIPELINE_ENABLE_DATABRICKS=false` to omit it. In real mode the provider and compute controls are
enabled by default. Metadata visibility verification defaults to false and must be explicitly
attested before schema inspection, row counts, source transfers, or destination writes. The writer
defaults on only while all prerequisites are true; its default staging schema is
`main.data_mover_staging`. Enabling these controls does not close the separate live acceptance or
release gates.

Advana, MongoDB, and arbitrary custom workloads are outside the current project scope.

## The Data Mover workflow

1. **Gain access.** A user signs in through the deployment's approved authentication path and, when
   required, receives administrator approval.
2. **Connect systems.** The user supplies provider credentials with only the permissions needed for
   the intended work.
3. **Validate access.** Data Mover verifies each connection before making it available for route
   creation.
4. **Choose the data path.** The user selects a source, destination, data object, and supported write
   behavior.
5. **Save the route.** The completed definition becomes a reusable, user-owned pipeline.
6. **Run and monitor.** Data Mover records the run, reports progress and outcomes, and preserves the
   operational events needed for review.
   The live panel shows endpoint objects, persisted row counts, and one stage-progress meter.
   Loading uses a directional packet animation; success, cancellation, and failure use static
   outcomes with recorded destination impact. Both light and dark modes honor reduced motion.
7. **Review or reconcile.** Successful runs retain their results and metrics. Ambiguous or
   interrupted writes are surfaced for human review instead of being reported as successful.

## Security and trust model

Data Mover follows a least-privilege, user-scoped model:

- Users can work only with connections, uploads, pipelines, and runs that belong to them.
- Remote systems continue to enforce their own authorization; Data Mover cannot grant access that a
  user's credential does not already have.
- Saved credential values are encrypted and are not displayed again after entry.
- Credential plaintext is limited to the signed-in owner's selected catalog/test request or the
  provider bundles required while a claimed transfer is executing; it is never stored in pipeline
  definitions, run snapshots, catalog caches, or browser responses.
- Security-relevant account, administration, and transfer activity is recorded for audit and
  troubleshooting.
- Demonstration connections are isolated from live provider access, and demonstration mode is not
  permitted as a production configuration.
- Production use requires an approved deployment environment, secure network paths, managed
  secrets, reliable persistence, and supervised transfer operations.

See the application's [security policy](SECURITY.md) for the detailed
assurance boundary and production gate.

## Repository organization

`README.md` contains the application quick start and documentation map. Operational and
implementation documentation lives under [`docs/`](docs/), with the minimal confidence app under
[`demo-app/`](demo-app/) and deployment helpers under [`docker/`](docker/).

## Getting started

Choose the path that matches your role:

| Audience | Start here |
|---|---|
| Evaluators and local demo users | [Application quick start](README.md#quick-start) |
| People configuring and running transfers | [User guide](docs/user-guide.md) |
| Deployment and platform teams | [Deployment guide](docs/deploy.md) |
| Security reviewers | [Security policy](SECURITY.md) |
| Operators | [Pipeline operations runbook](docs/runbooks/pipeline-worker.md) |
| Maintainers and contributors | [Maintainer guide](docs/maintainer-guide.md) |

For evaluation, begin with the demonstration environment. It uses simulated connectors and does
not contact MSS, MCS-COP, PostgreSQL, or Databricks endpoints. Do not use real credentials or
sensitive data in a disposable demonstration deployment.

## Current desktop UI

The [README screenshots](README.md#screenshots) and [light/dark gallery](docs/screenshots/README.md)
show the October 21, 2026 UI, including four-provider status, route and schema setup, completed
Delta transfers, account administration, and audit history. Screenshots use synthetic data from an
isolated demo.

## Project status

Data Mover is currently an alpha-stage project. The supported provider set is deliberately narrow;
route compatibility is capability-driven and production readiness depends on the target environment's identity,
network, provider, and security approvals.

Before adopting Data Mover for operational use, review the current
[release notes](docs/releases/README.md), [known boundaries](README.md#non-goals), and
[production security gate](SECURITY.md#production-security-gate).

## Contributing

Changes should preserve user isolation, truthful run reporting, provider-specific semantics, and
the separation between demonstration and live operations. See the
[contribution guide](CONTRIBUTING.md) for the development workflow and
quality checks.

Security concerns should be handled through the reporting process described in the
[security policy](SECURITY.md).

## License

Data Mover is available under the [MIT License](LICENSE).
