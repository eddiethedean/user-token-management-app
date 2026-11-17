# Administrator promotion

**[Epic CDO-5096](../stories/administrator-promotion-epic.md) · [Story CDO-5097](../stories/administrator-promotion-promote-existing-user.md) · Implemented and verified locally.** Initial plan dated November 17, 2026.

[Issue CSV](../../artifacts/jira/data-mover-administrator-promotion-issues.csv): one epic, one
story, and 12 tasks totaling 72 estimated person-hours. The export uses `AP-EPIC` for CDO-5096,
`AP-1` for CDO-5097, and `AP-1-T01` through `AP-1-T12` for the tasks.
Only task rows carry hours. Estimates include implementation, focused verification, normal
review fixes, and the additional acceptance work named in the tasks. They assume familiarity
with this repository and exclude contingency, production deployment, and external approval waiting.

## Goal and scope

An administrator can promote an existing active user from **Team** without using the CLI or
creating another invitation. Use the existing role name `administrator` and display label
**Administrator**.

The first release adds a **Make administrator** action. It adds the administrator role while
preserving existing role memberships, matching the current CLI's membership behavior. It does
not introduce a role editor, demotion, bulk changes, custom roles, or changes to registration
approval. Demotion would need its own self-demotion and last-active-administrator rules.

## Existing building blocks

| Area | Current behavior | Planned reuse |
|---|---|---|
| `app/models.py` | `Role`, `User.roles`, and the unique `user_roles` membership key already exist | Add a membership; no schema migration expected |
| `app/ui/partials/admin_users.py` | Directory shows roles and account actions with confirmation dialogs | Add a promotion action and confirmation dialog |
| `app/ui/routes/admin.py` | Administrator actions enforce authentication and CSRF; preserve listing state and return HTMX fragments or redirects | Follow the same transport and response conventions |
| `app/services/auth_common.py` | `lock_administrator_action` locks the administrator role and rechecks the actor | Reuse administrator revalidation inside the mutation transaction |
| `app/services/sessions.py` | Session creation coordinates through a user write lock; sessions can be revoked together | Coordinate promotion with session issuance and invalidate old sessions |
| `app/services/audit.py` | `record_event` stores actor, target, request ID, source IP, and detail | Record the role change in the same transaction |
| `app/cli.py` | `create-admin` can promote existing accounts as part of bootstrap and recovery | Use the same administrator-role lock before the CLI reads or updates an account |

## User experience

1. An administrator opens **Team** and finds an active, verified account without the
   `administrator` role. The account's actions include **Make administrator** if it also meets
   the sign-in requirements for the configured authentication mode.
2. Selecting the action opens a Hedron dialog titled **Make administrator**. It names the
   account by email and explains: “This account will be able to manage users, invitations,
   and application-wide audit records. Their current sessions will end, and they must sign
   in again.” The dialog provides **Cancel** and **Make administrator** controls.
3. Cancel closes the dialog without submitting. Confirmation posts the existing CSRF proof
   and directory query, status filter, and page. Prevent duplicate submissions while pending.
4. Success refreshes the directory row/table, shows the new role, removes the promotion
   action, and announces: “The account is now an administrator. They must sign in again.”
   Keep the current search, filter, and page, following existing pagination normalization.
5. Pending, disabled, unverified, and existing administrator accounts have no promotion
   control. The server independently enforces eligibility for stale pages or direct requests.
6. A known promotion failure keeps the dialog open and displays persistent feedback inside it.
   Focus moves to the error summary, and the submit control becomes available again when retry
   is appropriate. The administrator can always cancel without submitting again.

Reuse the existing mounted-path helpers and fragment regions. Verify dialog keyboard focus,
cancel/close behavior, screen-reader labels, and narrow-screen action layout. Ordinary form
submissions follow existing non-HTMX redirect/error behavior.

Use one shared promotion dialog per directory table, with a fixed
`#promotion-dialog-feedback` region inside it. Each row's opener supplies the server-rendered
account label and mounted promotion URL; opening the dialog initializes the selected account,
listing state, and form action and clears the previous account's feedback. Register the feedback
region in `app/ui/regions.py`, the application interaction policy, and the action's declared
regions. Updating feedback must preserve the open dialog, its form, and keyboard focus handling.

