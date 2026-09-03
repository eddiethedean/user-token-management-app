# Security architecture and decision record

This document records the security decisions implemented by Data Mover, the evidence used to
make them, and the controls that must be supplied by the deployment environment. It is intended to
support design review and preparation of an organization-specific system security plan (SSP).

It is **not** an authorization to operate, a claim of FedRAMP or DoD compliance, or a substitute for
the system owner's risk assessment. Repository implementation facts and deployment instructions were
last reviewed on 2026-08-26; the linked standards and guidance remain reference material and must be
rechecked when a release or authorization baseline changes. Standards use their own normative terms
(`SHALL`, `SHOULD`, and so on); an application decision citing a standard does not by itself establish
conformance with the whole standard.
[NIST SP 800-53 Rev. 5](https://doi.org/10.6028/NIST.SP.800-53r5) states that its guidelines do not
apply to national security systems without the express approval of the responsible policy officials.
The SIPR authorizing organization must select the applicable CNSS/DoD requirements and overlays;
NIST and OWASP references here support engineering reasoning but do not decide that authorization
boundary.

**Operator surface:** Data Mover is a **browser HTMX UI** with cookie sessions — not a public
REST/OpenAPI resource API. MSS, MCS-COP, and PostgreSQL credential bundles are stored
encrypted for authorized users; they are not how the application authenticates HTTP callers.
Demo mode uses fake connectors. In real mode, an explicit **Test connection** action decrypts the
current user's selected bundle in the web process, while transfer execution decrypts only the
bundles required by a claimed `pipeline-worker` run. Foundry outbound hosts are allowlisted, source
and staging data are bounded, and persisted run facts are redaction-filtered. CSV uploads and saved
pipeline definitions are real owner-scoped database content. Day-to-day setup lives in the
[README](README.md) and [docs/](docs/); use this file for the decision register and production gate.

## Reporting a vulnerability

Do **not** open a public issue for security-sensitive reports.

1. Prefer [GitHub Security Advisories](https://github.com/eddiethedean/user-token-management-app/security/advisories/new)
   (private vulnerability reporting) when enabled on the repository.
2. Otherwise email the maintainer listed in [pyproject.toml](pyproject.toml) with a description,
   impact, and reproduction steps. Allow reasonable time for a fix before public disclosure.

Please omit production secrets, live tokens, and unnecessary personal data from reports.

## Production security gate

Do not represent a deployment as production-ready until the system owner records evidence for every
item below. Items marked **code gap** require application work; the remainder require deployment or
authorization evidence.

- [ ] The privacy and security officials have determined whether making the application's profile,
      account activity, and session metadata available invokes the federal AAL2 minimum. If it does,
      `AUTHENTICATION_MODE=trusted_header` is used behind an approved phishing-resistant CAC/MFA
      identity-aware proxy, or another approved federation path replaces local password sign-in.
- [ ] For any remaining password-only use, the identity and authenticator risk assessment explicitly
      accepts email-verified access for the information and functions in scope.
- [ ] The NIPR and SIPR authorization boundaries, data flows, administrators, secrets, databases,
      SMTP relays, logs, backups, and pipelines are separate and documented.
- [ ] `APP_ENV=production`, `COOKIE_SECURE=true`, the stable HTTPS `PUBLIC_BASE_URL`, PostgreSQL
      `DATABASE_URL`, exact `ALLOWED_EMAIL_DOMAINS`, approved issuer/audience values, SMTP with
      STARTTLS, sent-body redaction, and the approved offline password blocklist are set. Production
      startup validation passes.
- [ ] If directory validation is enabled, the source's authority, attribute currency, privacy use,
      exact URL/response contract, CA trust, bearer-secret handling, fail-open/fail-closed policy,
      DNS behavior, and network egress allowlist are approved and monitored. It is not represented as
      authentication, CAC validation, identity proofing, clearance, or authorization.
- [ ] High-entropy, enclave-unique JWT and session secrets are injected from an approved store;
      access, audit, incident, and rotation procedures are recorded.
- [ ] High-entropy connection-credential encryption keys (`API_TOKEN_ENCRYPTION_KEYS` retains its
      legacy configuration name) are injected separately from JWT/session secrets; old
      referenced key versions remain available, protected backups and recovery are tested, and the
      exact cryptographic module and operational environment have the required validation evidence.
- [ ] The separately supervised pipeline worker is isolated from the web application with a
      least-privilege service account, protected spool directory, network egress policy, and resource
      limits. The current worker executes trusted built-in connector code in-process; it is not a
      sandbox for arbitrary user-supplied code.
- [ ] If arbitrary or separately packaged run code is introduced, the run supervisor uses an explicit
      minimal child environment that cannot inherit the master-key ring, grants only the selected
      provider slots, isolates users and runs, redacts logs/artifacts, and records each use. Arbitrary
      granted code is treated as capable of exfiltrating its token.
- [ ] For local Posit Connect execution, `Applications.InheritSystemEnvVars=false` is set and verified.
      This prevents ambient Connect variables from entering content processes; it does not replace the
      current worker's in-process trust boundary or sanitize subprocesses. Secret values never enter
      command arguments, URLs, logs, artifacts, exception reports, or crash dumps.
- [ ] The atomic credential-encryption key-usage counter is monitored, its per-key ceiling is approved well below
      the applicable SP 800-38D bound, rotation occurs early, and an approved rewrap procedure is
      tested before retiring old keys.
- [ ] Connection-management authentication strength and any step-up/reauthentication requirement are
      approved for the value of the stored credentials; provider-side issuance uses the minimum
      possible scope and lifetime, and revocation/rotation procedures are tested.
- [ ] The configured offline common/compromised-password blocklist is approved, versioned, updated,
      and tested without disclosing candidate passwords outside the enclave.
- [ ] The selected Argon2 parameters are recorded and benchmarked, or the exact FIPS-validated
      PBKDF2 module/configuration/operational environment is evidenced.
- [ ] TLS is approved end to end; HTTP is unavailable or redirected appropriately; Secure cookies,
      HSTS, and certificates have been tested at the external URL.
- [ ] Direct app-server access is blocked and the trusted proxy overwrites security-relevant
      forwarding headers; every immediate proxy address is explicitly in `TRUSTED_PROXY_IPS`, and
      `X-Forwarded-For` is not used for authorization.
- [ ] In `trusted_header` mode, the identity-aware proxy strips every client-supplied identity
      header, performs the approved authentication, injects exactly one normalized account email,
      and has tests proving header spoofing cannot bypass the proxy boundary.
- [ ] Cookie set, refresh, deletion, path isolation, SameSite, and expiry have been tested through
      both Connect and Workbench routes.
- [ ] The atomic five-failure terminal password disablement and approved reset/rebinding procedure
      are exercised against production PostgreSQL, monitored for denial-of-service abuse, and
      reflected in help-desk recovery procedures.
- [ ] Database-backed source/account throttling is enabled and tested on production PostgreSQL;
      ingress volumetric throttling, progressive delay/device-risk controls, enumeration timing
      tests and alerting are approved; signed pre-authentication CSRF behavior is verified through
      the production proxy.
- [ ] Conditional capability-consumption and refresh-family replay tests pass concurrently on the
      production PostgreSQL version and topology.
- [ ] `Cache-Control: no-store` behavior is verified through the production proxy for authenticated
      and token-bearing responses.
- [ ] Real transfer limits, lease expiry, cancellation, Foundry redirect rejection, HTTPS host
      allowlists, CA verification, source/spool quotas, staging cleanup, and PostgreSQL TLS mode are
      tested with production-equivalent configuration. The production allowlist must not include a
      loopback host, because loopback HTTP is reserved for local development.
- [ ] The app's background email delivery is monitored; SMTP transport is approved; retry backlog
      and dead letters are monitored; the operator requeue procedure is tested.
- [ ] Sent outbox bodies are redacted; pending/dead-letter retention is approved; proxy,
      application, SMTP, and SIEM logs redact registration/invitation/reset query tokens.
- [ ] PostgreSQL security, Alembic migration/adoption rehearsal, backup encryption, restore testing,
      retention, availability, and least-privilege credentials are approved. The release procedure
      migrates before application startup and never uses a migration to bootstrap an administrator.
- [ ] Audit events flow to protected centralized storage with time synchronization, retention,
      monitoring, alerts, access review, and incident-response integration.
- [ ] Administrator lifecycle, dual-control/break-glass needs, periodic access review, and prompt
      deprovisioning are documented.
- [ ] Dependency locking, vulnerability scanning, Hedron/HTMX provenance review, patching, and
      release approval are part of the deployment pipeline.
- [ ] Unit and HTMX fragment security tests pass; security tests also pass in the exact
      production-equivalent proxy and database topology; penetration testing and authorization
      review are complete at the required impact level.

## Contents

- [Reporting a vulnerability](#reporting-a-vulnerability)
- [Production security gate](#production-security-gate)
- [Status vocabulary](#status-vocabulary)
- [Evidence and claim discipline](#evidence-and-claim-discipline)
- [Assurance boundary](#assurance-boundary)
- [Threat model and trust boundaries](#threat-model-and-trust-boundaries)
- [Decision register](#decision-register)
- [Reference index](#reference-index)

## Status vocabulary

- **Implemented** — present in the repository version reviewed for this record. Automated tests
  exercise the major controls, but test success is not proof that a control is effective in a
  production topology.
- **Deployment control** — must be enforced or verified by the platform, network, or operators.
- **Risk acceptance** — an intentional limitation that the authorizing organization must accept.
- **Gap** — required follow-up before representing the application as meeting the cited guidance.

## Evidence and claim discipline

The register distinguishes three kinds of statements so a citation is not mistaken for a compliance
claim:

- Repository facts in **Decision** and **Status** were checked against the named code, migrations,
  configuration, and tests in this revision. They can become stale after a code change and must be
  reviewed with each release.
- External requirements and recommendations are attributed to the linked NIST, IETF, OWASP, vendor,
  or project source. Normative language is paraphrased with its scope intact; a source supporting the
  rationale does not certify this implementation or satisfy an organizational control by itself.
- Architecture choices, threat analysis, and residual-risk statements are this project's reasoned
  conclusions. They are labeled as rationale, limitations, deployment controls, risk acceptance, or
  gaps rather than presented as quotations or universal requirements.

No MSS, MCS-COP, or PostgreSQL credential-format, scope, lifetime, or revocation behavior is
asserted beyond Data Mover's local input-shape validation because no provider specification was supplied
or relied upon. The application treats stored values as opaque high-value credentials. This review
is an engineering evidence check, not a penetration test,
cryptographic-module validation, SSP assessment, or authorization to operate.

## Assurance boundary

Data Mover keeps administrator-approved local accounts and authorization roles. In
`local_password` mode it proves only that a claimant knows the password bound to an account;
invitation acceptance and self-registration verification also prove access to an approved email
mailbox at that time. In `trusted_header` mode it instead accepts an email identity asserted by an
allowlisted immediate proxy, after that proxy performs the approved authentication. Self-registered
accounts remain inactive until an administrator approves them. Local application controls do
**not** prove that a mailbox holder or unverified header value is the real-world person claimed.

NIST separates identity proofing into identity resolution, evidence validation, and verification
that the applicant is the person to whom the evidence was issued. Email confirmation alone does not
satisfy that proofing process. NIST also states that passwords are not phishing-resistant and that
AAL2 requires two factors and must offer a phishing-resistant option. Therefore:

- **Risk acceptance:** treat this design as no more than an AAL1-style, single-factor mechanism,
  subject to the organization's own digital identity risk assessment.
- **Risk acceptance:** it is not CAC authentication, CAC-equivalent authentication, AAL2, or AAL3.
- **Risk acceptance:** a `.gov` or `.mil` domain allowlist is an eligibility filter, not identity
  evidence, employment verification, clearance verification, or authorization to access classified
  information.
- **Deployment control:** the system owner must decide whether password-only access is appropriate
  for each NIPR or SIPR use case and data type. If AAL2, phishing resistance, or PKI-backed identity
  is required, place the application behind an approved CAC/mTLS identity-aware proxy or replace the
  local flow with an approved federation mechanism.
- **Potential deployment blocker:** SP 800-63B-4 says federal agencies must select at least AAL2
  when personal information is made available online. It also says federal agencies must require
  staff, contractors, and partners to use phishing-resistant authentication for federal information
  systems at AAL2. Because this application exposes names, organization, job title, phone number,
  account activity, and session metadata, the authorizing privacy and security officials must decide
  whether those provisions apply. If they do, this password-only mode is not sufficient and an
  approved phishing-resistant authenticator such as CAC must be added.
- **Potential deployment blocker:** SP 800-63B-4 also requires cryptography used by federal agency
  verifiers at AAL1 and AAL2 to be FIPS 140 validated. This repository does not include evidence that
  its Python runtime and cryptographic modules are validated in the deployed operational environment.
  Even an otherwise acceptable AAL1 deployment cannot claim NIST conformance until that evidence is
  supplied.

Evidence:

- [NIST SP 800-63A-4, Identity Proofing](https://pages.nist.gov/800-63-4/sp800-63a.html#identity-proofing-overview)
  defines resolution, validation, and verification as separate proofing steps.
- [NIST SP 800-63B-4, Authentication Assurance Levels](https://pages.nist.gov/800-63-4/sp800-63b/aal/)
  defines AAL1, requires two factors plus a phishing-resistant option at AAL2, sets the federal
  personal-information minimum, and states the phishing-resistance rule for federal workforce users.
- [NIST SP 800-63B-4, Passwords](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#passwords)
  explicitly says passwords are not phishing-resistant.
- [NIST SP 800-63B-4, Out-of-Band Devices](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#out-of-band-devices)
  prohibits email as an out-of-band authenticator while distinguishing address validation and
  recovery codes from authentication.

## Threat model and trust boundaries

The design protects account credentials, registration, invitation and reset capabilities,
authenticated sessions, profile data, role assignments, and audit events against common remote web threats. Those threats
include credential guessing, user enumeration, stolen database rows, session theft and replay, CSRF,
XSS impact, broken object authorization, malicious links, and untrusted proxy headers.

The application trusts:

1. the Posit Connect or Workbench reverse proxy to be the only external path to the application;
2. the proxy to terminate approved TLS and sanitize security-relevant forwarding headers;
3. the database and its backups to enforce approved confidentiality, integrity, and availability;
4. the SMTP relay and mail system to deliver recovery links only within the approved environment;
5. the deployment secret store to generate, restrict, audit, and rotate high-entropy secrets; and
6. administrators to use their elevated role only for approved account-management duties.

Compromise of an endpoint, mailbox, administrator account, application host, database plus signing
secrets, SMTP infrastructure, or trusted proxy is outside what application-only controls can fully
mitigate. No application control changes the classification rules or cross-domain transfer rules for
NIPR and SIPR.

## Decision register

### SD-01 — Own credentials locally only when federation is unavailable

**Status:** Selectable boundary implemented; deployment approval remains required.

**Decision:** The application owns user records, recovery state, sessions, and roles. Local password
authentication is available only in `local_password` mode; production requires explicit
`PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=true`. The preferred phishing-resistant boundary is
`trusted_header` behind an approved CAC/MFA identity-aware proxy. Only an allowlisted immediate
proxy may assert the configured identity header, exactly one header value is accepted, password
token issuance, recovery, and change are disabled, and the asserted email must match an existing
active verified account. Invitation acceptance and registration verification do not create a local
password in this mode. No header-driven auto-provisioning or role assignment occurs.

**Rationale:** Trusted federation keeps primary authentication, CAC validation, MFA policy, and
credential lifecycle in an approved identity layer while preserving local application
authorization. Local mode remains available for explicitly accepted lower-assurance environments,
but transfers credential lifecycle, incident response, recovery, and deprovisioning duties to the
application owner and caps assurance at the password-only boundary described above.

**Deployment control:** The identity-aware proxy must block direct application access, strip all
client-supplied instances of the configured identity header, authenticate the user using the
approved mechanism, inject exactly one normalized account email, and be the only address included
for this purpose in `TRUSTED_PROXY_IPS`. Header trust does not itself implement or validate CAC/MFA.

**Required operations:** document account sponsors and owners, periodically reconcile accounts with
an authoritative personnel source, disable departed users promptly, and define help-desk identity
verification for mailbox-loss recovery. Email possession must not be used to infer employment,
clearance, need-to-know, or CAC identity.

**Evidence:** NIST SP 800-53 Rev. 5 control AC-2 requires defined account management, approvals,
monitoring, and disabling in the
[primary NIST publication](https://doi.org/10.6028/NIST.SP.800-53r5).

### SD-02 — Verify self-registration and require administrator approval

**Status:** Implemented, with deployment controls.

**Decision:** `ALLOWED_EMAIL_DOMAINS` is mandatory in production. Email addresses are parsed,
normalized, and compared against an exact domain allowlist. Invited users receive a single-use
administrator-created invitation. Self-registering users receive a separate 24-hour, single-use
verification capability and, in local-password mode, choose a password only after opening it. The
resulting account stays in
`pending` status until an administrator approves it; pending accounts cannot obtain JWTs or browser
sessions. A newer invitation revokes older outstanding invitations for the same address.
When `DIRECTORY_LOOKUP_URL` is configured, both enrollment paths also query a bounded-time,
non-redirecting HTTPS directory endpoint and require the returned record's normalized email to match
exactly. A private CA bundle and bearer credential are supported. `DIRECTORY_LOOKUP_REQUIRED`
selects fail-closed or fail-open behavior; explicit not-found and email mismatch reject enrollment
when fail-closed mode is enabled and remain advisory otherwise. Outbound directory requests use the
actively maintained HTTPX2 client and
the operating-system trust store by default; a configured private CA bundle produces an explicit
certificate-verifying `SSLContext` instead.

**Rationale:** Exact allowlisting prevents suffix tricks. Deferring password setup until mailbox
verification prevents a requester from pre-claiming someone else's address with a credential they
control. Separate administrator approval preserves human authorization and account sponsorship. The
emailed capability validates control of the address without claiming identity proofing.
The optional directory is likewise an eligibility/enrichment signal, not an authenticator, CAC
validator, identity-proofing service, or authorization source.

**Limitations:** Domain control is coarser than person-level authorization. Forwarded or compromised
mailboxes remain a risk. An administrator can make a mistaken approval, so approval procedures need
an authoritative personnel source and accountable sponsor. Domain ownership and accepted aliases
require an organization-maintained source of truth. Registration submission is intentionally
non-enumerating. The database-backed source/account limits reduce automated abuse, but the
fixed-window algorithm can burst at a boundary and does not provide device or risk signals.
Directory availability, certificate policy, response contract, data release, and fail-open/fail-
closed selection require a deployment-specific risk decision.

**Deployment control:** treat the configured directory URL and bearer token as protected
administrator configuration. Restrict egress to the approved directory, use enclave-controlled DNS,
monitor the configured hostname's resolved addresses, prohibit redirects, minimize returned
attributes, and document whether the selected directory qualifies as an authoritative or credible
source for the enrollment decision. The application timeout and fixed URL reduce exposure but do not
replace network-layer egress policy or protect against a compromised directory.

**Evidence:**

- [NIST SP 800-63A-4, Identity Proofing Overview](https://pages.nist.gov/800-63-4/sp800-63a.html#identity-proofing-overview)
  separates resolution, attribute/evidence validation, and verification of the applicant, and defines
  criteria for authoritative and credible sources. A matching directory record therefore cannot by
  itself establish that the requester is the person in that record.
- [NIST SP 800-63A-4 confirmation-code requirements](https://pages.nist.gov/800-63-4/sp800-63a.html#confirmation-codes)
  permits a secure link, limits email confirmation codes to 24 hours, requires invalidation after use,
  and describes email confirmation as proof of access to an address used for communications.
- [NIST SP 800-63A-4 security considerations](https://pages.nist.gov/800-63-4/sp800-63a/security/)
  identifies high-volume automated enrollment as a threat and validation against authoritative or
  credible sources as a mitigation for falsified attributes.
- [OWASP Input Validation](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
  recommends allowlisting for well-defined inputs.
- [OWASP SSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
  recommends allowlisting identified upstream services, disabling redirects, protecting DNS, and
  applying both application- and network-layer controls.

### SD-03 — Use length-first passwords without composition or periodic rotation

**Status:** Implemented with a deployment-supplied offline blocklist.

**Decision:** Passwords are 15–128 Unicode code points. Account creation and password-change paths
normalize them with NFC before hashing. The validator imposes no upper/lower/digit/symbol composition
rules and no periodic password expiration. Passwords matching a small local list, a configured
offline blocklist, or the email local part are rejected. Production requires a readable blocklist.
The same NFC normalization is used for hashing and verification. Forms use standard
password-manager autocomplete values, allow paste, and provide a visibility control.

**Rationale:** Fifteen characters is NIST's minimum for a single-factor password. NIST recommends a
maximum of at least 64, NFC normalization, no composition rules, no periodic changes absent evidence
of compromise, password-manager support, and comparison with known common or compromised values.

**Deployment control:** Supply, version, update, and test an organization-approved offline list with
Unicode-normalized complete passwords; never send candidate passwords to an Internet service from
NIPR or SIPR. The “email local part appears anywhere” test is broader than NIST's whole-password
comparison and should remain only if the organization approves it as a context-specific rule.

**Evidence:** [NIST SP 800-63B-4 section 3.1.1](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#passwords)
contains the minimum length, accepted-character, normalization, composition, rotation, blocklist,
rate-limit, paste, and password-manager requirements.

### SD-04 — Hash passwords with an adaptive salted scheme

**Status:** Implemented, with a FIPS deployment caveat.

**Decision:** Argon2id through `pwdlib` is the default. A PBKDF2-HMAC-SHA-256 mode with at least
600,000 iterations and a random 128-bit salt is available for a FIPS-constrained boundary. Stored
formats carry algorithm and work-factor metadata, and successful login rehashes an outdated verifier.
Passwords are never encrypted or stored in plaintext.

**Rationale:** Slow, salted password hashing raises the cost of offline guessing after a database
breach. Rehash-on-login permits work factors and algorithms to evolve.

**Deployment control:** benchmark the selected parameters on production-equivalent hardware and
record the selected `pwdlib`/Argon2 parameters in the SSP. Merely choosing PBKDF2 does not establish
FIPS 140 compliance: the exact cryptographic module, version, operational environment, configuration,
and certificate status must be approved. Python `hashlib` must not be described as validated without
that evidence.

**Evidence:**

- [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
  recommends Argon2id and PBKDF2-HMAC-SHA-256 with 600,000 or more iterations when FIPS is required.
- [RFC 9106 section 7.4](https://www.rfc-editor.org/rfc/rfc9106.html#section-7.4) gives Argon2id
  recommendations.
- [NIST SP 800-132](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-132.pdf)
  specifies PBKDF2 with an approved HMAC.
- [NIST CMVP validated-module guidance](https://csrc.nist.gov/Projects/cryptographic-module-validation-program/validated-modules)
  explains that an approved algorithm alone does not make a product or implementation FIPS 140
  validated.

### SD-05 — Return generic authentication responses and throttle guessing

**Status:** Implemented in the application; perimeter and progressive controls remain gaps.

**Decision:** Login uses the same user-facing failure for a missing user, bad password, disabled or
unverified account, pending-approval registration, disallowed email domain, and active lockout. A
fixed dummy password is verified with the configured password scheme when no verifier exists, an
oversized candidate is supplied, or the email fails domain policy. Five consecutive failures
atomically disable local-password authentication (`failed_login_attempts >= 5`) until a successful
password-reset, authenticated password change, administrator enable, or `create-admin` rebinding
flow; an ordinary later login cannot clear that terminal state. The `locked_until` column is cleared
on unlock paths but is not used as a timed lockout deadline. Registration, login, and reset paths
additionally increment atomic fixed-window buckets for
both source address and normalized account email in the shared application database. Bucket keys are
HMAC-SHA-256 digests under the session pepper, expired buckets are deleted, denials return
`Retry-After`, and denials are audited without recording the raw bucket key. Authentication outcomes
are audited (including server-only `pending_approval` outcomes that still present the generic client
message).

**Rationale:** Generic responses and comparable expensive work reduce account enumeration. The
terminal per-account action and shared source/account windows constrain bursts and provide useful
defense in depth. NIST permits agencies to choose a threshold lower than 100 and requires a terminal
action when that threshold is reached.

**Gap:** Terminal account lockout can be abused for denial of service, and neither lockout nor fixed
windows are a complete defense against distributed password spraying. PostgreSQL
shares the fixed-window application counters across Connect workers, but the trusted ingress must
still impose volumetric source limits before requests consume application/database resources and
should add progressive delay, device/risk signals, and alerting. Boundary bursts are possible, and
`RATE_LIMIT_ENABLED=false` must not be used without an approved replacement. Test response-time
distributions; a dummy hash does not prove timing uniformity.

**Evidence:**

- [NIST SP 800-63B-4 section 3.2.2](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#rate-limiting-throttling)
  requires throttling, sets 100 as an upper bound, and discusses progressive waits and risk signals.
- [NIST SP 800-63B-4 customer-experience guidance](https://pages.nist.gov/800-63-4/sp800-63b/customer/)
  says throttled users should be told how long they must wait, supporting the visible wait message and
  `Retry-After` response.
- [NIST SP 800-63B-4 reauthentication guidance](https://pages.nist.gov/800-63-4/sp800-63b/session/#reauthentication)
  identifies IP, browser/device, timing, and usage patterns as possible risk signals and explicitly
  notes their privacy implications. This supports avoiding raw identifiers in rate buckets while
  requiring source-IP audit retention and any future device signals to be covered by the privacy risk
  assessment.
- [RFC 9110 section 10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3)
  defines `Retry-After` as the expected delay before a follow-up request. The header improves client
  coordination; it is not itself a throttling control.
- [OWASP Authentication, error messages and throttling](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html#authentication-and-error-messages)
  recommends generic responses and account-based throttling while describing lockout denial-of-service
  tradeoffs.

### SD-06 — Issue short-lived, narrowly validated access JWTs

**Status:** Implemented.

**Decision:** Successful login returns a 10-minute HS256 access JWT. Validation permits only HS256
and requires `iss`, `aud`, `sub`, `sid`, `jti`, `iat`, `nbf`, and `exp`. The JWT carries UUID subject
and session identifiers, a security version, and roles; it does not carry email, name, or profile data.
Authorization never trusts the JWT alone: the referenced user and database session must still be
active and consistent.

**Rationale:** A short lifetime limits exposure while the explicit algorithm, issuer, audience, and
required claims defend against algorithm confusion and token substitution. HS256 is a simple fit for
one application that both signs and verifies; it is not intended for independent relying parties.

**Limitations:** HS256 has one shared signing secret and no `kid`-based overlap period. Rotating it
invalidates every access JWT. If multiple independent services must verify tokens, use an approved
asymmetric key-management and rotation design rather than distributing this secret.

**Evidence:** [RFC 8725 sections 3.1, 3.5, 3.8, and 3.9](https://www.rfc-editor.org/rfc/rfc8725.html#section-3)
requires algorithm verification, sufficient key entropy, issuer/subject validation, and audience
validation.

### SD-07 — Use opaque, database-backed, rotating refresh tokens

**Status:** Implemented, including token-family replay detection.

**Decision:** Refresh tokens are 32-byte cryptographically random opaque capabilities. Only an
HMAC-SHA-256 digest made with a separate session pepper is stored in `refresh_sessions`. Every
successful refresh atomically replaces the token, stores the consumed digest in family history,
and extends only the idle deadline, never the absolute deadline. Reuse of a consumed token revokes
the active family. Invalid refresh attempts clear browser cookies.

**Rationale:** Opaque server-side state supports revocation, avoids putting account data in the
refresh token, and limits the usefulness of a database-only token-table disclosure. Rotation makes a
previous value unusable after a successful refresh.

**Concurrency control:** Rotation is a conditional `UPDATE ... RETURNING`; invitation acceptance
and password-reset completion likewise consume their capability with a conditional update before
changing account state. PostgreSQL race tests issue each capability concurrently and require exactly
one winner. Refresh-token history retains keyed digests, not raw tokens.

**Evidence:** [RFC 9700 section 4.14](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.14)
requires refresh-token confidentiality, expiration/revocation, and either sender constraint or
rotation that retains the relationship needed to detect replay. This application is not an OAuth
authorization server, but the token-theft analysis and rotation pattern apply directly.

### SD-08 — Couple JWTs to live server-side state for immediate revocation

**Status:** Implemented.

**Decision:** Every authenticated request verifies the JWT and then checks that the user is active,
the session is present and unrevoked, idle and absolute deadlines have not passed, the session belongs
to the subject, and the user's `security_version` matches the token. Password changes, password
resets, role changes, and disabling an account increment the version and/or revoke sessions.

**Rationale:** A signed JWT normally remains usable until expiration. The database check is an
intentional tradeoff of statelessness for immediate account, privilege, and session invalidation.

**Evidence:** [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
requires server-side session expiration and recommends reauthentication and session renewal after
risk events; [RFC 9700 section 4.14](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.14.2)
recognizes password change and logout as refresh-token revocation events.

### SD-09 — Store browser credentials in narrowly scoped hardened cookies

**Status:** Implemented, with a proxy deployment dependency.

**Decision:** The browser receives access and refresh credentials in `HttpOnly`, `SameSite=Lax`
cookies with no `Domain` attribute. Production refuses to start unless `Secure=true`. Cookie `Path`
is restricted to the externally visible application mount, derived at request time for Posit Connect
and Workbench, so the same code works at `/` and under dynamic proxy prefixes. Browser storage is not
used. Logout expiration cookies use the same path and security attributes as the live cookies.

**Rationale:** `HttpOnly` prevents direct JavaScript reads, `Secure` confines transport to HTTPS,
`SameSite` reduces cross-site attachment, omission of `Domain` makes the cookie host-only, and a
minimum practical path avoids sending credentials to unrelated applications on a shared host.

**Intentional deviation:** a `__Host-` cookie must use `Path=/`; that conflicts with least-path
scoping on shared Posit hosts. The application chooses a host-only, application-path cookie and
therefore cannot use the `__Host-` prefix. This makes trusted proxy isolation and header sanitation
mandatory.

The access cookie is also a signed JWT rather than the opaque cookie value NIST recommends. This is
required by the decision to return and use the same short-lived JWT for API access. Its payload omits
direct profile data, and every request is still bound to live server-side state, but anyone who
obtains the cookie can decode its UUIDs and role names and use it as a bearer credential until it is
expired or revoked.

**Evidence:** [NIST SP 800-63B-4, Browser Cookies](https://pages.nist.gov/800-63-4/sp800-63b/session/#browser-cookies)
requires `Secure`, minimum practical host/path, and recommends `HttpOnly` and `SameSite`; it also
warns against insecure local storage. [OWASP Session Management, Cookies](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#cookies)
explains the attributes and cookie-prefix tradeoffs.

### SD-10 — Require synchronizer CSRF tokens for cookie-authenticated changes

**Status:** Implemented.

**Decision:** Each database session has an independent random CSRF value. All authenticated
state-changing browser requests must return it in a form field or
`X-CSRF-Token` header; comparison is constant-time. `SameSite=Lax` is defense in depth, not the sole
control. Password, federated login, self-registration, and forgot-password forms use a separate
one-hour signed double-submit token in an `HttpOnly`, `SameSite=Strict` cookie scoped to the
application mount; `CSRF_SECRET` signs that pre-authentication token.

**Rationale:** Cookies are automatically sent by the browser, so authentication alone cannot
distinguish a forged cross-site request. A server-held synchronizer value supplies that distinction.

Reset and invitation forms are authorized by their high-entropy one-time capability, but still
require URL and log protections described in SD-13.

**Evidence:** [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
recommends synchronizer tokens for stateful applications, tokens on all state changes, `SameSite` as
defense in depth, and explicit consideration of login CSRF.

### SD-11 — Enforce short idle and absolute sessions on the server

**Status:** Implemented.

**Decision:** Access JWTs last 10 minutes. Database sessions have a 30-minute idle deadline and an
eight-hour absolute deadline by default. Refresh can move the idle deadline only up to the absolute
deadline. Logout invalidates the current session; users can list and revoke their sessions or revoke
all sessions. Deadlines are enforced server-side, not inferred from cookie expiry.

**Rationale:** Short access credentials reduce exposure; idle and absolute limits bound stolen-token
use. User-visible session termination provides control on shared endpoints.

**Deployment control:** the system owner must confirm that these values match mission sensitivity,
endpoint lock policy, and the selected assurance profile. Do not increase them only for convenience.

**Evidence:** [NIST SP 800-63B-4, Session Management](https://pages.nist.gov/800-63-4/sp800-63b/session/)
requires random session secrets, invalidation on logout, protected transport, expiry, and a logout
mechanism. [OWASP Session Management, Session Expiration](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#session-expiration)
recommends server-side idle and absolute timeouts.

### SD-12 — Prefer cookie sessions for the HTMX UI

**Status:** Implemented (cookie-session UI; no separate public JSON auth API).

**Decision:** Data Mover is a cookie-session HTMX application. Successful login sets HttpOnly
access and refresh cookies scoped to the deployment mount. Optional `Authorization: Bearer` may
carry the same access JWT for clients that already hold it, but there is no `/api/v1/auth/token`
JSON issuance endpoint and bearer credentials do not skip CSRF: authenticated mutations still require
the session synchronizer when the request authenticates through the browser cookie session. If a
bearer token is present it is authoritative for identity lookup; an invalid bearer request does not
silently fall back to a valid cookie.

**Rationale:** A single browser UI surface keeps CSRF rules simple and avoids maintaining a parallel
public API. Automatic cookie attachment still requires CSRF protection for state-changing requests.
The refresh path during normal navigation is a session-maintenance exception: it rotates a cookie
credential without a CSRF token and relies on `SameSite=Lax`, the same-origin response boundary, and
the attacker's inability to read the new token. Review this exception if cross-origin clients or
cookie policy change.

**Evidence:** [RFC 6750 section 2](https://www.rfc-editor.org/rfc/rfc6750.html#section-2)
defines Authorization-header bearer use and warns clients not to use more than one token transport
method per request. [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
explains why automatically attached cookies require CSRF protection.

### SD-13 — Make registration, invitation, and reset links expiring one-time capabilities

**Status:** Implemented, with operational gaps.

**Decision:** Registration verification, invitations, and password resets use 32-byte random
URL-safe tokens whose dedicated database columns contain only HMAC digests. Registration links
expire after 24 hours, invitations after 48 hours, and resets after 30 minutes. Completing one
registration verification invalidates the user's other outstanding registration links. A new
invitation revokes prior invitations for the address, and a new reset invalidates prior resets for
the user. `GET` validates and displays the form but does not consume the token, so mail-link scanners
cannot complete the action. Only `POST` accepts or consumes it. Password reset does not log the user
in, revokes all sessions, increments the security version, and sends a change notification.
Forgot-password and registration-submission responses are generic.

**Rationale:** High-entropy, bounded, single-use tokens resist guessing and replay. Preview-only GET
also preserves HTTP safe-method semantics and makes automated link inspection less destructive.

**Gaps and deployment controls:**

- Raw capability URLs necessarily appear in the email body and can therefore exist in
  `email_outbox.body_text`, SMTP systems, mailbox storage, browser history, and proxy request logs.
  Database columns for registration, invitation, reset, and session tokens store HMAC digests only;
  plaintext tokens are not retained in those columns after issue. Outbox and mail paths are a
  separate residual-risk surface until bodies are redacted after delivery.
- Restrict database/outbox access, encrypt storage and backups as required, define the shortest
  workable outbox retention, and purge or redact bodies after delivery. Configure every proxy and
  log collector to redact query parameter `token`; never put tokens in audit details.
- `Referrer-Policy: no-referrer` limits browser referrer leakage, but it cannot sanitize upstream
  access logs or mail systems.
- Per-account and per-source reset-request fixed-window throttling is implemented. Its boundary-burst,
  distributed-source, availability, and timing limitations are the same ones recorded in SD-05;
  verify response-time distributions rather than assuming generic text makes timing uniform.

**Evidence:** [OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
requires generic and timing-consistent responses, automated-submission controls, cryptographically
random long tokens, secure storage, expiry and single use, ordinary login after reset, notification,
and session invalidation. [OWASP Logging, Data to Exclude](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#data-to-exclude)
identifies access tokens and session identifiers as values that should not be recorded directly.

### SD-14 — Enforce authorization on the server for every object and action

**Status:** Implemented.

**Decision:** Authentication and administrator checks are FastAPI dependencies on protected routes.
Users can retrieve and change only their own profile and sessions. Administrator routes require the
`administrator` role. Role/status changes immediately invalidate affected credentials. An
administrator cannot disable their own account or remove their own administrator role through the
provided endpoints.

**Rationale:** UI visibility is not authorization. Central dependencies, ownership checks, default
denial, and immediate invalidation reduce horizontal and vertical privilege escalation. Self-lockout
guards reduce accidental loss of the last usable administrative path, though they do not prove that
another administrator exists.

**Limitations:** The two roles are deliberately coarse and administrators can manage other
administrators. High-impact deployments should consider separate account-administration and audit-
review roles, dual control, a guaranteed break-glass procedure, and phishing-resistant MFA for
privileged users.

**Evidence:** [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
recommends least privilege, deny by default, ownership enforcement, and validation on every request.
NIST SP 800-53 Rev. 5 control AC-6 defines least privilege in the
[primary NIST publication](https://doi.org/10.6028/NIST.SP.800-53r5).

### SD-15 — Record security events without credential material

**Status:** Implemented locally; centralized protection is a deployment control.

**Decision:** Structured records cover login outcomes, session lifecycle, profile and password
changes, invitation actions, and administrator changes. Records include event type, outcome,
timestamp, actor/target UUIDs, request ID, source IP, and narrowly selected JSON detail. Tokens,
passwords, password hashes, signing keys, and session-cookie values are not audit fields. Only an
administrator can view the most recent application audit records. `X-Forwarded-For` contributes a
source address only when the direct peer is explicitly in `TRUSTED_PROXY_IPS`; otherwise the direct
peer address is used. Rate-limit denials record the affected dimensions, not raw HMAC inputs.

**Rationale:** Authentication and administration events support investigation and accountability;
excluding credentials avoids turning logs into a secret store.

**Deployment control:** the local table is neither append-only nor tamper-evident. Export events to
an approved centralized collector, synchronize clocks, restrict access, monitor ingestion failure,
define retention and disposal, alert on guessing/lockouts/privilege changes, and protect records from
modification and deletion. Treat email addresses and source IPs as sensitive data.

**Evidence:** [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
recommends authentication and user-administration logging, excludes passwords/access tokens/session
IDs, and requires protection against unauthorized access, modification, and deletion. NIST SP
800-53 Rev. 5 controls AU-2, AU-3, AU-9, and AU-11 cover event selection, record content, protection,
and retention in the [primary publication](https://doi.org/10.6028/NIST.SP.800-53r5).

### SD-16 — Apply restrictive browser headers and self-host frontend assets

**Status:** Implemented; HSTS scope remains a deployment decision.

**Decision:** Every application response receives `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`, and a
Content Security Policy limited to same-origin scripts, styles, fonts, and connections, with
`frame-ancestors 'none'`, `base-uri 'self'`, and `form-action 'self'`. Production adds one-year HSTS;
`includeSubDomains` is enabled only with `HSTS_INCLUDE_SUBDOMAINS=true`. Application CSS and
progressive-enhancement JavaScript are served from `/assets` via Starlette `StaticFiles`; Hedron's
own assets are served under `/hedron-static/`. There is no SPA fallback and no runtime CDN or Node
dependency.

**Rationale:** These headers reduce script injection impact, clickjacking, MIME confusion, referrer
leakage, browser feature exposure, and transport downgrade. Self-hosting enables `script-src 'self'`
and makes production independent of an external CDN.

**Decision detail:** Every non-static response also receives `Cache-Control: no-store`. HSTS
`includeSubDomains` must be reviewed with the owner of the hostname before production because it
affects descendant hosts.

**Evidence:**

- [OWASP HTTP Security Response Headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
  documents the selected header defenses.
- [OWASP Content Security Policy](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
  describes CSP as defense in depth for XSS and data injection.
- [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
  recommends `Cache-Control: no-store` for sensitive responses and HSTS for HTTPS applications.
- [Starlette StaticFiles](https://www.starlette.io/staticfiles/) confirms that mounted static
  directories serve existing files through normal ASGI middleware without SPA fallback.

### SD-17 — Require TLS and production-safe startup configuration

**Status:** Implemented fail-fast checks; TLS itself is a deployment control.

**Decision:** Production configuration refuses to start with insecure cookies, a non-HTTPS or
malformed public URL, SQLite, console email, SMTP without a host and STARTTLS, missing domain
allowlists, weak placeholder application secrets, retained delivered email bodies, a missing
offline password blocklist, disabled application rate limits, or `DIRECTORY_LOOKUP_REQUIRED` without
a configured HTTPS directory URL. PostgreSQL must use the installed `psycopg` driver. Invalid ports,
unsafe header-bearing configuration values, malformed routing paths, and missing configured CA
bundle files also fail validation. Interactive API documentation is disabled in production. TLS is
expected to terminate at the approved Posit/reverse-proxy boundary; HSTS and Secure cookies are
applied by the application.

**Rationale:** Failing startup is safer than silently deploying known development defaults. TLS is
required for password and bearer-token confidentiality, integrity, and server authentication.

**Deployment control:** verify the full client-to-proxy and proxy-to-application path, approved TLS
versions/ciphers/certificates, HTTP-to-HTTPS behavior, and that the application cannot be reached
directly around the proxy. Startup validation proves configuration shape, not that
`PUBLIC_BASE_URL` names the organization-approved external route; operators must still verify that
exact routing before sending invitations or resets.

**Evidence:** [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
requires TLS for login and all authenticated pages, Secure cookies, no mixed transport, and protected
proxy links. [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#password-verifiers)
requires an authenticated protected channel when collecting passwords.

### SD-18 — Keep application secrets out of source and separate by enclave

**Status:** Application secret uses implemented; lifecycle remains a deployment control.

**Decision:** JWT signing, session-token hashing, SMTP, and database secrets come from deployment
configuration, not committed values. Production requires long non-placeholder JWT and session
secrets. NIPR and SIPR must use different secrets, databases, SMTP relays, backups, accounts, and
deployment pipelines; no application feature moves data across the boundary.

**Rationale:** Independent secrets constrain compromise and prevent tokens or data from one enclave
being accepted in another. A managed secret lifecycle provides access control, audit, rotation, and
incident response that environment files alone cannot.

`CSRF_SECRET` signs short-lived pre-authentication login CSRF tokens and is separate from the random
server-stored synchronizer values used after authentication.

**Deployment control:** use an approved secret manager or protected Connect configuration, generate
at least 256 random bits for JWT and session secrets, restrict human and service access, prohibit
logging, and document rotation. JWT-secret rotation invalidates access tokens; session-pepper
rotation invalidates all refresh, registration-verification, invitation, and reset capabilities and
changes rate-limit bucket identifiers, and therefore needs a planned user-impact procedure.

**Evidence:** [RFC 8725 section 3.5](https://www.rfc-editor.org/rfc/rfc8725.html#section-3.5)
requires sufficient JWT-key entropy. [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
documents least-privilege access, automated rotation, auditing, revocation, and no logging of secrets.

### SD-19 — Treat SMTP and the email outbox as security-sensitive systems

**Status:** Asynchronous delivery, retry, and dead-letter controls implemented; relay operation is a
deployment control.

**Decision:** Request handlers queue messages transactionally and attach a FastAPI background task
to successful email-producing responses. The task claims due rows atomically with PostgreSQL
`FOR UPDATE SKIP LOCKED`, sends outside the claim transaction, applies bounded exponential backoff,
and marks exhausted messages as dead letters. Operators can explicitly requeue all or one approved
dead letter. The SMTP backend uses STARTTLS with hostname and certificate validation through the system
trust store or `SMTP_CA_BUNDLE`, plus optional relay authentication. The application sends
registration/invitation/reset URLs and account-status or password-change notifications (including
authenticated password changes from the security page), never passwords.

**Rationale:** Transactional queuing avoids losing a message when the surrounding database operation
commits. Change notifications give users an independent signal of possible compromise.

**Limitations:** SMTP delivery is at-least-once: a process failure after relay acceptance but before
the final database commit can produce a duplicate message. Production startup requires SMTP,
STARTTLS, and post-delivery body redaction, but application validation cannot prove the relay's
certificate issuance policy or operational approval. Pending and dead-lettered bodies still contain
capability URLs and require strict access and retention controls.

**Deployment control:** require an approved enclave-local relay and protected route, verify TLS and
certificate behavior, prohibit console email in production, restrict outbox and backup readers,
monitor delivery failures and dead letters, and define the shortest workable retention/purge
period for pending and failed bodies. Mail administrators must define equivalent retention and
access controls downstream.

**Evidence:** [OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
requires secure reset-token handling and post-change notification. [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
describes TLS confidentiality, integrity, and authentication. [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#data-to-exclude)
supports excluding or specially protecting token and session values.

### SD-20 — Trust proxy routing data only at an isolated ingress

**Status:** Implemented parsing and direct-peer enforcement; ingress isolation is a deployment control.

**Decision:** The application derives its external mount path from Posit Connect's
`RStudio-Connect-App-Base-URL` only when the direct peer is allowlisted, or from the ASGI `root_path`
used by Workbench. Values are reduced to a
same-origin absolute path and values containing protocol-relative paths, backslashes, query strings,
fragments, or control characters are rejected. The result prefixes application links, form actions,
HTMX paths, static asset URLs, and scopes cookies. A narrow middleware also handles duplicate ASGI
root paths, Workbench
`/proxy/<port>/...` mounts, and encoded absolute-URL paths. Source IP accepts the first
`X-Forwarded-For` value only when the direct peer is in the explicit `TRUSTED_PROXY_IPS` allowlist;
malformed values are ignored.

**Rationale:** Connect applications do not know their external base URL ahead of time, while
Workbench FastAPI applications run behind a dynamic ASGI root path. Runtime resolution allows one
codebase without unsafe cross-origin redirects. When an interactive Workbench runtime supplies a
full HTTP(S) `UVICORN_ROOT_PATH`, the launcher treats that runtime-provided URL as the public base so
Hedron can compare encoded absolute request targets with the expected origin. After Hedron validates
the URL, the launcher removes the origin-bearing variable from Uvicorn's reload environment and
passes the normalized mount directly to Hedron. Hedron can then decode the absolute proxy target
before establishing the local ASGI `root_path`. Path-only values are not promoted, explicit operator
configuration takes precedence, and unexpected origins continue to fail closed with `FWB-0006`.

**Deployment control:** clients must not reach the app server directly. The final trusted proxy must
remove inbound client-supplied `RStudio-Connect-App-Base-URL`, `Forwarded`, and `X-Forwarded-*`
values and set authoritative values itself. Until that is verified, source IP is untrusted display
and audit context and must never drive authorization or lockout. Protect the proxy-to-app link from
eavesdropping, injection, and replay.

**Evidence:**

- [Posit Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/) states that the
  externally visible base URL is supplied at runtime because Connect routes through an internal proxy.
- [Posit Workbench proxy documentation](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html#fastapi)
  requires FastAPI's ASGI `root_path` behind Workbench's proxy.
- [RFC 9700 section 4.13](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.13) requires a TLS-
  terminating reverse proxy to sanitize security-relevant inbound headers and protect its upstream
  link.
- [RFC 7239 section 8.1](https://www.rfc-editor.org/rfc/rfc7239.html#section-8.1) warns that forwarded
  values can be modified by clients or intermediaries, recommends verifying/allowlisting trusted
  proxies, and notes that even an allowlist cannot make the earlier chain trustworthy or replace a
  protected proxy-to-origin link.

### SD-21 — Use SQLite only for development and an approved service for production

**Status:** Versioned schema control implemented; production operation is a deployment control.

**Decision:** Twelve ordered Alembic revisions create the baseline, self-registration, shared
rate-limit, user credential, atomic-token/email-worker, encryption-key-usage, saved-pipeline,
catalog/connection-health, CSV-source, real-transfer-run, user-color-mode, and dark-mode-default
schemas. Application startup verifies the current revision and refuses to serve a stale or unversioned schema;
`python -m app migrate` is an explicit release action. Legacy `create_all()` databases require the
explicit `--adopt-existing` path, which verifies known table/column shapes before stamping and
upgrading. Administrator bootstrap is a separate `create-admin` command and is never migration
data. The production core dependency set includes the `psycopg` PostgreSQL driver; deployment still
requires an approved managed or operated database, backup, migration, encryption, access-control,
monitoring, and recovery process.

**Rationale:** Credential, session, outbox, profile, role, and audit data require concurrent access,
durability, backup, operational monitoring, and controlled schema change beyond the local developer
configuration.

**Deployment control:** prove encryption in transit and at rest, least-privilege service credentials,
backup confidentiality and restore tests, patching, high availability as required, migration review,
forward and rollback rehearsal on a restored copy, retention/deletion, and separate instances and
keys for NIPR and SIPR. Take and verify a recoverable backup before adoption or migration. Alembic
downgrades are operator actions and may intentionally remove schema/data; they are never an account
deprovisioning mechanism. Database compromise exposes password verifiers and raw queued email bodies
even though dedicated capability columns are hashed.

**Evidence:**

- [NIST SP 800-53 Rev. 5, current CSRC publication page](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final)
  includes the CM-3 configuration change-control, SA-10 developer configuration-management, and
  SA-11 developer testing/evaluation controls. Versioned, reviewed migrations and schema-drift tests
  support evidence for those controls but do not satisfy organizational approval, separation of
  duties, or production change-control requirements by themselves.
- [Alembic's official tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html) documents
  revision scripts, ordered upgrade/downgrade operations, and recording the current database
  revision. It supports the selected mechanism, while the application's explicit release procedure
  is the project-specific control.
- The broader [NIST SP 800-53 Rev. 5 catalog](https://doi.org/10.6028/NIST.SP.800-53r5) covers access
  control, cryptographic protection, backup, audit protection, contingency, and configuration
  management; the exact baseline and parameters must come from the system's impact level and
  authorization package.

### SD-22 — Prefer a server-rendered, progressively enhanced frontend

**Status:** Implemented.

**Decision:** FastAPI executes authentication and authorization, Hedron typed components render HTML,
and HTMX progressively enhances same-origin forms and partial updates. The browser never determines
access rights. Node is not a runtime or deployment dependency. Application static files are served
under `/assets` with fallback disabled, after normal application routes, and receive the same
security middleware. Hedron injects its bundled HTMX from `/hedron-static/htmx.min.js`; the app adds
progressive-enhancement behavior from `/assets/app.js`. HTMX's built-in indicator style injection is
disabled declaratively because the local stylesheet supplies the indicator rules; this keeps HTMX
from attempting an inline style that the application's CSP intentionally blocks. HTMX expression
evaluation and response script execution are disabled. Authenticated navigation uses an in-shell
`#main-panel` swap with a modest HTMX history cache; history restore refreshes stale filter state
through an explicit server request (`HX-History-Restore-Request`). Mutation endpoints negotiate
fragments for `HX-Request: true`; ordinary form submissions redirect to or render complete pages.
Expected error fragments remain visible for `4xx` and `5xx` responses, while unexpected HTMX errors
are retargeted to a global live region and expired sessions receive `HX-Redirect` independently of
the browser's `Accept` header.

**Rationale:** This architecture fits Workbench's no-Node environment and keeps the security boundary
on the Python server. A small, self-hosted script surface supports a restrictive CSP and removes a
runtime CDN dependency. It does not make XSS impossible; output encoding (Hedron's HTML escaping),
dependency review, CSP, and server authorization remain required.

**Deployment control:** record the HTMX file version and integrity hash shipped by the Hedron
package, review upgrades, scan and pin Python dependencies through the approved supply-chain process,
and never bypass Hedron's HTML escaping for untrusted values without a security review.

**Gap:** the repository has no software bill of materials or recorded HTMX acquisition/provenance
evidence beyond the pinned Hedron dependency. Approved vulnerability scanning, artifact attestation,
and supply-chain review remain production gates.

**Evidence:** [FastAPI Frontend](https://fastapi.tiangolo.com/tutorial/frontend/) documents route
precedence, middleware application, static-asset serving, and disabled fallback. [OWASP CSP](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
describes CSP as an additional layer rather than a replacement for secure output handling.
[HTMX's indicator documentation](https://htmx.org/attributes/hx-indicator/) recommends disabling
`includeIndicatorStyles` and hosting the indicator CSS when CSP blocks inline style tags.

### SD-23 — Test browser security behavior through both proxy path models

**Status:** Unit/integration coverage implemented; full browser e2e harness is deferred.

**Decision:** Connect and Workbench path normalization, trusted-proxy header handling, and cookie
path scoping are exercised by automated unit and HTMX fragment tests (including Workbench
`/s/<session>/p/<port-id>/` style `root_path` cases). A full Playwright multi-profile proxy suite is
not shipped in this repository; production-equivalent browser verification remains a deployment
control when required by the authorization package.

**Rationale:** `TestClient` and fragment clients catch deterministic route, header, and HTMX
contract failures. They do not replace enterprise-browser cookie selection or a real reverse proxy's
path and header transformations.

**Deployment control:** before authorization and after proxy/platform changes, repeat security tests
at the real external URLs using supported enterprise browsers, production-equivalent headers and TLS,
and non-production accounts/data. Preserve results with the reviewed release. Do not treat functional
suites as penetration testing.

**Evidence:**

- NIST SP 800-53 Rev. 5 control SA-11 in the
  [current CSRC publication](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final) calls for developer
  testing and evaluation; the organization must select the applicable rigor and evidence.
- [OWASP WSTG cookie-attribute testing](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/02-Testing_for_Cookies_Attributes)
  provides browser-observable checks for cookie scope, lifetime, `Secure`, `HttpOnly`, and `SameSite`.
- [OWASP WSTG logout testing](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/06-Session_Management_Testing/06-Testing_for_Logout_Functionality)
  calls for verifying browser cookie behavior, server-side invalidation, and loss of access to
  authenticated pages after logout.
- Posit's [Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/) and
  [Workbench FastAPI proxy documentation](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html#fastapi)
  establish that the two platforms expose different runtime base-path signals that require testing.

### SD-24 — Encrypt user-owned connection credentials and restrict provider slots

**Status:** Storage, owner-management, and durable worker execution are implemented; arbitrary-run
isolation remains intentionally unsupported and is a deployment/integration gap if introduced later.

**Decision:** Authenticated users may store at most one credential bundle for each explicitly
supported provider: MSS, MCS-COP, and PostgreSQL. Provider names and target
environment-variable names are application constants, not user-controlled inputs. The UI and
server routes expose configuration metadata but no plaintext retrieval operation. Replacing a
bundle encrypts a complete new value in the same provider slot; deleting it removes the ciphertext.
Administrators have no route that retrieves another user's credentials. Data Mover validates required
fields, surrounding whitespace, bounded lengths, ports, URLs, and displayed transport-mode choices.
Storage and connector health checks do not prove that a credential is minimally scoped,
unexpired, or unrevoked at its provider.

Pipeline catalogs expose only the current user's stored bundles whose validation status is `connected`.
Pipeline persistence repeats that provider-availability check on the server, so hidden or stale
browser options cannot be submitted directly. The explicit **Test connection** action decrypts only
the selected owner's bundle in the web process and closes the provider client after the health check.
Real transfers are enqueued by the web process and run in `pipeline-worker`; after claiming a lease,
the worker decrypts only the credential bundles required by the saved snapshot (none for a CSV source,
one for a CSV-to-provider run, or two for a provider-to-provider run). Built-in connectors receive
those values as in-process mappings; the worker is a separate supervised process, not an arbitrary-code
sandbox. Foundry hosts must be on the operator allowlist and real-mode writers must be explicitly
enabled. The local `seed-demo-connections` helper uses reserved `.demo.invalid` hosts and explicit
fake values, does not overwrite by default, and refuses to run when `APP_ENV=production` or
`DATA_MOVER_MODE=real`.

Real transfer execution also uses server-reloaded, owner-scoped pipeline snapshots, an idempotency
token when supplied by the browser, a single lease token for worker ownership, heartbeats, cooperative
cancellation, bounded batches, maximum source/run/spool sizes, and redaction before run events and
verification facts are persisted. These controls limit accidental duplication, concurrent ownership,
resource exhaustion, and secret leakage; they do not make a provider credential least-privileged or
revoke it at the provider.

Foundry requests use bounded connect/read/write timeouts, certificate verification through the system
trust store or an explicitly configured CA bundle, no automatic redirects, encoded dataset/file path
segments, and a host allowlist. Non-loopback HTTP endpoints are normalized to HTTPS; loopback HTTP is
retained only for local development and must not be placed in a production allowlist. Foundry source
files are streamed into a run-scoped temporary directory, limited by `PIPELINE_MAX_SOURCE_BYTES`, and
only CSV/Parquet suffixes are accepted. Foundry destinations stage Snappy Parquet data under the
protected spool root, use per-run names and bounded chunk files, enforce
`PIPELINE_MAX_SPOOL_BYTES`, and remove the final and chunk staging artifacts on completion or abort;
the janitor removes stale files and chunk directories. PostgreSQL identifiers are validated or passed
through `psycopg.sql.Identifier`, provider values use parameterized queries/COPY, and the configured
`sslmode` plus connection/statement/idle timeouts apply to each connection.

Each credential bundle is encrypted with a random 256-bit data-encryption key using AES-256-GCM and fresh nonces.
The data key is independently wrapped with the active key from `API_TOKEN_ENCRYPTION_KEYS`. Additional
authenticated data binds both ciphertexts to the environment-independent format version, owner,
secret record, and provider. The database stores the ciphertexts, nonces, and non-secret master-key
identifier. Old master keys remain in the configured key ring while records reference them. Creation,
replacement, deletion, and run-boundary use are audited without credential material. Authenticated
credential pages and fragments set `Cache-Control: no-store`. Each data-key wrap atomically increments a
database counter for its master-key ID. The operation fails closed at
`API_TOKEN_MAX_WRAPS_PER_KEY` (one million by default). Because historical replacements cannot be
reliably attributed to a key, migration marks every key with pre-counter ciphertext above the
maximum configurable ceiling. Those keys remain available for decryption but cannot wrap again;
operators must configure a fresh active key after upgrade.

**Research-backed rationale:**

| Design choice | Security justification | Residual boundary |
| --- | --- | --- |
| Treat saved values as high-value capabilities | [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750.html#section-5) explains that any party possessing a bearer token can use it and identifies disclosure and replay as threats. This directly applies to Advana/MSS tokens and supports the same conservative handling for database passwords: TLS, encrypted storage, non-reveal responses, and never placing values in URLs. | These controls do not narrow the privileges encoded by a token or database account. Users must issue the least-privileged credential at the provider. |
| Three encrypted credential slots, fixed environment-variable names, and owner-scoped queries | [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html#enforce-least-privileges) recommends least privilege, deny-by-default behavior, and permission checks on every request. An allowlist prevents users from inventing environment-variable names that could alter runner behavior; owner predicates prevent cross-user object access. CSV is a separate local source type and is not an encrypted provider credential slot. | A compromised owner account can replace or delete that owner's credentials. The current AAL1-style authentication boundary may be insufficient for high-value credentials. |
| Per-record AES-256-GCM with fresh 96-bit nonces and context-bound AAD | [NIST SP 800-38D](https://doi.org/10.6028/NIST.SP.800-38D) specifies GCM as authenticated encryption with associated data, and the [`cryptography` AES-GCM API](https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM) requires a nonce never be reused with a key. Random per-record data keys and fresh nonces protect confidentiality and detect modification; AAD causes decryption to fail if ciphertext is moved to a different owner, record, provider, or purpose. | Randomness depends on the operating-system CSPRNG. An atomic aggregate counter fails closed at a conservative configured wrap limit, but the organization must approve that ceiling, monitor it, and rotate early. The deployed module and environment still need required FIPS evidence. |
| Envelope encryption and a versioned key ring separate from the database and auth keys | [NIST SP 800-57 Part 1 Rev. 5](https://doi.org/10.6028/NIST.SP.800-57pt1r5) covers protection, lifecycle, cryptoperiods, backup, and recovery of keying material. [OWASP Cryptographic Storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html#key-management) recommends storing keys separately from encrypted data and designing for rotation. The active key protects new data while retained key identifiers permit controlled migration and recovery. | The key ring is available to the FastAPI process. Database-only theft does not disclose plaintext, but application-host or key-ring compromise can. Loss of an old referenced key permanently loses the associated tokens. |
| No plaintext read endpoint or UI reveal | GitHub's [Actions secrets REST API](https://docs.github.com/en/rest/actions/secrets) lists secret metadata without returning encrypted values. Following that pattern reduces routine exposure in browsers, support workflows, and admin tooling. | This is product-level non-disclosure, not end-to-end encryption. Privileged host operators and trusted application code remain in the security boundary. |
| Metadata-only audit events and no-store responses | [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html#23-logging) says secrets must not be logged and recommends auditing who requested or used them. GitHub warns that [automatic redaction is not guaranteed](https://docs.github.com/en/actions/reference/security/secure-use#using-secrets), so correctness cannot depend on a masking heuristic. | Application, proxy, runner, artifact, exception, and crash-dump paths all require deployment testing. `no-store` controls caching; it cannot prevent a compromised browser or endpoint from reading a token while it is entered. |
| Explicit delivery only at an authorized action or run boundary | OWASP describes controlled [secret consumption](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html#25-secret-consumption) and warns that environment variables may leak through logs or dumps. The web process decrypts only for the user's explicit connection test; the worker decrypts only after it claims the corresponding run lease. Posit documents that local content processes [inherit Connect server environment variables by default](https://docs.posit.co/connect/admin/appendix/configuration/#inherit-system-env-vars). | The worker and web process are trusted application-code boundaries and both receive the master-key ring. `Applications.InheritSystemEnvVars=false` does not sanitize subprocesses created by the application. If arbitrary code is ever granted a bearer value, it can copy or transmit it; an explicit child environment and process/filesystem/network isolation must then be added. |
| Prefer short-lived credentials when providers support them | OWASP recommends limiting secret lifetime and automating rotation. GitHub's [OIDC guidance](https://docs.github.com/en/actions/concepts/security/openid-connect) uses short-lived, job-specific credentials instead of stored long-lived secrets; Posit similarly documents [process-lifetime API keys](https://docs.posit.co/connect/admin/content-management/api-keys/#automatic-provisioning). | Data Mover currently accepts static user-supplied connection bundles. Provider-side OAuth, federation, scope, expiration, and revocation remain future integration work. |

**Limitations and deployment controls:** The FastAPI and pipeline-worker processes receive the
master-key ring and can therefore decrypt all stored credential bundles; encryption primarily
separates a database-only compromise from the key material. The current production path is a real
transfer path, not a browser simulation: the web process enqueues, and a separately supervised worker
contacts the built-in Foundry/PostgreSQL connectors. The worker passes credentials in memory to trusted
connector methods and does not launch arbitrary user code or provide a child-process sandbox. Credential
references are released at the end of the action/run, but the application does not promise secure
memory wiping. The explicit connection-test action is an additional, intentional web-process
decryption boundary.

Before connecting arbitrary run code, launch each run with an explicit minimal environment that
excludes the master-key ring, inject only the selected user's selected provider values, prohibit
secrets in command arguments and logs, and define process, filesystem, network, artifact, and
crash-dump isolation. Code intentionally granted a credential can still exfiltrate it, so authorization
and approval must happen before each grant. Protect, back up, rotate, and test recovery of every
production key separately from the database. Losing all copies of a referenced key makes its tokens
unrecoverable; compromising the application host or key ring defeats database encryption. The
all-zero development key is rejected in production. The exact deployed cryptographic module still
requires organization-specific FIPS and authorization evidence. Deleting the local record does not
revoke the credential at its provider; suspected disclosure requires provider-side revocation or
rotation. The JSON key-ring variable is itself a structured high-value secret and must never be logged;
masking or redaction is not a substitute for preventing disclosure.
[OWASP Cryptographic Storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html#key-storage)
warns that process environment variables may be exposed. Posit encrypts configured content variables
at rest and in memory before process startup as documented in its
[content settings](https://docs.posit.co/connect/user/content-settings/#environment-variables), but
the FastAPI process necessarily receives plaintext; an approved secret manager or protected
key-file/HSM boundary is preferable when available. The
application enforces an atomic per-key wrap ceiling but does not automatically rewrap existing
records. Monitor the counter, rotate well before the configured ceiling, and retain prior keys until
their records are rewrapped by an approved procedure or deleted.

**Evidence:**

- [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
  covers least privilege, lifecycle metadata, rotation, revocation, auditing, run-time delivery, and
  the fact that secrets must never enter logs.
- [NIST SP 800-38D](https://doi.org/10.6028/NIST.SP.800-38D) specifies GCM authenticated encryption,
  including IV uniqueness and authenticated additional data; [NIST SP 800-57 Part 1 Rev. 5](https://doi.org/10.6028/NIST.SP.800-57pt1r5)
  supplies the key-management lifecycle basis.
- The `cryptography` project's
  [authenticated-encryption documentation](https://cryptography.io/en/stable/hazmat/primitives/aead/)
  documents AES-GCM authenticated encryption and its nonce-uniqueness requirement.
- [PostgreSQL pgcrypto security limitations](https://www.postgresql.org/docs/current/pgcrypto.html#PGCRYPTO-NOTES)
  explain why cryptographic operations and key handling are kept in the application rather than the
  database server.

### SD-25 — Treat CSV pipeline sources as untrusted owner data

**Status:** Upload validation and owner scoping implemented; retention, database-at-rest protection,
and malware/content inspection are deployment controls or future work.

**Decision:** CSV is accepted only as a pipeline source for the authenticated owner. The server
requires a `.csv` filename, limits the body to 5 MB, rejects NUL/non-UTF-8 content, allows at most 200
unique non-empty headers, limits header and cell sizes, and requires consistent row widths. Parsing
uses Python's data-only CSV reader; inferred values are never evaluated as code. Hedron escapes
column names and examples when rendering the inspection result. A pipeline may reference an upload
only when both rows belong to the same authenticated user.

The database stores the original upload bytes, filename, content type, SHA-256 checksum, row count,
and inferred column profile. Application envelope encryption currently protects connection
credentials, not CSV content. Production deployments must therefore provide approved database and
backup encryption, access controls, retention/deletion procedures, malware or content inspection if
required by policy, and limits/monitoring appropriate to expected aggregate upload volume.

CSV inspection does not establish that a file is safe, authoritative, correctly classified, or
semantically suitable for a destination schema. In real mode, CSV bytes can be loaded into an enabled
destination by the trusted pipeline worker; they are not exported to spreadsheets. Any future
spreadsheet exporter must separately address formula injection, and every destination path must
address type conversion, transactional failure, and partial-write recovery.

**Evidence:** Owner predicates, bounded parsing, escaped output, no-store authenticated responses,
and metadata-only audit events follow the authorization, input-handling, and logging principles
already cited in this register. Tests cover invalid encoding/shape, size and header limits, inferred
types, storage metadata, saved-pipeline ownership, and cross-user rejection.

## Reference index

These are the primary or recognized industry sources actually consulted for this record:

1. [NIST SP 800-63-4, Digital Identity Guidelines](https://pages.nist.gov/800-63-4/sp800-63.html)
2. [NIST SP 800-63A-4, Identity Proofing and Enrollment](https://pages.nist.gov/800-63-4/sp800-63a.html)
3. [NIST SP 800-63B-4, Authentication and Authenticator Management](https://pages.nist.gov/800-63-4/sp800-63b.html)
4. [NIST SP 800-53 Rev. 5, current CSRC publication and Release 5.2.0 notice](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final)
5. [NIST SP 800-132, Password-Based Key Derivation](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-132.pdf)
6. [NIST Cryptographic Module Validation Program](https://csrc.nist.gov/Projects/cryptographic-module-validation-program/validated-modules)
7. [RFC 6750, OAuth 2.0 Bearer Token Usage](https://www.rfc-editor.org/rfc/rfc6750.html)
8. [RFC 8725, JSON Web Token Best Current Practices](https://www.rfc-editor.org/rfc/rfc8725.html)
9. [RFC 9106, Argon2](https://www.rfc-editor.org/rfc/rfc9106.html)
10. [RFC 9700, OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700.html)
11. [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
12. [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
13. [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
14. [OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
15. [OWASP HTTP Security Response Headers Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
16. [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
17. [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
18. [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
19. [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
20. [OWASP Transport Layer Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
21. [Posit Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/)
22. [Posit Workbench FastAPI proxy documentation](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html#fastapi)
23. [FastAPI Frontend documentation](https://fastapi.tiangolo.com/tutorial/frontend/)
24. [Hedron](https://github.com/eddiethedean/hedron)
25. [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
26. [Playwright Python, Browsers](https://playwright.dev/python/docs/browsers)
27. [Playwright Python, Continuous Integration](https://playwright.dev/python/docs/ci)
28. [RFC 7239, Forwarded HTTP Extension](https://www.rfc-editor.org/rfc/rfc7239.html)
29. [RFC 9110, HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
30. [OWASP Server-Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
31. [OWASP Web Security Testing Guide, Cookie Attributes](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/02-Testing_for_Cookies_Attributes)
32. [OWASP Web Security Testing Guide, Logout Functionality](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/06-Session_Management_Testing/06-Testing_for_Logout_Functionality)
33. [Playwright Python, BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext)
34. [NIST SP 800-38D, Galois/Counter Mode](https://csrc.nist.gov/pubs/sp/800/38/d/final)
35. [NIST SP 800-57 Part 1 Rev. 5, Key Management](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final)
36. [OWASP Cryptographic Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html)
37. [GitHub Actions, Secure Use](https://docs.github.com/en/actions/reference/security/secure-use)
38. [GitHub Actions Secrets REST API](https://docs.github.com/en/rest/actions/secrets)
39. [GitHub Actions, OpenID Connect](https://docs.github.com/en/actions/concepts/security/openid-connect)
40. [Posit Connect, Process Management](https://docs.posit.co/connect/admin/process-management/)
41. [Posit Connect, Automatic API-key Provisioning](https://docs.posit.co/connect/admin/content-management/api-keys/#automatic-provisioning)
42. [`cryptography`, Authenticated Encryption](https://cryptography.io/en/stable/hazmat/primitives/aead/)
43. [PostgreSQL `pgcrypto`, Security Limitations](https://www.postgresql.org/docs/current/pgcrypto.html#PGCRYPTO-NOTES)
44. [OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
45. [OWASP Content Security Policy Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
46. [HTMX, `hx-indicator` CSP configuration](https://htmx.org/attributes/hx-indicator/)
47. [Posit Connect, Reverse Proxy](https://docs.posit.co/connect/admin/proxy/)
48. [Posit Connect, Release Notes](https://docs.posit.co/connect/news/)
49. [Posit Workbench, Running with a Proxy](https://docs.posit.co/ide/server-pro/admin/access_and_security/running_with_a_proxy.html)
50. [Posit Connect, `InheritSystemEnvVars`](https://docs.posit.co/connect/admin/appendix/configuration/#inherit-system-env-vars)
51. [Posit Connect, Content Environment Variables](https://docs.posit.co/connect/user/content-settings/#environment-variables)
