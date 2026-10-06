# CDO-4551 GitHub review bugs

## GitHub bug tracking

Eight bugs were created, one per review finding. Each includes the observed/expected behavior, cause, reproduction, suggested correction, acceptance checks, testing strategy, documentation follow-up, and related Jira links.

[Published review report](https://github.com/eddiethedean/user-token-management-app/issues/104#issuecomment-6020068416) · [Runnable reproductions and observed output](https://github.com/eddiethedean/user-token-management-app/issues/104#issuecomment-6020068702)

| Finding | Priority | GitHub bug |
| --- | --- | --- |
| F1 | P1 | [#132 — Blocked guardrail runs fail internally and lose their findings](https://github.com/eddiethedean/user-token-management-app/issues/132) |
| F2 | P1 | [#133 — Unavailable Foundry sensitivity metadata is treated as a clean source](https://github.com/eddiethedean/user-token-management-app/issues/133) |
| F3 | P1 | [#134 — Misplaced metadata refresh lets preview scans read marked Foundry tables](https://github.com/eddiethedean/user-token-management-app/issues/134) |
| F4 | P1 | [#135 — Foundry table sensitivity checks ignore resource-marking pages after the first](https://github.com/eddiethedean/user-token-management-app/issues/135) |
| F5 | P1 | [#136 — Run submission rejects valid Remove routes by validating the original schema](https://github.com/eddiethedean/user-token-management-app/issues/136) |
| F6 | P2 | [#137 — Remove leaves selected columns in existing PostgreSQL destination schemas](https://github.com/eddiethedean/user-token-management-app/issues/137) |
| F7 | P2 | [#138 — Run records mark guardrail actions applied before execution starts](https://github.com/eddiethedean/user-token-management-app/issues/138) |
| F8 | P2 | [#139 — Source manifests lose original columns and types after guardrail projection](https://github.com/eddiethedean/user-token-management-app/issues/139) |

Review snapshot: local uncommitted guardrail implementation on `main`, based on `834481f61a811e192ed914d9beaddd9d77e2df76`, reviewed October 6, 2026.