Expected HTMX failures retain their 4xx/5xx status and explicitly retarget that region with an
error response policy that permits the swap. Use a focusable error summary with an appropriate
live announcement and include the request reference for unexpected failures. Do not rely on
the default toast retarget: `app/static/app.js` currently closes dialogs only on successful
requests, so page-level feedback alone can remain behind the modal. For dependency/global
errors that still use page-level feedback, close the promotion dialog and focus that feedback;
retain the existing authentication redirect for expired sessions. Network failures render a
local in-dialog message explaining that the outcome is unknown and the directory should be
refreshed before retrying. Do not automatically repeat the promotion. Successful responses
close/remove the dialog and restore focus to the updated account row or directory heading.

## Server and transaction contract

Add `POST /admin/users/{user_id}/promote`, guarded by `AdminAuth`, `RequireCsrf`, and
`UserIdPath`. The server fixes the assigned role to `administrator`; the form cannot choose
another role or alter account status.

Put the operation in a focused `app/services/user_roles.py` service, called by the route.
The service owns one transaction for the membership, session invalidation, and audit event:

1. Establish database-appropriate write serialization and revalidate the acting administrator.
   Extract the administrator role lock from `lock_administrator_action` so promotion can distinguish
   an unavailable role from a forbidden actor. Keep the existing helper as a wrapper for
   status and registration actions.
   - On PostgreSQL, lock the administrator role row first. Then lock the distinct actor and
     target user rows in user-ID order with **`FOR NO KEY UPDATE`**
     (`with_for_update(key_share=True)` without `read=True` in SQLAlchemy). Reload their state
     and memberships, and recheck actor eligibility after acquiring those user locks. Retain
     the administrator-role-first order used by existing administrator mutations.
   - Do not use `FOR UPDATE` for the promotion's user locks. Refresh rotation updates a
     `RefreshSession` first and later inserts an audit event whose user foreign keys require
     `KEY SHARE` locks. A promotion holding `FOR UPDATE` on that user while waiting to revoke
     the session creates a cycle. `FOR NO KEY UPDATE` still excludes competing user updates
     and session-creation user locks, while allowing refresh's foreign-key checks to finish.
     Promotion does not change user IDs or other referenced keys, so its later writes must
     retain that compatible lock strength.
   - On SQLite, make the shared role-lock operation a conditional no-op write on the
     administrator role row, such as setting its name to its existing name, before reloading
     either account. Reuse it for status and registration actions so reads that drive competing
     mutations also happen under the write lock. Use the existing request session and avoid a
     second transaction nested inside the one opened by authentication. A read snapshot that
     cannot upgrade to a write returns a conflict after rollback.
   Reject an actor who is no longer active or an administrator. Keep locks until commit or
   rollback, and roll back lock/snapshot conflicts before returning a retryable response.
2. Reload the target and memberships under that transaction. Require active status and a
   verified email. In local-password mode also require an existing password hash; trusted-header
   accounts do not require a local password. Never enable, approve, verify, unlock, or reset
   credentials as a side effect of promotion.
3. If the eligible target already has the role, return an unchanged result. Repeated requests,
   including a direct request for the acting administrator, must not revoke sessions,
   increment the security version, or create another role-change event.
4. Add the existing `administrator` membership while retaining every existing membership.
   Increment `security_version` once and call `revoke_all_sessions` for the target. Coordinate
   with session creation through the target user lock. Refresh uses its existing session-row
   lock: a refresh that wins finishes before revocation, and a refresh that loses observes a
   revoked session. Any target-account token returned by an overlapping refresh must be
   unusable after promotion commits. Do not acquire a stronger user lock while waiting for
   session rows.
5. Record `admin.user.roles_changed`, with actor and target, and detail containing sorted
   `before_roles` and `after_roles`. Reuse the audit helper's request metadata. Commit all
   effects together and roll back confirmed pre-commit failures. If the connection drops
   during commit and the result is unknown, ask the administrator to refresh the directory
   before retrying.

After commit, old access tokens fail the security-version/session checks and old refresh
tokens cannot restore a session. A fresh local or trusted-header sign-in grants administrator
access. A request that already passed administrator authorization may complete, and running
pipeline jobs continue under their existing execution rules.

