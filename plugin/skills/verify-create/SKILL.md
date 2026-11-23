---
name: verify-create
description: Add one machine-checked promise to this repo - a TLA+ model of the design and/or a property or concurrency test of the real code inside the existing test projects - register it in verification/verification.toml, and prove the check can fail. Use for /verify-create, "prove this invariant", "model-check this race", or when retries, payments, locks, queues, sync, state machines or UI races need more than example tests.
disable-model-invocation: true
---

# Create a verified promise

Example tests check the paths someone thought of. This skill checks every path inside a
stated scope for ONE promise, and wires it into `verification/verification.toml` so every
later change is checked with one command: `python verification/verikit.py run <id>`.

Before you start:
- If `verification/verikit.py` is missing, run `python <kit>/verikit.py init` in the folder
  that should own the checks: the repo root, or one service folder in a monorepo.
- Run `python verification/verikit.py doctor` to see the projects, stacks and test frameworks.
- If `verification/PLAN.md` exists, take the next approved promise from it. If the repo is
  large and there is no plan, suggest `/verify-survey` first.

## 1. Pick the promise from the code

Read the code before you ask anything; ask the user only what the code cannot tell you.
A good target is a promise where a break hurts users and tests rarely reach the breaking
case. Choose the one where a bug costs the most, not the easiest one to verify.

## 2. Write the rule, and show the user what it leaves out

- **Rule:** one plain sentence. "A customer is never charged twice for one purchase."
- **Harm:** one sentence about what happens to a person when it breaks.
- **Gaps:** two or three neighbouring promises this rule does NOT cover.

Show these to the user before modelling. A check shows the code keeps the rule you
wrote; only the user can judge that it is the right rule.

## 3. Let the question choose the tool

