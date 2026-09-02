# Data Mover

Data Mover is a self-hosted workspace for moving data between approved systems without requiring
users to assemble one-off transfer scripts or share credentials outside a controlled environment.
It brings connection setup, reusable transfer routes, run monitoring, and audit history into one
place while keeping access tied to each user's own permissions.

The project is designed for teams that need a repeatable, reviewable way to move operational data
between MSS, MCS-COP, PostgreSQL, and local CSV files.

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
- **Connection validation** before a system is offered for use in a transfer route.
- **Catalog browsing** using terminology appropriate to each provider, such as PostgreSQL tables or
  Foundry datasets, branches, and files.
- **Reusable pipelines** that capture a source, destination, object selection, and write policy.
- **Durable transfer runs** with status, progress, cancellation, metrics, and persisted events.
- **CSV intake** for controlled transfers from local files into supported destinations.
- **Account administration and audit history** for access decisions and operational review.
- **A safe demonstration mode** for training and evaluation without contacting remote systems.

## Supported movement paths

The initial project scope supports the following routes:

| Source | Destination | Availability |
|---|---|---|
| MSS | PostgreSQL | Supported |
| PostgreSQL | MSS | Supported |
| PostgreSQL | MCS-COP | Supported |
| CSV upload | PostgreSQL | Supported |
| CSV upload | MSS | Supported |
| CSV upload | MCS-COP | Supported |

MCS-COP is destination-only in the current scope. CSV is a source-only option. A route is shown to
a user only when Data Mover supports that provider role and the required connection is ready.

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
7. **Review or reconcile.** Successful runs retain their results and metrics. Ambiguous or
   interrupted writes are surfaced for human review instead of being reported as successful.

## Security and trust model

Data Mover follows a least-privilege, user-scoped model:

- Users can work only with connections, uploads, pipelines, and runs that belong to them.
- Remote systems continue to enforce their own authorization; Data Mover cannot grant access that a
  user's credential does not already have.
- Saved credential values are encrypted and are not displayed again after entry.
- Only the credentials required for a selected transfer are made available while that transfer is
  being executed.
- Security-relevant account, administration, and transfer activity is recorded for audit and
  troubleshooting.
- Demonstration connections are isolated from live provider access, and demonstration mode is not
  permitted as a production configuration.
- Production use requires an approved deployment environment, secure network paths, managed
  secrets, reliable persistence, and supervised transfer operations.

See the application's [security policy](user-token-management-app/SECURITY.md) for the detailed
assurance boundary and production gate.

## Repository organization

```text
data-mover/
├── README.md
└── user-token-management-app/   # Data Mover user workspace and operational application
```

The repository-level README describes the Data Mover project as a whole. Component-specific setup,
configuration, deployment, maintenance, and implementation documentation lives inside
[`user-token-management-app/`](user-token-management-app/README.md).

## Getting started

Choose the path that matches your role:

| Audience | Start here |
|---|---|
| Evaluators and local demo users | [Application quick start](user-token-management-app/README.md#quick-start) |
| People configuring and running transfers | [User guide](user-token-management-app/docs/user-guide.md) |
| Deployment and platform teams | [Deployment guide](user-token-management-app/docs/deploy.md) |
| Security reviewers | [Security policy](user-token-management-app/SECURITY.md) |
| Operators | [Pipeline operations runbook](user-token-management-app/docs/runbooks/pipeline-worker.md) |
| Maintainers and contributors | [Maintainer guide](user-token-management-app/docs/maintainer-guide.md) |

For evaluation, begin with the demonstration environment. It uses simulated connectors and does
not contact MSS, MCS-COP, or PostgreSQL endpoints. Do not use real credentials or sensitive data in
a disposable demonstration deployment.

## Project status

Data Mover is currently an alpha-stage project. The supported provider and route matrix is
deliberately narrow, and production readiness depends on the target environment's identity,
network, provider, and security approvals.

Before adopting Data Mover for operational use, review the current
[release notes](user-token-management-app/docs/releases/README.md),
[known boundaries](user-token-management-app/README.md#non-goals), and
[production security gate](user-token-management-app/SECURITY.md#production-security-gate).

## Contributing

Changes should preserve user isolation, truthful run reporting, provider-specific semantics, and
the separation between demonstration and live operations. See the
[contribution guide](user-token-management-app/CONTRIBUTING.md) for the development workflow and
quality checks.

Security concerns should be handled through the reporting process described in the
[security policy](user-token-management-app/SECURITY.md).

## License

Data Mover is available under the [MIT License](user-token-management-app/LICENSE).