| Outcome | HTTP/response contract |
|---|---|
| Promoted | HTMX 200 with updated `#user-directory-body`, match-count OOB fragment, and success toast; ordinary POST 303 with `notice=role-updated` |
| Already administrator | Same successful response shape, with “This account is already an administrator”; ordinary POST uses `notice=already-administrator` |
| Anonymous / non-administrator / invalid CSRF | Existing authentication, administrator, and CSRF rejection behavior; no mutation |
| Missing / malformed target ID | 404 / 422, matching current user actions |
| Target no longer eligible | 409 with persistent in-dialog feedback explaining the eligibility requirement; disable promotion until the directory is refreshed; no mutation |
| Administrator role unavailable after the route's initial authorization | 503 with safe in-dialog feedback; refresh the directory before submitting again |
| Concurrent write conflict | Roll back and return 409 with in-dialog retry guidance; no partial success |
| Unexpected failure | Close the dialog and focus existing global feedback with the request reference. Guide the administrator to refresh before retrying if the commit or response outcome is unknown |

Only successful membership changes produce `admin.user.roles_changed`. Audit failure must fail
the mutation. The actor's sessions remain valid when promoting another user. No email delivery
is required for this first release.

## Implementation sequence

The issue CSV remains the source for individual estimates and dependencies. These groups describe
the order in which each part can be verified. Start the database race tests (T08 and T09) after
T03, and start the authorization integration tests (T07) after T04. Mounted-path testing (T10)
follows the dialog work, and browser acceptance (T11) follows T10. T12 uses the evidence from
all three test tracks.

| Group | Existing task IDs | Hours | Evidence to proceed |
|---|---|---:|---|
| Role service and persistence | AP-1-T01–T03 | 20 | Service tests prove eligibility, repeat-request behavior, one atomic write, and rollback |
| HTTP action and directory dialog | AP-1-T04–T06 | 20 | The protected action works through Team and expected errors stay visible in the open dialog |
| Authorization and database integration | AP-1-T07–T09 | 18 | Fresh sign-in and session invalidation work in both auth modes. Real PostgreSQL and file-backed SQLite pass the race cases |
| Mounted routes and browser acceptance | AP-1-T10–T11 | 10 | HTTP tests preserve directory state across mounts. Browser evidence covers focus, recovery, and keyboard use |
| Guide and release verification | AP-1-T12 | 4 | User guide and evidence are recorded and applicable repository checks pass |

### Service and transaction boundaries (AP-1-T01–T03)

- Add `app/services/user_roles.py`. Define an eligibility predicate shared by the table and the
  service and a `promote_user` operation receiving the request session, actor and target IDs,
  authentication mode, and audit request metadata. Keep the status/verification/credential
  predicate separate from administrator membership so an eligible repeat request is an explicit
  `already_administrator` result. Use typed rejection codes for the HTTP mapping.
- Use the request's SQLAlchemy session for the write and make the service responsible for
  finishing it. Load the actor and target with `populate_existing` and refresh their role
  collections after lock acquisition. `AdminAuth` may already have populated the actor and
  `SessionLocal` uses `expire_on_commit=False`. Materialize result data before ending the
  transaction and release locks on unchanged and rejected paths as well. Existing admin status
  mutations should use their freshly read target after acquiring the shared administrator lock.
- Add the role through the loaded relationship, bump the version, revoke sessions with the
  existing helper, and record the audit event. Commit once and report success only after commit
  returns. Classify known aborted serialization/lock errors by driver code, including PostgreSQL
  deadlock/serialization failures and SQLite busy/snapshot conflicts. Keep programming errors,
  unrelated constraint errors, and ambiguous disconnects on the normal unexpected-error path.
  Test the stored membership, audit fields, and session state from a new database session.

### Route, fragments, and browser state (AP-1-T04–T06)

- In `app/dependencies.py`, have `require_admin` verify current administrator membership and
  the authenticated session in one database read. Match the session ID and user ID, require
  active status, valid session expiry and revocation state, and the security version observed
  during authentication. Capture that version in the auth context at credential acceptance.
  Currently, `get_optional_auth` validates the session before `require_admin` may lazy-load
  roles in a separate read. Across a promotion commit, an older request can otherwise combine
  its earlier session check with the newly assigned role. Keep existing auth error semantics.
- Add the action in `app/ui/routes/admin.py` with `AdminAuth`, `RequireCsrf`, `SettingsDep`,
  and the existing path and listing form types. Accept only listing state and CSRF aside from the
  path ID. Build both result notices from the service outcome. Reload the directory only after
  the service ends its transaction. Use normalized `user_listing_values` for the table and
  ordinary redirect. Generate the 303 `Location` through `redirect_path`, which supports
  Workbench's legacy `/proxy/<port>/` and `/s/.../p/...` entry points. Form and HTMX action URLs
  use the appropriate mounted `app/ui/urls.py` helpers. Keep `/admin/users` as the post-login
  destination for expired promotion POSTs so login never redirects to a POST-only URL.