| The question | Tool |
|---|---|
| Can steps happen in a bad order? (retries, races, queues, sync, UI flows) | TLA+ model checked by `verikit tlc`. Default in every stack. |
| Does this code behave for every input up to a bound? | A verifier on the real code if the language has one (Kani, CBMC, JBMC, CrossHair). |
| Must it hold at every size? | A proof (Lean, Dafny, Verus). Rarely the first step. |
| No verifier for this language (C#, TypeScript, JavaScript...) | Property / concurrency tests on the REAL code with generated inputs and orderings. Say it is testing, not proof. |

Stack-specific instructions - test projects, libraries, run commands, pitfalls:
[.NET and .NET Framework](stacks/dotnet.md) · [Node backend](stacks/node.md) ·
[React](stacks/react.md) · [Angular](stacks/angular.md) · [other stacks](routing.md).

**Design check, code check, or both?**
- **Both** when the promise depends on more than one process or machine: retries across a
  network, several instances of a service, queues, webhooks, browser + API. A code check runs
  in one process and cannot see a second instance; the model can (make the instance count a
  constant). This is also the default when unsure.
- **Code check only** when everything that can interleave happens inside one process and the
  test can drive every ordering (a UI component, an in-process state machine).
- **Design check only** when the code does not exist yet or cannot be driven from a test;
  say in the hand-off that nothing checks the code itself.

For a promise that spans frontend and backend, write ONE design model of the whole flow
(browser, network, services, providers as separate actors) plus one code check per side
that owns part of the promise. The model can drift from the code; the code checks cannot,
but they only sample. Together they cover each other's blind spot.

## 4. Build the checks so they are true to the code

**Models (TLA+), in `verification/<area>/`:**
- Every action is one indivisible step in the code. Put the file and function it stands
  for in a comment above it. Use this repo's names.
- Write `Next` as a disjunction of named actions. A trace step is labelled with the action
  TLC evaluated (for `\E n \in S : Handle(n)` that can be `Handle`, without `n`); `verikit tlc`
  prints which variables changed in each step, and that shows which request moved. If the
  reader needs the request named, keep its id in a variable (e.g. `lastStep`).
- Model the environment honestly: the network delays, reorders and duplicates; timers fire
  before or after replies; a second user, tab or worker can act at any time.
- **In-memory guards protect one process.** A lock, `Set`, flag or cache held in memory does
  nothing for a second instance of the service. Model the number of instances as a constant;
  if the rule breaks with 2, report it as a finding, or record "runs as a single instance" in
  `assumes` only after the user confirms it.
- Keep bounds small (2-3 actors, 2-3 retries) and write them in `bounds`. Write every
  assumption in `assumes`. Add `TypeOK`.
- One `.cfg` per spec. Express variants with `--const NAME=VALUE` and witnesses with
  `--inv NAME` on the `verikit tlc` command line; do not copy config files.
- Start from the verikit kit's `templates/tla/Starter.tla` if you have the kit; the worked
  reference model is the kit's `examples/node-checkout/verification/checkout/Checkout.tla`.

**Code checks, inside the EXISTING test projects:**
- Put them next to the team's tests, in the same project and runner, and mark them so
  they can run alone: `[Trait("Category", "Verification")]` (xUnit),
  `[Category("Verification")]` (NUnit), `[TestCategory("Verification")]` (MSTest), or a
  `verification` name / tag in Jest, Vitest and Jasmine. The manifest's `run` selects them.
  Make sure the team's normal test command includes them too: if the test script lists
  files explicitly (`node --test a.test.js`), add the new file or switch to a glob.
- Editing test and CI configuration so the checks run (test scripts, test project
  references, the CI workflow's install steps) is part of this work. Editing product logic
  is not, unless the user asks.
- List the check's own test files in the manifest's `harness`, so editing the check itself
  (a weakened assertion, fewer runs) makes it stale and re-runs it, like a product change.
- Run the real code. Fake only the boundaries you do not own (network, clock, provider),
  and let the tool control their timing and order.
- Read the seed from the `VERIKIT_SEED` environment variable when the library supports a
  seed, so a CI failure can be replayed with `verikit run --seed N`.
- Make the failure message state the rule, so `match` can find it.

## 5. Prove it can fail - not optional

A check that you have never seen fail proves nothing. Add `[[check.must_fail]]` entries:

- **One broken variant per guard**, each on its own: a `patch` that removes or reorders
  one guard in the product code (write it with `git diff --relative` from the folder that
  holds `verification/`), or a `--const` that disables it in the model. A guard is any
  check or ordering whose removal could break THIS rule. Code that only affects whether
  things eventually finish (a `finally` that releases a lock) is not a guard for a safety
  rule like "at most once"; list it under the gaps instead.
- **One reachability witness per model:** `--inv NeverCharged` where `NeverCharged`
  says the good thing never happens. It must FAIL; otherwise the rule may pass vacuously.
- **Every `must_fail` has a `match`** regex naming the broken rule. Write regexes as TOML
  literal strings in single quotes: `match = 'VIOLATION\] NeverChargedTwice'`.

## 6. Register and run

Add the `[[check]]` with `id`, `rule`, `harm`, `level` (`design` or `code`), `tool`,
`tags`, ONE `run` command, `covers` (globs, relative to the folder holding `verification/`,
of every product file the check describes - both frontend and backend paths if it spans
them), `harness` (the check's own test files outside `verification/`), `bounds`, `assumes`
and the `must_fail` entries. If `init --ci` created a workflow, make it install what this
check needs (uncomment `npm ci` with the right `working-directory`, keep `setup-dotnet`, use a
Windows runner for `net4x` tests). Then:

```sh
python verification/verikit.py run <id>
```

- **Holds and every must_fail fails:** commit, then `python verification/verikit.py sync <id>`
  and commit the manifest.
- **Fails on the current code:** that is a finding. Replay the counterexample against the
  real code step by step. If every step can happen, report it in the code's words with the
  harm. Do not weaken the rule or the model, and do not change product code unless asked.
- **Fails in a way the code cannot do:** the model is wrong. Fix it and say what was wrong.
- **TLC prints a warning:** read it. "changed while it is specified as UNCHANGED" means an
  action can never fire; `verikit tlc` treats it as an error.

## 7. Hand off

Tell the user in plain words: the rule, the harm, the tools and why, bounds and
assumptions, the result (counterexamples as numbered steps in the code's words), each
broken variant you watched it catch, the command to rerun, and the gaps. If
`verification/PLAN.md` exists, mark the promise done there. Point at `/verify-maintain`
for changes that skip the check.
