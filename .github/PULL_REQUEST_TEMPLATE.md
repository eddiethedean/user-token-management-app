## What changed

<!-- Describe the user or maintainer problem this change solves. Include the main files or boundaries touched. -->

## Validation

- [ ] I ran the focused tests or checks for this change.
- [ ] I ran `make check` (or explained why a narrower check is appropriate).
- [ ] I ran `git diff --check`.

## Safety and operations

- [ ] No secrets, credentials, raw provider responses, or customer data are included.
- [ ] Security-sensitive behavior has a regression test and was reviewed against `SECURITY.md`.
- [ ] Schema changes include an Alembic migration and rollout/recovery notes.
- [ ] Runtime, configuration, deployment, or user-facing documentation is updated when needed.

## Handoff notes

<!-- Mention migrations, changed environment variables, restart requirements, opt-in checks, or known follow-up work. -->