- Add `PROMOTION_DIALOG_FEEDBACK` in `app/ui/regions.py`, `APP_REGIONS` in
  `app/ui/interactions.py`, and the promotion action's declarations. Keep explicit retargeting
  to the feedback region for service errors. The shell already allows 4xx and 5xx HTMX swaps,
  so verify the returned `HX-Retarget` and inner swap with the actual response renderer. Add a
  stable error code and explicit retry permission to the feedback markup. The stale target and
  missing-role outcomes require a directory refresh. A confirmed rolled-back write conflict
  permits a manual retry. Retain standard response handling for plain forms and dependency errors.
- Pass the current authentication mode through all directory render paths, including the page,
  filters, pagination, and existing user mutations. Render one shared dialog alongside the table
  inside `#user-directory-body` and place the feedback region inside the dialog. Each opener carries
  an escaped account label and a server-generated action URL. Set the label with `textContent`.
  Initialize native `action` and the HTMX POST path together before enabling confirmation. Verify
  the bundled HTMX behavior when setting the path dynamically, including processing the form after
  changing its HTMX attributes if needed. With the dialog initialized, the same form supports
  ordinary POST if HTMX is unavailable.
- In `app/static/app.js`, scope request lifecycle handling to the promotion form. Track the
  selected account and request through the swap. When a confirmation is pending, prevent a
  second submit or selection of a different account. Dismissing the dialog leaves the submitted
  operation in progress. Opening the dialog discards earlier directory responses that could
  replace the form. Focus the current error or replacement directory row after the relevant
  swap settles. The promotion request has a 45-second browser deadline. A timeout or dropped
  response asks the administrator to refresh **Team** and check the account's role.
  Invalidate cached script URLs through `UI_SCRIPT_VERSION` in `app/ui/layout.py` and update
  the shell's asset expectation in `tests/test_ui_shell.py`.

### Verification and release evidence (AP-1-T07–T12)

| Task | Test surface | Evidence |
|---|---|---|
| AP-1-T07 | `tests/test_auth_security.py` and `tests/test_web_flows.py` | Use separate actor and target clients. Assert authorization and CSRF rejection, idempotency, persisted audit, unusable target cookie/bearer/refresh credentials, and fresh sign-in in both auth modes. Pause a target request between session acceptance and administrator authorization, commit promotion, and assert that the older request is denied |
| AP-1-T08 | New PostgreSQL promotion tests using the `postgresql` fixture | Use separate connections and bounded barriers around real SQL. Exercise refresh/audit versus revocation in both orders, actor refresh, session issuance with a previously read version, parallel promotion, and the existing status mutation. Assert transaction completion, final roles and versions, events, and credential validity |
| AP-1-T09 | New file-backed SQLite promotion tests | Reuse the application session configuration with independent connections. Hold competing writes long enough to verify rollback and retry classification with a bounded timeout. Exercise duplicate promotion and disable races through the shared lock |
| AP-1-T10 | `tests/test_ui_interactions.py`, `tests/test_web_flows.py`, and mounted route regressions | Assert the action's registered regions, HTTP statuses, redirects, and promotion eligibility across page/fragment paths. Resolve actual form and redirect URLs from both Workbench entry points. Verify the existing account actions remain usable |
| AP-1-T11 | [Playwright smoke test](../../scripts/check_administrator_promotion_browser.cjs) | Submit with keyboard at desktop and narrow viewport widths. Assert focus and the modal's open state after errors, test a dropped response, and reopen for a different account. Confirm success focuses a surviving element after the directory swap |
| AP-1-T12 | `docs/user-guide.md`, implementation evidence, and repository checks | Document Team promotion and required sign-in. Run the applicable lint, format, type, documentation, Hedron, and regression checks. Record which real database and browser cases ran |

Land the service and its focused tests together. Land the route, fragment, and browser behavior
together so the directory action first ships with its feedback handling. The release gate is a
passing integration run on both database engines plus the browser checks for the two failure
surfaces. A fixture skip does not satisfy the PostgreSQL gate. Existing role, membership, and
audit tables support the feature without a migration. An application rollback removes the
promotion action while preserving committed memberships and audit history.

## Acceptance evidence

