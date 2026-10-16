# Data Mover slide text — Old and new

For each entry, replace the **OLD** text with the **NEW** text. These are the exact passages before and after the application-claim corrections. Table entries show only the cells that changed. The explanation column states why each replacement is needed.

## Slide 1 — Cross-Platform Data Mobility Pipeline

### Change 1 — Introductory paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| DART is a highly flexible, omnidirectional data mobility solution designed to securely synchronize information across critical Department of War (DoW) and SOCOM platforms. Rather than a one-way extraction tool, DART serves as a robust bi-directional conduit capable of both pulling and pushing data among the War Data Platform (WDP), on-premise databases, and Palantir Foundry instances. This ensures rapid, edge-capable synchronization, delivering near real-time operational integration where operators need it most. | DART is a data mobility solution designed to support controlled information transfers across Department of War (DoW) and SOCOM platforms. The current application supports user-initiated transfers among PostgreSQL databases and the MSS and MCS-COP Palantir Foundry environments, including transfers in either direction where connector capabilities, user permissions, and enabled destination writers allow. Integration with the War Data Platform (WDP) and other platforms is a proposed future extension. Automated synchronization and near real-time operation are future goals that require implementation and validation; edge use depends on the deployment environment and connectivity. | Limits current scope to implemented routes, marks broader integrations and automatic synchronization as future work, and qualifies access and performance. |

### Change 2 — Platform Connectivity Matrix paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| The pipeline transcends traditional linear Extract, Transform, Load (ETL) workflows by offering true multi-directional data mobility. It is engineered to seamlessly read from and write to any of the integrated nodes using fast and repeatable code, ensuring data generated or modified in one environment is rapidly available exactly where analysts and operators need it. | The current pipeline provides repeatable extract-and-load transfers between supported source and destination nodes, with explicit column type casts available. Saved pipeline definitions allow users to rerun a configured route. General validation and transformation workflows are proposed future capabilities. Data availability depends on successful transfer completion, destination behavior, and access permissions; transfer speed and freshness depend on workload, network conditions, and when runs are started. | Replaces the inaccurate ETL comparison and speed assurances with current repeatable transfers; marks general processing workflows as future work. |

### Change 3 — Availability line

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| **Availability:** *In Development* (NIPR & SIPR) | **Availability:** *In Development*; NIPR and SIPR are intended deployment environments, subject to validation and authorization for each network. | Identifies intended network deployment targets without implying operational availability or authorization on either network. |


## Slide 2 — Operational use cases

### Change 4 — Slide title

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Slide 2 — Demonstrated Operational Use Cases | Slide 2 — Operational Use Cases: Current and Future Capabilities | Removes the unsupported claim that all listed use cases have been demonstrated and identifies their current or future scope. |

### Change 5 — Introductory sentence

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| The architecture's flexibility allows for diverse workflows that support both centralized reporting and edge-level analytics: | The architecture is intended to support diverse workflows for centralized reporting and edge-level analytics. Current routes and proposed future extensions for these use cases are described below; deployment-specific demonstrations require separate evidence. | Distinguishes intended use from validated operational outcomes. |

### Change 6 — Use case 1 heading

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| 1. ADE to Palantir Near Real-Time Synchronization | 1. ADE to Palantir Transfer | Describes the implemented transfer route without promising automatic synchronization or a particular latency. |

### Change 7 — Use case 1 paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Applications utilizing this pipeline successfully capture data generated within the Advanced Development Environment (ADE) and stored in local PostgreSQL databases, subsequently pushing those updates into MSS. This capability feeds Palantir environments in near real-time, empowering immediate analytical exploitation of locally generated intelligence. | **Current:** The implemented PostgreSQL-to-MSS route can transfer data generated within the Advanced Development Environment (ADE) and stored in local PostgreSQL databases, where the deployment provides the required connectivity and permissions. This uploads the transferred data to the selected dataset; subsequent ingestion and analytical availability depend on Foundry processing and configuration.<br><br>**Future:** Automated synchronization could make updates available on a defined cadence. Near real-time behavior would require scheduling or change detection and measured end-to-end latency in the target environment. | Separates the current upload route from future synchronization and explains that analytical availability depends on destination processing. |

### Change 8 — Use case 2 heading

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| 2. Palantir to PostgreSQL and WDP Reverse Sync | 2. Palantir to PostgreSQL Transfer and Future WDP Integration | Distinguishes the current PostgreSQL transfer capability from proposed WDP integration. |

