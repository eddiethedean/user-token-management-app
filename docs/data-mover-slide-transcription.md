# Cross-Platform Data Mobility Pipeline

> Converted from the main slide text in the supplied images, with application claims revised to distinguish current capabilities from proposed future features and correct technical inaccuracies. The first two images show the same slide and are represented once here. The small text inside the application screenshots is not reliably legible in the supplied photographs and has not been transcribed.

**Accelerate the SOF Decision Advantage**<br>
USSOCOM CDAO

## Slide 1 — Cross-Platform Data Mobility Pipeline

### Access, Refine, and Transport Data in a Secure Environment

DART is a data mobility solution designed to support controlled information transfers across Department of War (DoW) and SOCOM platforms. The current application supports user-initiated transfers among PostgreSQL databases and the MSS and MCS-COP Palantir Foundry environments, including transfers in either direction where connector capabilities, user permissions, and enabled destination writers allow. Integration with the War Data Platform (WDP) and other platforms is a proposed future extension. Automated synchronization and near real-time operation are future goals that require implementation and validation; edge use depends on the deployment environment and connectivity.

### Platform Connectivity Matrix

The current pipeline provides repeatable extract-and-load transfers between supported source and destination nodes, with explicit column type casts available. Saved pipeline definitions allow users to rerun a configured route. General validation and transformation workflows are proposed future capabilities. Data availability depends on successful transfer completion, destination behavior, and access permissions; transfer speed and freshness depend on workload, network conditions, and when runs are started.

*App is in development. These visuals are still subject to change.*

**Availability:** *In Development*; NIPR and SIPR are intended deployment environments, subject to validation and authorization for each network.

## Slide 2 — Operational Use Cases: Current and Future Capabilities

The architecture is intended to support diverse workflows for centralized reporting and edge-level analytics. Current routes and proposed future extensions for these use cases are described below; deployment-specific demonstrations require separate evidence.

### 1. ADE to Palantir Transfer

**Current:** The implemented PostgreSQL-to-MSS route can transfer data generated within the Advanced Development Environment (ADE) and stored in local PostgreSQL databases, where the deployment provides the required connectivity and permissions. This uploads the transferred data to the selected dataset; subsequent ingestion and analytical availability depend on Foundry processing and configuration.

**Future:** Automated synchronization could make updates available on a defined cadence. Near real-time behavior would require scheduling or change detection and measured end-to-end latency in the target environment.

### 2. Palantir to PostgreSQL Transfer and Future WDP Integration

**Current:** The application can read dataset files from MSS and MCS-COP and transfer their data into PostgreSQL through configured routes. Use with SOCOM's on-premise databases depends on deployment connectivity and permissions.

**Future:** Proposed Army Vantage and War Data Platform (WDP) integrations could extend this workflow, subject to additional connectors and integration validation. An automated feedback loop would also require coordination of the transfer steps, change tracking, and policies for updates, deletes, and conflicting changes.

### 3. Centralized Distribution to Edge Platforms (Future)

The proposed future workflow would extract datasets from central Databricks environments and transfer them into PostgreSQL and supported Palantir environments. The current application implements those destination types; the Databricks source integration would require an additional connector and validation. Dataset scale, throughput, and edge connectivity would need performance and deployment validation. Maintaining parity with the authoritative store would depend on refresh cadence, source consistency, and handling of updates and deletes.

### Current and Future Integration Nodes

Current status describes implementation in the application, with operational validation required for each target deployment. Future entries are proposed integrations requiring implementation and validation; the WDP / Advana label requires confirmation of the intended platform and endpoint.

| Platform | Pipeline Capability | Status |
|---|---|---|
| War Data Platform (WDP / Advana) | Future: Source & Destination | Proposed / Not implemented |
| SOCOM On-Premise PostgreSQL | Current: Source & Destination | Implemented; target deployment validation required |
| Maven Smart System (MSS) | Current: Source & Destination | Implemented; target deployment validation required |
| MCS-COP | Current: Source & Destination | Implemented; target deployment validation required |
| Army Vantage | Future: Source & Destination | Proposed / Not implemented |
| Air Force Envision | Future: Source & Destination | Conceptual / Untested |
| SOCOM Jira | Future: Source | Proposed / Not implemented |

*Visible footer marking: UNCLASSIFIED.*

