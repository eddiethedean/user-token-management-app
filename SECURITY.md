# Security architecture and decision record

This document records the security decisions implemented by Access Registry, the evidence used to
make them, and the controls that must be supplied by the deployment environment. It is intended to
support design review and preparation of an organization-specific system security plan (SSP).

It is **not** an authorization to operate, a claim of FedRAMP or DoD compliance, or a substitute for
the system owner's risk assessment. Sources were opened and their relevant guidance was last verified
on 2026-08-03. Standards use their own normative terms (`SHALL`, `SHOULD`, and so on); an application
decision citing a standard does not by itself establish conformance with the whole standard.
[NIST SP 800-53 Rev. 5](https://doi.org/10.6028/NIST.SP.800-53r5) states that its guidelines do not
apply to national security systems without the express approval of the responsible policy officials.
The SIPR authorizing organization must select the applicable CNSS/DoD requirements and overlays;
NIST and OWASP references here support engineering reasoning but do not decide that authorization
boundary.

## Status vocabulary

- **Implemented** — present in the repository version reviewed for this record. Automated tests
  exercise the major controls, but test success is not proof that a control is effective in a
  production topology.
- **Deployment control** — must be enforced or verified by the platform, network, or operators.
- **Risk acceptance** — an intentional limitation that the authorizing organization must accept.
- **Gap** — required follow-up before representing the application as meeting the cited guidance.

## Assurance boundary

Access Registry is an administrator-approved, locally managed, password-authenticated application.
It proves that a claimant knows the password bound to an application account. Invitation acceptance
and self-registration verification also prove access to an approved email mailbox at that time.
Self-registered accounts remain inactive until an administrator approves them. These controls do
**not** prove that the mailbox holder is the real-world person they claim to be.

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
- [NIST SP 800-63B-4, Authentication Assurance Levels](https://pages.nist.gov/800-63-4/sp800-63b.html#sec2)
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

**Status:** Risk acceptance.

**Decision:** The application owns user records, password verifiers, recovery, sessions, and roles.
An administrator may invite a specific address. A user may also request registration, but must prove
control of the allowed government mailbox and cannot authenticate until an administrator explicitly
approves the pending account. Approval and denial are audited and communicated by email.

**Rationale:** This makes deployment independent of Posit viewer accounts and an unavailable OIDC
provider, but transfers credential lifecycle, incident response, recovery, and deprovisioning duties
to the application owner. It also caps assurance at the password-only boundary described above.

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
verification capability and choose a password only after opening it. The resulting account stays in
`pending` status until an administrator approves it; pending accounts cannot obtain JWTs or browser
sessions. A newer invitation revokes older outstanding invitations for the same address.
When `DIRECTORY_LOOKUP_URL` is configured, both enrollment paths also query a bounded-time,
non-redirecting HTTPS directory endpoint and require the returned record's normalized email to match
exactly. A private CA bundle and bearer credential are supported. `DIRECTORY_LOOKUP_REQUIRED`
selects fail-closed or fail-open behavior for service failure; explicit not-found and email mismatch
always reject enrollment.

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

**Status:** Partly implemented; blocklist is a gap.

**Decision:** Passwords are 15–128 Unicode code points. Account creation and password-change paths
normalize them with NFC before hashing. The validator imposes no upper/lower/digit/symbol composition
rules and no periodic password expiration. Passwords matching a small local list or containing the
email local part are rejected. Forms use standard password-manager autocomplete values and do not
disable paste.

**Rationale:** Fifteen characters is NIST's minimum for a single-factor password. NIST recommends a
maximum of at least 64, NFC normalization, no composition rules, no periodic changes absent evidence
of compromise, password-manager support, and comparison with known common or compromised values.

**Gap:** the six-entry `COMMON_PASSWORDS` set is deliberately only a development safeguard and is
not a sufficient common/compromised-password blocklist. Before a production claim of SP 800-63B-4
alignment, integrate an organization-approved offline blocklist, version and update it, test it with
Unicode-normalized complete passwords, and do not send candidate passwords to an Internet service
from NIPR or SIPR. The current “email local part appears anywhere” test is broader than NIST's
whole-password comparison and should be replaced with reviewed context-specific blocklist entries and
derivatives. Login verification also needs to apply the same NFC normalization as enrollment. The UI
should offer a password-visibility control and explicit guidance after blocklist rejection.

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
unverified account, and active lockout. A dummy Argon2 operation is performed when no verifier exists.
Five consecutive failures lock the account for 15 minutes; successful authentication clears the
counter. Registration, login, and reset paths additionally increment atomic fixed-window buckets for
both source address and normalized account email in the shared application database. Bucket keys are
HMAC-SHA-256 digests under the session pepper, expired buckets are deleted, denials return
`Retry-After`, and denials are audited without recording the raw bucket key. Authentication outcomes
are audited.

**Rationale:** Generic responses and comparable expensive work reduce account enumeration. A
per-account limit constrains online password guessing; five is intentionally below NIST's maximum of
100 consecutive attempts.

**Gap:** fixed per-account lockout can be abused for denial of service and neither lockout nor fixed
windows are a complete defense against distributed password spraying. PostgreSQL shares application
counters across Connect workers, but the trusted ingress must still impose volumetric source limits
before requests consume application/database resources and should add progressive delay, device/risk
signals, and alerting. Boundary bursts are possible, and `RATE_LIMIT_ENABLED=false` must not be used
without an approved replacement. Test response-time distributions; a dummy hash does not prove
timing uniformity.

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

**Status:** Implemented with an important replay-detection gap.

**Decision:** Refresh tokens are 32-byte cryptographically random opaque capabilities. Only an
HMAC-SHA-256 digest made with a separate session pepper is stored in `refresh_sessions`. Every
successful refresh replaces the token and extends only the idle deadline, never the absolute
deadline. Invalid refresh attempts clear browser cookies.

**Rationale:** Opaque server-side state supports revocation, avoids putting account data in the
refresh token, and limits the usefulness of a database-only token-table disclosure. Rotation makes a
previous value unusable after a successful refresh.

**Gap:** the implementation replaces the stored digest and does not retain token-family history.
Consequently, reuse of an old token is rejected but cannot be recognized as evidence of theft and
cannot revoke the still-active replacement. Add token-family/replacement records and revoke the
family when reuse is detected before claiming full refresh-token replay detection.

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
used.

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

**Status:** Implemented, with login CSRF as a gap.

**Decision:** Each database session has an independent random CSRF value. All authenticated
state-changing browser and cookie-authenticated API requests must return it in a form field or
`X-CSRF-Token` header; comparison is constant-time. `SameSite=Lax` is defense in depth, not the sole
control. Authorization-header bearer requests are exempt because browsers do not attach that header
automatically cross-site.

**Rationale:** Cookies are automatically sent by the browser, so authentication alone cannot
distinguish a forged cross-site request. A server-held synchronizer value supplies that distinction.

**Gap:** the unauthenticated login form has no pre-authentication CSRF mechanism. Assess forced-login
risk and add an origin check, Fetch Metadata policy, or pre-authentication token before production.
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

### SD-12 — Separate browser-cookie and API-bearer behavior

**Status:** Implemented.

**Decision:** `/api/v1/auth/token` returns an access JWT in JSON for API clients and also establishes
the browser cookie session. Subsequent API requests may use `Authorization: Bearer`. If a bearer token
is present it is authoritative; an invalid bearer request does not silently fall back to a valid
cookie. Authenticated business mutations using cookies require CSRF; bearer-authenticated mutations
do not.

**Rationale:** Explicit precedence avoids credential confusion and makes the CSRF rule depend on the
credential actually used. Both modes still require the same live database session and authorization
checks. The refresh endpoint and automatic refresh during normal navigation are session-maintenance
exceptions: they rotate a cookie credential without a CSRF token and rely on `SameSite=Lax`, the
same-origin response boundary, and the attacker's inability to read the new token. Review this
exception if cross-origin clients or cookie policy change.

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

- Raw URLs necessarily appear in the email body and can therefore exist in `email_outbox.body_text`,
  SMTP systems, mailbox storage, browser history, and proxy request logs. The README statement that
  all such tokens are “stored only as hashes” is intentionally narrowed: it is true only of the
  registration, invitation, reset, and session token columns.
- Restrict database/outbox access, encrypt storage and backups as required, define the shortest
  workable outbox retention, and purge or redact bodies after delivery. Configure every proxy and
  log collector to redact query parameter `token`; never put tokens in audit details.
- `Referrer-Policy: no-referrer` limits browser referrer leakage, but it cannot sanitize upstream
  access logs or mail systems.
- Add per-account and per-source reset-request throttling and verify uniform response timing.

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

**Status:** Implemented, with cache control as a gap.

**Decision:** Every application response receives `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`, and a
Content Security Policy limited to same-origin scripts, styles, fonts, and connections, with
`frame-ancestors 'none'`, `base-uri 'self'`, and `form-action 'self'`. Production adds one-year HSTS
with `includeSubDomains`. HTMX and CSS are vendored and served through `app.frontend()` with no SPA
fallback and no runtime CDN or Node dependency.

**Rationale:** These headers reduce script injection impact, clickjacking, MIME confusion, referrer
leakage, browser feature exposure, and transport downgrade. Self-hosting enables `script-src 'self'`
and makes production independent of an external CDN.

**Gap:** authenticated pages and token-bearing form responses do not currently set
`Cache-Control: no-store`. Add it to sensitive responses, then test behavior through Connect and
Workbench proxies. HSTS `includeSubDomains` must be reviewed with the owner of the hostname before
production because it affects descendant hosts.

**Evidence:**

- [OWASP HTTP Security Response Headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
  documents the selected header defenses.
- [OWASP Content Security Policy](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
  describes CSP as defense in depth for XSS and data injection.
- [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
  recommends `Cache-Control: no-store` for sensitive responses and HSTS for HTTPS applications.
- [FastAPI Frontend](https://fastapi.tiangolo.com/tutorial/frontend/) confirms that
  `app.frontend()` serves existing static files, runs through normal middleware, and can disable
  fallback.

### SD-17 — Require TLS and production-safe startup configuration

**Status:** Implemented fail-fast checks; TLS itself is a deployment control.

**Decision:** Production configuration refuses to start with insecure cookies, missing domain
allowlists, weak placeholder application secrets, or a missing SMTP host when SMTP is selected.
Interactive API documentation is disabled in production. TLS is expected to terminate at the
approved Posit/reverse-proxy boundary; HSTS and Secure cookies are applied by the application.

**Rationale:** Failing startup is safer than silently deploying known development defaults. TLS is
required for password and bearer-token confidentiality, integrity, and server authentication.

**Deployment control:** verify the full client-to-proxy and proxy-to-application path, approved TLS
versions/ciphers/certificates, HTTP-to-HTTPS behavior, and that the application cannot be reached
directly around the proxy. Production must never use the console mail backend because it prints raw
capability URLs. `PUBLIC_BASE_URL` is not currently restricted to HTTPS by application validation;
operators must verify that it is the exact approved external HTTPS URL before sending invitations or
resets.

**Evidence:** [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
requires TLS for login and all authenticated pages, Secure cookies, no mixed transport, and protected
proxy links. [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#password-verifiers)
requires an authenticated protected channel when collecting passwords.

### SD-18 — Keep application secrets out of source and separate by enclave

**Status:** Deployment control, with one configuration cleanup gap.

**Decision:** JWT signing, session-token hashing, SMTP, and database secrets come from deployment
configuration, not committed values. Production requires long non-placeholder JWT and session
secrets. NIPR and SIPR must use different secrets, databases, SMTP relays, backups, accounts, and
deployment pipelines; no application feature moves data across the boundary.

**Rationale:** Independent secrets constrain compromise and prevent tokens or data from one enclave
being accepted in another. A managed secret lifecycle provides access control, audit, rotation, and
incident response that environment files alone cannot.

**Gap:** `CSRF_SECRET` is currently validated but not used; CSRF uses random server-stored session
values and needs no signing key. Do not list `CSRF_SECRET` as an active control. Remove the dead
setting, or implement and document a justified cryptographic use, to avoid misleading operators.

**Deployment control:** use an approved secret manager or protected Connect configuration, generate
at least 256 random bits for JWT and session secrets, restrict human and service access, prohibit
logging, and document rotation. JWT-secret rotation invalidates access tokens; session-pepper
rotation invalidates all refresh, registration-verification, invitation, and reset capabilities and
changes rate-limit bucket identifiers, and therefore needs a planned user-impact procedure.

**Evidence:** [RFC 8725 section 3.5](https://www.rfc-editor.org/rfc/rfc8725.html#section-3.5)
requires sufficient JWT-key entropy. [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
documents least-privilege access, automated rotation, auditing, revocation, and no logging of secrets.

### SD-19 — Treat SMTP and the email outbox as security-sensitive systems

**Status:** Deployment control and gap.

**Decision:** The mailer supports a development console backend and an SMTP backend with STARTTLS
enabled by default and optional relay authentication. Messages are queued transactionally in the
database, retried up to five times, and retain delivery outcome. The application sends
  registration/invitation/reset URLs and account-status or password-change notifications, never
  passwords.

**Rationale:** Transactional queuing avoids losing a message when the surrounding database operation
commits. Change notifications give users an independent signal of possible compromise.

**Gap:** production currently does not forbid the console backend, require SMTP, require STARTTLS,
or enforce an approved relay certificate policy. The current schema also retains raw message bodies
after delivery and defines no purge schedule; those bodies contain active or expired capability URLs.

**Deployment control:** require an approved enclave-local relay and protected route, verify TLS and
certificate behavior, prohibit console email in production, restrict outbox and backup readers,
define retry monitoring, and implement body redaction/purge after the operationally required period.
Mail administrators must define equivalent retention and access controls downstream.

**Evidence:** [OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
requires secure reset-token handling and post-change notification. [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)
describes TLS confidentiality, integrity, and authentication. [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#data-to-exclude)
supports excluding or specially protecting token and session values.

### SD-20 — Trust proxy routing data only at an isolated ingress

**Status:** Implemented parsing; trust enforcement is a deployment control.

**Decision:** The application derives its external mount path from Posit Connect's
`RStudio-Connect-App-Base-URL` or the ASGI `root_path` used by Workbench. Values are reduced to a
same-origin absolute path and values containing protocol-relative paths, backslashes, query strings,
fragments, or control characters are rejected. The result prefixes application links and scopes
cookies. A narrow middleware also handles duplicate ASGI root paths, Workbench
`/proxy/<port>/...` mounts, and encoded absolute-URL paths. Source IP accepts the first
`X-Forwarded-For` value only when the direct peer is in the explicit `TRUSTED_PROXY_IPS` allowlist;
malformed values are ignored.

**Rationale:** Connect applications do not know their external base URL ahead of time, while
Workbench FastAPI applications run behind a dynamic ASGI root path. Runtime resolution allows one
codebase without unsafe cross-origin redirects.

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

**Decision:** Three ordered Alembic revisions create the baseline, self-registration, and shared
rate-limit schemas. Application startup verifies the current revision and refuses to serve a stale or
unversioned schema; `python -m app migrate` is an explicit release action. Legacy `create_all()`
databases require the explicit `--adopt-existing` path, which verifies known table/column shapes
before stamping and upgrading. Administrator bootstrap is a separate `create-admin` command and is
never migration data. Production is expected to use the PostgreSQL optional dependency and an
approved managed or operated database, backup, migration, encryption, access-control, monitoring,
and recovery process.

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

**Decision:** FastAPI executes authentication and authorization, Jinja renders HTML, and HTMX
progressively enhances same-origin forms and partial updates. The browser never determines access
rights. HTMX is vendored; Node is not a runtime or deployment dependency. Static files are served
under `/assets` with fallback disabled, after normal application routes, and receive the same
security middleware. The reviewed artifact identifies itself as HTMX 2.0.10 and has SHA-256
`71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de`.

**Rationale:** This architecture fits Workbench's no-Node environment and keeps the security boundary
on the Python server. A small, self-hosted script surface supports a restrictive CSP and removes a
runtime CDN dependency. It does not make XSS impossible; output encoding, dependency review, CSP,
and server authorization remain required.

**Deployment control:** record the HTMX file version and integrity hash, review upgrades, scan and pin
Python dependencies through the approved supply-chain process, and never render untrusted values with
Jinja's `safe` escape bypass without a security review.

**Gap:** compatible dependency ranges are declared, but the repository has no committed lockfile,
software bill of materials, or recorded HTMX acquisition/provenance evidence. Supply-chain review is
therefore a production gate, not an implemented control.

**Evidence:** [FastAPI Frontend](https://fastapi.tiangolo.com/tutorial/frontend/) documents route
precedence, middleware application, static-output serving, and disabled fallback. [Jinja Autoescaping](https://jinja.palletsprojects.com/en/stable/api/#autoescaping)
documents HTML autoescape configuration. [OWASP CSP](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
describes CSP as an additional layer rather than a replacement for secure output handling.

### SD-23 — Test browser security behavior through both proxy path models

**Status:** Implemented harness; production-equivalent execution remains a deployment control.

**Decision:** The Playwright suite starts the actual Uvicorn service behind two local reverse
proxies: one preserves the external prefix in the upstream request and one strips it while supplying
the external Connect base. A real Chromium context exercises login, HTMX form submission, static
assets, cookie attributes and path scope, access-token loss followed by refresh, logout cookie
deletion, and denial of authenticated access after logout. Unit tests separately cover Workbench
path anomalies that are awkward to generate through an ordinary HTTP proxy.

**Rationale:** `TestClient` is valuable for deterministic route and header coverage but does not
apply a browser's cookie-selection rules, execute HTMX, or reproduce a reverse proxy's path
transformation. The two layers catch different failure classes. Testing both simulated proxy models
guards the one-source deployment design, but neither simulation proves the exact installed Connect,
Workbench, ingress, TLS, enterprise-browser, and PostgreSQL combination.

**Deployment control:** run the suite in the approved pipeline with pinned, internally obtained
browser artifacts. Before authorization and after proxy/platform changes, repeat security tests at
the real external URLs using supported enterprise browsers, production-equivalent headers and TLS,
and non-production accounts/data. Preserve results with the reviewed release. Do not treat this
functional suite as penetration testing.

**Evidence:**

- NIST SP 800-53 Rev. 5 control SA-11 in the
  [current CSRC publication](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final) calls for developer
  testing and evaluation; the organization must select the applicable rigor and evidence.
- [OWASP WSTG cookie-attribute testing](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/02-Testing_for_Cookies_Attributes)
  provides browser-observable checks for cookie scope, lifetime, `Secure`, `HttpOnly`, and `SameSite`.
- [OWASP WSTG logout testing](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/06-Session_Management_Testing/06-Testing_for_Logout_Functionality)
  calls for verifying browser cookie behavior, server-side invalidation, and loss of access to
  authenticated pages after logout.
- [Playwright browser-context documentation](https://playwright.dev/python/docs/api/class-browsercontext)
  confirms that the harness can observe the browser's effective cookie domain, path, expiry,
  `HttpOnly`, `Secure`, and `SameSite` properties.
- Posit's [Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/) and
  [Workbench FastAPI proxy documentation](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html#fastapi)
  establish that the two platforms expose different runtime base-path signals that require testing.

## Production security gate

Do not represent a deployment as production-ready until the system owner records evidence for every
item below. Items marked **code gap** require application work; the remainder require deployment or
authorization evidence.

- [ ] The privacy and security officials have determined whether making the application's profile,
      account activity, and session metadata available invokes the federal AAL2 minimum. If it does,
      an approved phishing-resistant CAC/MFA path has been added; password-only mode is not approved.
- [ ] For any remaining password-only use, the identity and authenticator risk assessment explicitly
      accepts email-verified access for the information and functions in scope.
- [ ] The NIPR and SIPR authorization boundaries, data flows, administrators, secrets, databases,
      SMTP relays, logs, backups, and pipelines are separate and documented.
- [ ] `APP_ENV=production`, `COOKIE_SECURE=true`, the stable `PUBLIC_BASE_URL`, exact
      `ALLOWED_EMAIL_DOMAINS`, and approved issuer/audience values are set.
- [ ] If directory validation is enabled, the source's authority, attribute currency, privacy use,
      exact URL/response contract, CA trust, bearer-secret handling, fail-open/fail-closed policy,
      DNS behavior, and network egress allowlist are approved and monitored. It is not represented as
      authentication, CAC validation, identity proofing, clearance, or authorization.
- [ ] High-entropy, enclave-unique JWT and session secrets are injected from an approved store;
      access, audit, incident, and rotation procedures are recorded.
- [ ] **Code gap:** replace the six-entry password list and substring rule with an approved
      common/compromised/contextual-password blocklist, normalize password input consistently during
      verification, and add the related user guidance and visibility control.
- [ ] The selected Argon2 parameters are recorded and benchmarked, or the exact FIPS-validated
      PBKDF2 module/configuration/operational environment is evidenced.
- [ ] TLS is approved end to end; HTTP is unavailable or redirected appropriately; Secure cookies,
      HSTS, and certificates have been tested at the external URL.
- [ ] Direct app-server access is blocked and the trusted proxy overwrites security-relevant
      forwarding headers; every immediate proxy address is explicitly in `TRUSTED_PROXY_IPS`, and
      `X-Forwarded-For` is not used for authorization.
- [ ] Cookie set, refresh, deletion, path isolation, SameSite, and expiry have been tested through
      both Connect and Workbench routes.
- [ ] Database-backed source/account throttling is enabled and tested on production PostgreSQL;
      ingress volumetric throttling, progressive delay/device-risk controls, enumeration timing
      tests, alerting, and login-CSRF treatment are approved.
- [ ] **Code gap:** retain refresh-token family history and revoke the active family on reuse before
      claiming replay detection.
- [ ] **Code gap:** apply `Cache-Control: no-store` to authenticated and token-bearing responses.
- [ ] The SMTP relay requires approved transport protection; console email is disabled; delivery
      failure is monitored.
- [ ] **Code gap:** redact or purge delivered outbox bodies; proxy, application, SMTP, and SIEM logs
      redact registration/invitation/reset query tokens.
- [ ] PostgreSQL security, Alembic migration/adoption rehearsal, backup encryption, restore testing,
      retention, availability, and least-privilege credentials are approved. The release procedure
      migrates before application startup and never uses a migration to bootstrap an administrator.
- [ ] Audit events flow to protected centralized storage with time synchronization, retention,
      monitoring, alerts, access review, and incident-response integration.
- [ ] Administrator lifecycle, dual-control/break-glass needs, periodic access review, and prompt
      deprovisioning are documented.
- [ ] Dependency locking, vulnerability scanning, vendored HTMX provenance/hash, patching, and
      release approval are part of the deployment pipeline.
- [ ] The Playwright preserve/strip proxy suite and unit edge-case tests pass; security tests also
      pass in the exact production-equivalent proxy and database topology; penetration testing and
      authorization review are complete at the required impact level.

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
24. [Jinja API, Autoescaping](https://jinja.palletsprojects.com/en/stable/api/#autoescaping)
25. [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
26. [Playwright Python, Browsers](https://playwright.dev/python/docs/browsers)
27. [Playwright Python, Continuous Integration](https://playwright.dev/python/docs/ci)
28. [RFC 7239, Forwarded HTTP Extension](https://www.rfc-editor.org/rfc/rfc7239.html)
29. [RFC 9110, HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
30. [OWASP Server-Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
31. [OWASP Web Security Testing Guide, Cookie Attributes](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/02-Testing_for_Cookies_Attributes)
32. [OWASP Web Security Testing Guide, Logout Functionality](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/06-Session_Management_Testing/06-Testing_for_Logout_Functionality)
33. [Playwright Python, BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext)