### Change 9 — Use case 2 paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| The pipeline is not limited to feeding Foundry instances; it actively extracts from them. Functionality has been demonstrated pulling refined data and analytical products out of MSS, MCS-COP, and Army Vantage. This data is seamlessly pushed into SOCOM's on-premise PostgreSQL databases and propagated back up into the War Data Platform (WDP), closing the operational feedback loop and enriching the central data lake. | **Current:** The application can read dataset files from MSS and MCS-COP and transfer their data into PostgreSQL through configured routes. Use with SOCOM's on-premise databases depends on deployment connectivity and permissions.<br><br>**Future:** Proposed Army Vantage and War Data Platform (WDP) integrations could extend this workflow, subject to additional connectors and integration validation. An automated feedback loop would also require coordination of the transfer steps, change tracking, and policies for updates, deletes, and conflicting changes. | Limits current scope to MSS/MCS-COP and PostgreSQL, places Vantage/WDP in future work, and qualifies requirements for a feedback loop. |

### Change 10 — Use case 3 heading

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| 3. Centralized Distribution to Edge Platforms | 3. Centralized Distribution to Edge Platforms (Future) | Marks the Databricks-to-edge distribution use case as future work. |

### Change 11 — Use case 3 paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| The pipeline rapidly extracts massive datasets from central Databricks environments and replicates them into PostgreSQL and Palantir. This ensures downstream platforms maintain parity with the authoritative central data store. | The proposed future workflow would extract datasets from central Databricks environments and transfer them into PostgreSQL and supported Palantir environments. The current application implements those destination types; the Databricks source integration would require an additional connector and validation. Dataset scale, throughput, and edge connectivity would need performance and deployment validation. Maintaining parity with the authoritative store would depend on refresh cadence, source consistency, and handling of updates and deletes. | Identifies the missing source connector and replaces scale, speed, and ongoing parity guarantees with their implementation and validation conditions. |

### Change 12 — Integration table heading

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Supported Integration Nodes | Current and Future Integration Nodes | Makes clear that the table includes both current functionality and proposed future integrations. |

### Change 13 — Insert below the integration table heading, before the table

| OLD | NEW — Add this paragraph | Explanation |
|---|---|---|
| *No text at this location — insert the new paragraph.* | Current status describes implementation in the application, with operational validation required for each target deployment. Future entries are proposed integrations requiring implementation and validation; the WDP / Advana label requires confirmation of the intended platform and endpoint. | Defines implementation status separately from operational validation and flags the unresolved WDP/Advana endpoint naming. |

### Change 14 — Integration table: War Data Platform (WDP / Advana)

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Pipeline Capability | Source & Destination | Future: Source & Destination | Marks this integration as a future proposal because the current app has no corresponding connector. |
| Status | Confirmed | Proposed / Not implemented | Replaces unsupported confirmation with the observed status: proposed and not implemented in the current app. |

### Change 15 — Integration table: SOCOM On-Premise PostgreSQL

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Pipeline Capability | Source & Destination | Current: Source & Destination | Identifies the existing source and destination connector functionality as current. |
| Status | Confirmed | Implemented; target deployment validation required | Distinguishes implemented functionality from successful validation in a specific operational deployment. |

### Change 16 — Integration table: Maven Smart Systems (MSS)

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Platform | Maven Smart Systems (MSS) | Maven Smart System (MSS) | Corrects the official product name to the singular Maven Smart System. |
| Pipeline Capability | Source & Destination | Current: Source & Destination | Identifies the existing source and destination connector functionality as current. |
| Status | Confirmed | Implemented; target deployment validation required | Distinguishes implemented functionality from successful validation in a specific operational deployment. |

### Change 17 — Integration table: MCS-COP

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Pipeline Capability | Source & Destination | Current: Source & Destination | Identifies the existing source and destination connector functionality as current. |
| Status | Confirmed | Implemented; target deployment validation required | Distinguishes implemented functionality from successful validation in a specific operational deployment. |

### Change 18 — Integration table: Army Vantage

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Pipeline Capability | Source & Destination | Future: Source & Destination | Marks this integration as a future proposal because the current app has no corresponding connector. |
| Status | Confirmed | Proposed / Not implemented | Replaces unsupported confirmation with the observed status: proposed and not implemented in the current app. |

### Change 19 — Integration table: Air Force Envision

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Pipeline Capability | Source & Destination | Future: Source & Destination | Marks this integration as a future proposal because the current app has no corresponding connector. |

### Change 20 — Integration table: SOCOM Jira

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Pipeline Capability | Source | Future: Source | Marks this integration as a future proposal because the current app has no corresponding connector. |
| Status | Confirmed | Proposed / Not implemented | Replaces unsupported confirmation with the observed status: proposed and not implemented in the current app. |


## Slide 3 — Technical Architecture & Security Features

