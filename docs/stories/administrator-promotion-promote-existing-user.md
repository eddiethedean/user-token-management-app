# Promote an existing user from Team

**Jira story [CDO-5097](https://idstjira.socom.mil/jira/browse/CDO-5097) · Implemented and verified locally**

[Parent epic CDO-5096](administrator-promotion-epic.md) · [Issue export](../../artifacts/jira/data-mover-administrator-promotion-issues.csv)

## User story

As a Data Mover administrator, I want to promote an eligible existing user from **Team** so they
can manage users and invitations and review the application-wide audit log after they sign in
again.

## How to promote a user

1. Open **Team** and find the user in the directory. The account must be active and have a
   verified email address. If the deployment uses local password sign-in, the account also needs
   a password set.
2. Select **Make administrator** and verify the email in the confirmation dialog. The dialog
   explains the privileges being granted and when the user's current sessions will end. Select
   **Cancel** to close the dialog before sending a request.
3. Confirm by selecting **Make administrator** in the dialog. The row displays the added
   **Administrator** role, and the confirmation reminds you that the promoted user must sign in
   again. The directory keeps the applied search, status filter, and page.
4. To review the change, open **Audit log** and filter for `admin.user.roles_changed`. The event
   identifies the acting administrator, the affected account, and the roles before and after the
   grant. [Administrator user guide](../user-guide.md#make-an-existing-user-an-administrator)

## Acceptance criteria

| Scenario | Expected result |
| --- | --- |
| An active administrator confirms an eligible account | The app verifies the request, adds **Administrator** to the existing roles, and records the grant. The result is announced and focus returns to the updated row or directory heading. |
| The request lacks administrator authority or valid CSRF proof | The app rejects the request before changing the account. |
| Eligibility changes before confirmation | The server checks the account again. If it became ineligible, the dialog explains the restriction and tells the administrator to refresh the directory. |
| The same account is promoted again | The app reports that the account is already an administrator. The original role grant remains the only audit event and session revocation for that promotion. |
| The promoted user opens a page with an old session | Previous access and refresh credentials are rejected. Signing in again grants access to **Team** and **Audit log**. |
| A search response arrives during confirmation | The earlier response is discarded so the pending promotion can show its result. |
| The browser times out or loses the response | Refresh **Team** and check the **Roles** column before submitting again. The current error includes a support reference when one is available. |

## Verification

Service and web tests cover role assignment, session revocation, authorization, repeated requests,
and audit records. Chromium checks cover keyboard confirmation, focus after success or failure,
overlapping search, and timeout recovery. The plan records the PostgreSQL, SQLite, browser, and
Workbench mount results. [Acceptance evidence](../plans/administrator-promotion.md#acceptance-evidence)
