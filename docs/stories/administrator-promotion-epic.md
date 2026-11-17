# Administrator promotion epic

**Jira epic [CDO-5096](https://idstjira.socom.mil/jira/browse/CDO-5096) · Implemented and verified locally**

Data Mover administrators can grant the **Administrator** role to an eligible existing user from
**Team**. This gives a teammate access to user and invitation management and the application-wide
audit log after they sign in again. [Administrator user guide](../user-guide.md#make-an-existing-user-an-administrator)

## Scope and outcome

The epic covers granting the existing **Administrator** role to one account at a time. Before
granting access, the administrator confirms the target email and sees the privileges the user
will gain. The user keeps their existing roles. The app grants the new role, ends the user's
current sessions, and records who made the change in one transaction. [Implementation plan](../plans/administrator-promotion.md)

## Story and estimate

| Issue | Outcome | Status |
| --- | --- | --- |
| [CDO-5097 · Promote an existing user from Team](administrator-promotion-promote-existing-user.md) | An administrator confirms the grant and sees the updated role in the directory. The promoted user signs in again to use administrator features. | Implemented and verified locally |

The issue export contains 12 tasks that total 72 estimated person-hours. It uses `AP-EPIC` for
CDO-5096 and `AP-1` for CDO-5097. [Issue export](../../artifacts/jira/data-mover-administrator-promotion-issues.csv)

## Acceptance and verification

- **Authorization and eligibility.** The app rechecks the administrator's authority and the target
  account's eligibility when the request arrives. Local password and trusted header sign-ins both
  require a fresh session to use the new privileges. [Authentication tests](../../tests/test_auth_security.py)
- **Consistent account state.** A successful grant preserves existing memberships, revokes the
  target's sessions, and creates one role-change audit record. Repeated requests report the
  existing role. Tests cover transaction failures and concurrent session or account updates on
  PostgreSQL and SQLite. [Transaction tests](../../tests/test_user_roles.py)
- **Clear confirmation and recovery.** The directory shows the result, keeps the applied filters
  and page, and directs the administrator to the current error or status. Verification covers
  Workbench mount paths, keyboard focus, overlapping search, and a lost or timed-out response.
  [Acceptance evidence](../plans/administrator-promotion.md#acceptance-evidence)