### Change 21 — Introductory paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| DART is engineered with a strict security-first architecture at its foundation. Moving away from static credential files, the future application introduces fully segregated, encrypted-at-rest token storage with strict user profile isolation. To mitigate the risk of stale credentials or unauthorized lateral movement, the platform implements automated, time-phased token lifecycle alerts that proactively prompt users to rotate their active platform keys. By validating all user payloads via Pydantic prior to pipeline execution, DART prevents common exploitation vectors (such as remote code execution or SQL injection attempts), ensuring that data mobility never compromises sovereign network defenses. | DART's current architecture uses encrypted-at-rest credential storage with owner-scoped access controls, providing logical separation of users' saved credentials within shared application infrastructure. Automated, time-phased provider-token lifecycle alerts are a proposed future feature; provider-key rotation is currently an operator responsibility. Pydantic currently validates selected application settings, locators, and write policies. SQL injection defenses rely on parameterized values and safely composed SQL identifiers, and transfer execution uses trusted built-in connector code. Input validation alone does not prevent remote code execution or guarantee network security; security also depends on deployment controls and permissions enforced by connected systems. | Identifies encrypted storage and logical owner isolation as current, alerts as future, and replaces Pydantic security guarantees with the actual controls. |

### Change 22 — Centralized Credential Management paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Utilizes a secure, unified token structure (via `cred.py`) to handle authentication across disparate systems (Databricks, PostgreSQL, Palantir API, Jira). This decouples sensitive secrets from execution logic and allows for rapid scaling to new platforms. | Currently uses encrypted, user-owned credential bundles and an authorized credential resolver to handle authentication for PostgreSQL and the MSS/MCS-COP Palantir APIs. This keeps saved secrets out of pipeline definitions and resolves required credentials for authorized actions. The `cred.py` structure belongs to earlier standalone scripts. Future Databricks, Jira, and other platform integrations would require additional adapters, credential specifications, and validation. | Describes current encrypted credential bundles and authorized resolution, places cred.py in its historical context, and qualifies future integrations. |

### Change 23 — Secure Network Optimization paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Configured specifically to operate seamlessly within secure NIPRNet environments. By incorporating custom certificate handling libraries (`socom_ca_fix`, `pip_system_certs`), the pipeline automatically bypasses common TLS/SSL handshake errors and network timeouts. | Designed for deployment within secure NIPRNet environments, subject to network-specific configuration and validation. The current application supports system or configured CA trust and optional `socom_ca_fix` bootstrap. Use of `pip_system_certs` would be a deployment-specific choice requiring validation. Approved CA configuration can resolve certificate-trust errors while preserving TLS verification; it does not eliminate all TLS/SSL failures or network timeouts. The application uses configured timeouts and error handling for connection failures. | Describes current CA trust and optional bootstrap accurately; certificate configuration preserves verification and cannot eliminate every TLS failure or timeout. |

### Change 24 — Idempotent Data Loading heading

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Idempotent Data Loading | Idempotent Data Loading with Upsert | Limits the idempotence claim to the selected upsert behavior rather than every loading mode. |

### Change 25 — Idempotent Data Loading paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Employs intelligent, repeatable database logic. When pushing to PostgreSQL, it utilizes temporary staging tables and `ON CONFLICT` clauses mapped to unique identifiers (UUIDs). This guarantees that the pipeline can run continuously without duplicating records or corrupting existing datasets. | Current PostgreSQL loads use staging tables, and selected upsert policies use `ON CONFLICT` against configured primary or unique keys, which need not be UUIDs. The staging tables are ordinary schema tables, and the application includes cleanup logic. With stable, non-null conflict keys backed by suitable constraints, repeated upserts can avoid adding duplicate keys according to the selected update or ignore policy. Append mode can add duplicate records; data integrity also depends on source data, database constraints, and write policy. Automated continuous synchronization is a future goal. | Corrects staging-table and key descriptions, states the conditions for repeatable upserts, and removes universal duplication and integrity guarantees. |

### Change 26 — Analytics-Optimized File Formatting paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| When interfacing with Palantir Foundry, the pipeline automatically converts raw data streams (like pandas DataFrames) into snappy.parquet files prior to upload. This leverages a compressed, columnar format optimized for massive analytical queries, significantly improving Foundry's ingestion and processing speeds. | When interfacing with the supported Palantir Foundry environments, the current application serializes Polars transfer batches as Snappy-compressed Parquet files prior to upload. This provides compressed columnar storage suitable for analytical workloads. The effect on Foundry ingestion and query performance depends on the workload and platform configuration and requires measurement. | Identifies current Polars and Snappy-compressed Parquet output and qualifies the unmeasured Foundry performance benefit. |


## Slide 4 — Schema Validation & Ontology Enforcement

