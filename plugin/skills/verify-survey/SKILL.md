---
name: verify-survey
description: Survey a large repository and rank the promises worth machine-checking (money, retries, concurrency, sync, permissions, state machines) across backend and frontend, then write verification/PLAN.md for a human to approve. Use for /verify-survey, "what should we verify", or before the first /verify-create in a big or unfamiliar codebase.
disable-model-invocation: true
---

# Survey: what is worth verifying here

A large repo has thousands of behaviours and room for perhaps ten verified promises.
This skill finds the few where a bug costs the most and tests reach the least, and
writes them down as a plan the user approves before any model or harness is written.
It reads; it changes nothing except `verification/PLAN.md`.

## 1. Map the system

Run `python verification/verikit.py doctor` (or `python <kit>/verikit.py doctor` before
`init`). It lists every project with its stack and test framework: .NET Framework and
.NET services, Node backends, React and Angular apps. Then read, for each deployable
part, enough to answer:

- What does a user or another service ask it to do? (endpoints, handlers, jobs, screens)
- What state does it own? (tables, queues, caches, local storage, stores)
- Where does it talk to something it does not control? (payment providers, email, other
  services, the browser's network, timers)
- Where does work happen more than once or out of order? (retries, timeouts, queues with
  redelivery, background jobs, websockets, optimistic UI updates, double clicks, two tabs)

## 2. List candidate promises

Write each one as a single sentence a product owner would agree with. Look hardest at:

| Area | Typical promise | Where it breaks |
|---|---|---|
| Money | A customer is never charged twice for one purchase. Refunds never exceed the charge. | retries, timeouts, webhooks delivered twice |
| Inventory / quotas | Stock never goes below zero. A user never exceeds their quota. | concurrent requests, check-then-act |
| Jobs | A job never runs twice at once, and every accepted job eventually runs. | leases, crashes, redelivery |
| Access | A user can never read another tenant's data. | new endpoints, ID guessing, cached responses |
| Workflow state | An order never skips a state (shipped before paid). | new transitions, partial failures |
| Sync / offline | An edit made offline is never silently lost. | reconnect, two devices, last-write-wins |
| Frontend | Pay sends one request however fast it is clicked. A stale response never overwrites a newer one. | double clicks, slow networks, route changes |
| Cross-service | Both sides agree on the outcome after a timeout. | idempotency keys, at-least-once delivery |

For each candidate, find the code that makes the promise, and check whether existing
tests ever reach the case that breaks it. Tests that only walk the happy path do not count.

## 3. Score and rank

For each candidate record: **harm** (who is hurt, how badly, how visibly), **likelihood**
(how many orderings or inputs reach it; does the code retry, run concurrently, or cross a
network), **test reach** (do current tests hit the breaking case?), and **cost to check**
(a small TLA+ model, a property test in the existing test project, or a verifier on the code).
Rank by harm x likelihood x (1 - test reach), then cost as a tie-breaker. Prefer the
strongest promise, not the easiest one to verify.

## 4. Choose the level for each top promise

Use [the routing table](../verify-create/routing.md) and the stack notes in
[../verify-create/stacks/](../verify-create/stacks/). Most top promises in a frontend +
backend system want two checks:

- a **design** check (TLA+) of the flow across browser, network, services and providers;
- a **code** check per side that owns part of the promise, written in that side's own test
  project (xUnit/NUnit/MSTest + FsCheck/Coyote, Jest/Vitest/Jasmine + fast-check), so it
  runs with the team's other tests.

## 5. Write verification/PLAN.md

```markdown
# Verification plan - <repo>, <date>

| # | Promise (one sentence) | Harm | Why tests miss it | Checks (level: tool, where) | Covers | Est. effort |
|---|---|---|---|---|---|---|
| 1 | A customer is never charged twice for one purchase. | ... | retry on timeout; no test lets both calls land | design: TLA+; code: FsCheck in Payments.Tests; code: fast-check in web/checkout | src/Payments/**, web/src/app/checkout/** | S |

## Not chosen, and why
## Open questions for the owner
```

Keep it to the top 3-10. Then stop and ask the user to approve, reorder or cut the list.
Do not start `/verify-create` until they have. When they approve, run `/verify-create`
on the first promise, one promise at a time.
