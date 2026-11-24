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

**Does this flow already have a model or a test file? Then add to it; do not start a second
one.** Look in the manifest for checks with the flow's tag (`list --tag checkout`) and in
`verification/<flow>/`. Rules about the same flow share one model and one test file, and each
rule still gets its own manifest entry:

- Model: add one named invariant for the rule and one witness for it. Add actions only when
  the rule needs behaviour the model lacks (a failure, a crash, a retry). List the invariant in
  the `.cfg` as well.
- Code: add one named test to the flow's test file, reusing its fakes and generators.
- Manifest: a new `[[check]]` per level with `id = "<flow>-<rule>-<level>"`, the flow's name in
  `tags`, and a `run` that selects only this rule: `--inv TypeOK --inv <Rule>` for the model
  (`--inv` replaces the `.cfg` list), the runner's test-name filter for code. Use the same
  `rule` sentence, word for word, in every entry of one rule: the report groups by it.
- Your edit makes every check of the flow out of date, because they share the model (and the
  test file in `harness`). That is intended: rerun and record them all with
  `python verification/verikit.py run --tag <flow>`, then `sync --tag <flow>`. A new action
  can break an older rule; that is a finding, not a reason to remove the action.
- If the flow has a conformance check, update its trace recorder for new variables or actions.
- A rule that depends on two flows changing the same data gets its own small model: the shared
  state and only the actions of each flow that touch it. Never merge two flow models.

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
- **If the guard is in the database** (a conditional `UPDATE`, a unique constraint, a row
  lock) or the service runs as several instances, an in-memory fake cannot show the bug. Use
  two real connections and let the test schedule the SQL statements: see the kit's
  `examples/node-postgres` and the "Database-backed promises" section of `stacks/node.md`.

**Conformance checks (when a promise has both a model and code):**
- A design check is blind to code that drifts from the model. Close that gap with a third
  check: a test that records, for many orderings, the values of the model's variables as the
  real code runs (one JSON object per observation, one trace per line, appended to the file
  named by `VERIKIT_TRACE_FILE`), and `run = '{verikit} trace <Spec.tla> --record "<test command>"'`.
- The recorder asserts nothing about the rule; it only writes down what happened. Run the code
  with the parameters the model's constants describe (the model's `MaxAttempts = 3` means
  `maxRetries = 2` in the example), or the model will rightly reject the traces.
- Give it two variants: the product code with a guard removed, and the model with a constant
  changed (`--const`). Both must end in `TRACE REJECTED`.
- It shows the code stays inside the model. It cannot show behaviour the model allows but the
  code never has; say so in `assumes`. Reference: the kit's `examples/node-checkout`
  (`checkout-conformance`).

## 5. Prove it can fail - not optional

A check that you have never seen fail proves nothing. Add `[[check.must_fail]]` entries:

- **One broken variant per guard**, each on its own. In product code, prefer a
  find-and-replace edit that names only the guard's own text:
  `edits = [{ file = "src/pay.js", find = 'if (order.paid) return;', replace = '' }]`.
  `find` must match exactly once; it keeps working when code around the guard changes, where
  a diff stops applying. Use a `patch` file (written with `git diff --relative` from the
  folder that holds `verification/`) when the change spans several places. In a model, use a
  `--const` that disables the guard. A guard is any
  check or ordering whose removal could break THIS rule. Code that only affects whether
  things eventually finish (a `finally` that releases a lock) is not a guard for a safety
  rule like "at most once"; list it under the gaps instead.
- **One reachability witness per rule checked on a model:** `--inv NeverCharged` where
  `NeverCharged` says the situation the rule talks about never happens. It must FAIL; otherwise
  the rule may pass vacuously. A model shared by several rules needs one for each: "if the
  charge fails, the item goes back to stock" proves nothing in a model where no charge fails.
- **In a model with several rules, every `must_fail` line names its own rule** with `--inv`.
  With all invariants on, TLC stops at the first rule that breaks, which can be another one,
  and `match` misses.
- **Every `must_fail` has a `match`** regex naming the broken rule. Write regexes as TOML
  literal strings in single quotes: `match = 'VIOLATION\] NeverChargedTwice'`.

## 6. Register and run

Add the `[[check]]` with `id`, `rule`, `harm`, `level` (`design`, `code` or `conformance`),
`tool`, `tags`, ONE `run` command, `covers` (relative to the folder holding `verification/`:
every product file the check describes - both frontend and backend paths if it spans them),
`harness` (the check's own test files outside `verification/`), `bounds`, `assumes` and the
`must_fail` entries.

Keep `covers` as narrow as the promise. A glob over a busy folder makes the check stale on
every unrelated edit, and people stop reading stale warnings. When the promise lives in one
function of a large file, cover that function: `"src/Payments/CheckoutService.cs#PayForOrder"`
(every declaration with that name, overloads included; for other regions put
`verikit:begin NAME` / `verikit:end NAME` comments around the code and use `#NAME`). If the
name is not found, `stale` says so and `sync` refuses. If `init --ci` created a workflow, make it install what this
check needs (uncomment `npm ci` with the right `working-directory`, keep `setup-dotnet`, use a
Windows runner for `net4x` tests). Then:

```sh
python verification/verikit.py run <id>
```

- **Holds and every must_fail fails:** `python verification/verikit.py sync <id>`, then commit
  the check together with its sync record (`verification/sync/<id>.json`).
- **Fails on the current code:** that is a finding. Replay the counterexample against the
  real code step by step. If every step can happen, report it in the code's words with the
  harm. Do not weaken the rule or the model, and do not change product code unless asked.
  If a property test found it with a random seed, keep the case:
  `python verification/verikit.py seed add <id> <seed> --note "<what it is>"` (every later run
  replays it), and commit `verification/seeds/<id>.txt`.
- **You have uncommitted work in the covered files:** use
  `python verification/verikit.py run <id> --isolated`, so the broken variants are applied in
  a throwaway copy and never in your working files (needs `[isolation] link = ["node_modules"]`
  in the manifest when dependencies are git-ignored).
- **Fails in a way the code cannot do:** the model is wrong. Fix it and say what was wrong.
- **TLC prints a warning:** read it. "changed while it is specified as UNCHANGED" means an
  action can never fire; `verikit tlc` treats it as an error.

## 7. Hand off

Tell the user in plain words: the rule, the harm, the tools and why, bounds and
assumptions, the result (counterexamples as numbered steps in the code's words), each
broken variant you watched it catch, the command to rerun, and the gaps. If
`verification/PLAN.md` exists, mark the promise done there. Point at `/verify-maintain`
for changes that skip the check.