### Change 27 — Introductory paragraph

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Pydantic will be utilized to perform rigorous schema validation on all data payloads. By codifying SOCOM's data ontology into Pydantic models, the application will programmatically enforce strict type checking, data constraints, and relational integrity. This guarantees that all data moved across the enterprise adheres exactly to the command's unified ontology standards, preventing corrupted or non-standard data from entering authoritative environments. | **Future capability:** Pydantic models are proposed for per-record validation against defined SOCOM schema and ontology rules before destination writes. They could check configured types, field constraints, and required relationships. The current application uses Pydantic for selected application contracts and performs schema-continuity and provider checks during transfers; it does not yet implement this ontology validation layer. Future enforcement would depend on complete rule definitions, correct mappings, reference checks, and applying validated outputs on all covered transfer paths. Schema validation alone cannot guarantee factual correctness or enterprise-wide ontology consistency. | Identifies ontology validation as proposed future work, describes existing checks separately, and removes guarantees of complete semantic correctness. |

### Change 28 — Validation table column headings

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Second column heading | Technical Execution via Pydantic | Proposed Future Execution via Pydantic | Makes the implementation column explicitly describe proposed future work. |
| Third column heading | Impact on SOCOM Ontology | Expected Impact on SOCOM Ontology | Identifies intended benefits rather than guaranteed outcomes. |

### Change 29 — Validation table: Strict Type Checking

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Technical execution cell | Enforces exact data types for every field (e.g., ensuring a coordinate is a float, or a date follows ISO 8601 formatting). | Would validate declared types using explicitly configured strict behavior and date rules, such as numeric coordinates with permitted ranges and a defined ISO 8601 date representation. | Strictness requires explicit configuration; valid coordinates and date representations also require defined rules. |
| Impact cell | Eliminates “dirty data” at the point of entry, ensuring down-stream analytics tools (like Palantir) do not break due to malformed records. | Could reject defined type violations before transfer and reduce malformed inputs; well-typed data may still be incorrect or incompatible with downstream requirements. | Type compliance can reduce malformed input but cannot guarantee factual correctness or all downstream compatibility. |

### Change 30 — Validation table: Constraint Enforcement

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Technical execution cell | Applies rules to data fields, such as maximum string lengths, specific regex patterns for MGRS coordinates, or restricted categorical values from a defined drop-down. | Would apply configured rules such as maximum string lengths, MGRS format patterns, or permitted categorical values. Additional semantic checks would be required where format checks are insufficient. | Marks the rules as future work and distinguishes format checks from semantic validation. |
| Impact cell | Ensures that entities (like Units, Targets, or Assets) are uniformly categorized and standardized across all integrated platforms. | Could support uniform categorization of Units, Targets, or Assets across participating platforms when shared definitions and mappings are applied consistently. | Uniform categories depend on shared definitions and mappings being applied consistently. |

### Change 31 — Validation table: Relational Integrity

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Technical execution cell | Validates nested data structures, ensuring that if a “Use Case” record is submitted, it contains the mandatory linked “Jira Ticket ID” and “Assigned Unit” fields. | Would validate nested structures and require linked “Jira Ticket ID” and “Assigned Unit” fields on a “Use Case” record. Verifying referenced records would also require explicit lookups or database constraints. | Required IDs establish field presence; verifying that referenced records exist requires explicit reference checks. |
| Impact cell | Maps perfectly to an ontology's requirement for defined relationships between disparate data nodes, preventing orphaned data. | Could detect missing required relationships and, with reference checks, reject invalid links. Preventing orphaned data would also require update and delete policies. | Preventing orphaned records requires reference checks and update/delete policies, beyond a nested payload shape. |

### Change 32 — Validation table: Automated Sanitization

| Cell | OLD — Replace this | NEW — With this | Explanation |
|---|---|---|---|
| Technical execution cell | Automatically strips out unauthorized fields or forces data into the correct shape before the pipeline executes the transfer. | Would use an explicit policy to reject or ignore unexpected fields and perform approved conversions. Destination writes would use validated output and approved mappings; authorization checks would remain separate. | Makes field handling and conversion explicit, keeps authorization separate, and requires writes to use validated output. |
| Impact cell | Prevents users from accidentally introducing rogue schemas or custom columns into authoritative databases like the War Data Platform. | Could reduce unintended schema changes when combined with controlled destination schemas and write permissions on the covered transfer paths. | Schema protection depends on controlled destination schemas and write permissions, not field conversion alone. |


## Markdown document note — outside the slide text

### Change 33 — Opening document note

| OLD — Replace this | NEW — With this | Explanation |
|---|---|---|
| Transcription of the main slide text in the supplied images. The first two images show the same slide and are represented once here. Headings, line breaks, and quotation marks are normalized for Markdown. The small text inside the application screenshots is not reliably legible in the supplied photographs and has not been transcribed. | Converted from the main slide text in the supplied images, with application claims revised to distinguish current capabilities from proposed future features and correct technical inaccuracies. The first two images show the same slide and are represented once here. The small text inside the application screenshots is not reliably legible in the supplied photographs and has not been transcribed. | Updates the document note to disclose that the text now contains revised application claims rather than a verbatim transcription. |