- An administrator promotes an eligible standard user; the membership survives a new database
  session and the target can access **Team** and **Audit log** after fresh sign-in.
- Existing memberships and account/profile/credential fields are preserved. Exactly one
  security-version increment and one audit event occur for the change.
- Old browser sessions, bearer access tokens, and refresh tokens cannot authenticate after
  promotion. Cover local-password and trusted-header sign-in, including concurrent session
  issuance/refresh at the promotion boundary.
- Add a deterministic PostgreSQL regression: pause refresh after it locks a target session;
  acquire the promotion's user locks; resume refresh through its audit insert while promotion
  attempts revocation. Both transactions must finish without a deadlock, and the resulting
  access/refresh tokens must fail authentication after promotion commits. Also exercise refresh
  of the acting administrator, whose user row promotion locks; their valid session must survive.
  Test the reverse interleaving, with promotion winning before the target's refresh, and verify
  that refresh is rejected.
- A standard user cannot promote themselves or anyone else, including with forged fields.
  Missing/invalid CSRF, anonymous requests, and a disabled or deprivileged actor cause no change.
- Pending, disabled, unverified, and local accounts without a password cannot be promoted.
  Stale-page submissions recheck current state. Missing targets and missing role data fail safely.
- Duplicate and concurrent promotions produce one membership, one role-change event, and one
  session invalidation. Injected audit or confirmed pre-commit failure leaves memberships, sessions,
  and security version unchanged. Exercise concurrency on PostgreSQL and file-backed SQLite.
- If target disable commits first, promotion rejects it. If promotion commits first and a
  subsequent disable succeeds, the account ends disabled with the recorded administrator
  membership and no valid sessions. Actor disable or role removal committed before the
  authoritative recheck rejects the action. Test each order explicitly.
- HTMX responses refresh only declared regions; ordinary POST redirects display the correct
  notice. Search, filter, pagination, and mounted Workbench paths survive the operation.
- Browser-test the open confirmation dialog with 409 and 503 responses: visible in-dialog
  feedback receives focus, the modal stays open, cancel works, and only retryable outcomes allow
  another submission. Verify dependency/global errors close the modal and focus visible feedback,
  expired sessions redirect, and network failures show outcome-unknown guidance without retries.
- Cancel submits nothing; confirmation works by keyboard; role/action changes and focus render
  correctly after success. Reopening for another account clears stale feedback and uses the
  correct mounted action URL. Existing invitations, approvals, and status actions continue to work.

The automated checks use isolated databases and synthetic accounts.

| Evidence | Result |
|---|---|
| [User role transaction and concurrency tests](../../tests/test_user_roles.py) | PostgreSQL and file-backed SQLite verified rollback, repeat promotion, refresh and session issuance races, lock conflicts, and both orders of a concurrent disable. PostgreSQL refresh audit writes completed while promotion held its user locks |
| [Authentication tests](../../tests/test_auth_security.py) | Browser cookies, bearer tokens, and refresh tokens from before promotion lost access. Fresh local-password and trusted-header sign-ins gained administrator access. A request paused between session authentication and the role check could not gain access |
| [Web flows](../../tests/test_web_flows.py), [fragment tests](../../tests/test_ui_interactions.py), and [shell tests](../../tests/test_ui_shell.py) | Verified the audit payload, directory filters and pagination, declared fragment regions, and promotion URLs and redirects through each simulated Workbench entry point |
| [Chromium smoke test](../../scripts/check_administrator_promotion_browser.cjs) | Verified keyboard confirmation and cancel, focus at desktop and 375 px, a search response overlapping a pending promotion, the current error and support reference across consecutive failures, timeout recovery, expired sign-in, ordinary form submission, and a lost response after commit |
| `make check` | Lint, formatting, type checking, documentation, Hedron, and Posit mount checks passed. The application suite passed 1,099 tests with 82.61% coverage, and all 25 demo tests passed |

Run the database tests with `python -m pytest tests/test_user_roles.py`. The PostgreSQL cases
use `initdb` and `postgres` from the local test environment. Run the browser check with
`node scripts/check_administrator_promotion_browser.cjs` from an environment with Node.js,
Playwright Chromium, and the repository's Python dependencies. Set `PYTHON` to override the
default `.venv/bin/python`. The browser check starts and removes its own app and database.

The [user guide](../user-guide.md#make-an-existing-user-an-administrator) describes promotion,
session revocation, and audit lookup.
