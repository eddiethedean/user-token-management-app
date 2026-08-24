# HTMX Framework 0.17 discovery backlog (HED-5)

This is a scoped discovery list, not a delivery plan. It contains no release dates or commitments.

## Reactive dashboards

| Need | Constraint | Discovery question | Priority |
|---|---|---|---|
| Incremental metric updates | Must not leak cross-user data | Can a typed fragment stream preserve region ownership? | High |
| Filtered activity views | Must remain URL-addressable | Which server-side query state belongs in SafeUrl values? | High |
| Empty/loading/error parity | Must be accessible without JS | Can one state primitive cover all three states? | Medium |
| Backpressure | Workers may outpace browsers | How should polling cadence adapt to persisted sequence numbers? | Medium |

## Agent UIs

| Need | Constraint | Discovery question | Priority |
|---|---|---|---|
| Explainable actions | No hidden tool invocation | What typed action metadata should be rendered? | High |
| Approval gates | Human must confirm mutations | Can a submit gate carry a signed intent and expiry? | High |
| Auditability | Every action needs a durable record | Which event shape is shared with pipeline runs? | High |
| Provider isolation | Agents must not inherit master keys | What explicit capability envelope is minimal? | High |

## Open questions

- How should reconnect and replay work after a browser sleeps?
- Which dashboard primitives belong in Hedron versus the application domain?
- What accessibility contract is required for agent-generated content?
- Can typed recipes express user-specific density without product CSS?