## Slide 3 — Technical Architecture & Security Features

DART's current architecture uses encrypted-at-rest credential storage with owner-scoped access controls, providing logical separation of users' saved credentials within shared application infrastructure. Automated, time-phased provider-token lifecycle alerts are a proposed future feature; provider-key rotation is currently an operator responsibility. Pydantic currently validates selected application settings, locators, and write policies. SQL injection defenses rely on parameterized values and safely composed SQL identifiers, and transfer execution uses trusted built-in connector code. Input validation alone does not prevent remote code execution or guarantee network security; security also depends on deployment controls and permissions enforced by connected systems.

### Centralized Credential Management

Currently uses encrypted, user-owned credential bundles and an authorized credential resolver to handle authentication for PostgreSQL and the MSS/MCS-COP Palantir APIs. This keeps saved secrets out of pipeline definitions and resolves required credentials for authorized actions. The `cred.py` structure belongs to earlier standalone scripts. Future Databricks, Jira, and other platform integrations would require additional adapters, credential specifications, and validation.

### Secure Network Optimization

Designed for deployment within secure NIPRNet environments, subject to network-specific configuration and validation. The current application supports system or configured CA trust and optional `socom_ca_fix` bootstrap. Use of `pip_system_certs` would be a deployment-specific choice requiring validation. Approved CA configuration can resolve certificate-trust errors while preserving TLS verification; it does not eliminate all TLS/SSL failures or network timeouts. The application uses configured timeouts and error handling for connection failures.

### Idempotent Data Loading with Upsert

Current PostgreSQL loads use staging tables, and selected upsert policies use `ON CONFLICT` against configured primary or unique keys, which need not be UUIDs. The staging tables are ordinary schema tables, and the application includes cleanup logic. With stable, non-null conflict keys backed by suitable constraints, repeated upserts can avoid adding duplicate keys according to the selected update or ignore policy. Append mode can add duplicate records; data integrity also depends on source data, database constraints, and write policy. Automated continuous synchronization is a future goal.

### Analytics-Optimized File Formatting

When interfacing with the supported Palantir Foundry environments, the current application serializes Polars transfer batches as Snappy-compressed Parquet files prior to upload. This provides compressed columnar storage suitable for analytical workloads. The effect on Foundry ingestion and query performance depends on the workload and platform configuration and requires measurement.

## Slide 4 — Schema Validation & Ontology Enforcement

**Future capability:** Pydantic models are proposed for per-record validation against defined SOCOM schema and ontology rules before destination writes. They could check configured types, field constraints, and required relationships. The current application uses Pydantic for selected application contracts and performs schema-continuity and provider checks during transfers; it does not yet implement this ontology validation layer. Future enforcement would depend on complete rule definitions, correct mappings, reference checks, and applying validated outputs on all covered transfer paths. Schema validation alone cannot guarantee factual correctness or enterprise-wide ontology consistency.

| Validation Mechanism | Proposed Future Execution via Pydantic | Expected Impact on SOCOM Ontology |
|---|---|---|
| Strict Type Checking | Would validate declared types using explicitly configured strict behavior and date rules, such as numeric coordinates with permitted ranges and a defined ISO 8601 date representation. | Could reject defined type violations before transfer and reduce malformed inputs; well-typed data may still be incorrect or incompatible with downstream requirements. |
| Constraint Enforcement | Would apply configured rules such as maximum string lengths, MGRS format patterns, or permitted categorical values. Additional semantic checks would be required where format checks are insufficient. | Could support uniform categorization of Units, Targets, or Assets across participating platforms when shared definitions and mappings are applied consistently. |
| Relational Integrity | Would validate nested structures and require linked “Jira Ticket ID” and “Assigned Unit” fields on a “Use Case” record. Verifying referenced records would also require explicit lookups or database constraints. | Could detect missing required relationships and, with reference checks, reject invalid links. Preventing orphaned data would also require update and delete policies. |
| Automated Sanitization | Would use an explicit policy to reject or ignore unexpected fields and perform approved conversions. Destination writes would use validated output and approved mappings; authorization checks would remain separate. | Could reduce unintended schema changes when combined with controlled destination schemas and write permissions on the covered transfer paths. |

*Visible footer marking: UNCLASSIFIED.*
