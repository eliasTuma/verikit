# Combining verikit with your tests

verikit does not replace your test suite or your test runner. Code checks **are** tests in your
existing test projects. verikit adds three things around them: the promise they check, the
broken variants that must make them fail, and drift tracking.

## Put code checks in the existing test projects

| Runner | Mark | Manifest `run` |
|---|---|---|
| xUnit | `[Trait("Category", "Verification")]` | `dotnet test <proj> --filter Category=Verification` |
| NUnit | `[Category("Verification")]` | `dotnet test <proj> --filter Category=Verification` |
| MSTest | `[TestCategory("Verification")]` | `dotnet test <proj> --filter TestCategory=Verification` |
| legacy (non-SDK) .NET Framework project | MSTest `[TestCategory("Verification")]`, or a trait/category as above | `{python} verification/legacy_test.py <sln> <tests.dll> TestCategory=Verification` (MSBuild + vstest; copy it from `examples/dotnet-legacy`). Use `Category=` for xUnit/NUnit. Never `dotnet test` on the project: it runs nothing and exits 0 |
| Vitest | `test('verification: ...')` | `npx vitest run -t verification` |
| Jest | `test('verification: ...')` | `npx jest -t verification` |
| Karma/Jasmine (Angular) | `src/app/<area>.verification.spec.ts` | `npx ng test --watch=false --no-progress --browsers=ChromeHeadlessCI --include=src/app/<area>.verification.spec.ts` (a headless launcher in `karma.conf.js`; see `examples/angular-testbed`) |
| node:test | a separate `*.test.js` file | `node --test test/<file>.test.js` |

The manifest's `run` selects only the verification tests, so verikit can run each check, and
each broken variant, quickly. Make sure the team's normal command runs them too: filters by
category include them by default, but a test script that lists files one by one
(`node --test a.test.js`) must be given the new file or a glob (`node --test "test/*.test.js"`).

List each check's own test files under `harness` in the manifest. Then a change to the check
itself (a weakened assertion, fewer runs) makes it stale and `run --changed` picks it up, just
like a change to the product code under `covers`.

## Seeds

`verikit run` exports `VERIKIT_SEED` (random and printed, or `--seed N`). Read it in property
tests (`seed` in fast-check, `Replay` in FsCheck) so any CI failure replays locally with
`verikit run <id> --seed N`.

## Reports

`verikit run --junit verify-results.xml` writes one test case per check step ("holds on the
current code", "must fail: <why>") with the rule in the name. Publish it like your other test
results: Azure DevOps `PublishTestResults@2` (JUnit), a GitHub Actions artifact or JUnit
reporter action, GitLab `artifacts:reports:junit`.

## Pull requests

`verikit run --ci` reads the pull request's target branch from GitHub Actions, Azure Pipelines
(Azure Repos or GitHub repos) or GitLab CI, and runs only the checks whose covered files (or any
verification file) changed relative to it. On a push, or if the target branch cannot be diffed
(a shallow clone), it runs every check - never none. `verikit run --changed origin/main` does the
same with an explicit base. Run everything on main or nightly.

The CI templates use `--ci`, run each verification root of a monorepo, and publish JUnit:

| CI | `init` flag | Notes |
|---|---|---|
| GitHub Actions | `--ci github` | One matrix job per root. `fetch-depth: 0`. |
| Azure Pipelines | `--ci azure` | One job runs every root (`${{ each }}`), then `PublishTestResults@2` (JUnit) fills the Tests tab. `fetchDepth: 0`. For **Azure Repos**, the YAML `pr:` trigger is not used: add the pipeline as a *Build validation* branch policy. |
| both | `--ci both` | Writes both files. |

Run `init --ci ...` again inside each other root of a monorepo: it adds that root to the
`# verikit:roots` list in the existing workflow.

## Frontend and backend in separate repositories

A check lives in one repository, but its `covers` (and `harness`) can name files in another one
through a named source:

```toml
[sources.web]
path = "../storefront-web"          # where that repo is checked out, relative to this root

[[check]]
id = "pay-once"
covers = ["src/Payments/**/*.cs", "web:src/app/checkout/**/*.ts"]   # name:glob = in that repo
harness = ["web:src/app/checkout/pay.verification.spec.ts"]
```

- `sync` records the content of the covered files in both repositories in
  `verification/sync/<id>.json`. `stale`, `run --changed`, `run --ci` and the Stop hook see any
  later change in the other repository as drift, committed or not. No commit ids are stored, so
  the other repository can squash, rebase or be cloned shallow.
- A missing checkout makes the check `unknown`, which fails `stale --strict` (never `synced`).
  The Stop hook does not block a developer who simply has not cloned the other repo.
- Override the path with `VERIKIT_SOURCE_WEB=<path>` (CI often checks it out elsewhere). Run
  commands get `VERIKIT_SOURCE_WEB` exported, and may use `{source:web}` for the path.
- Drift tracking only: broken-variant patches are applied in this repository, never in the other.
  Put the other side's code check (and its variants) in that repository's own manifest.
- CI: check the other repository out with full history, then set `VERIKIT_SOURCE_<NAME>`.
  GitHub: a second `actions/checkout` with `repository:` and `path:`. Azure: a `resources:
  repositories:` entry and a second `checkout:` step; with more than one checkout step Azure puts
  `self` under `$(Build.SourcesDirectory)/<repo name>`, so adjust the template's `workingDirectory`.

## Many rules: one model and one test file per flow

A product with 25 to 50 rules does not need 50 models. Rules about the same flow (checkout,
refunds, sign-up) describe the same steps, so they share **one model and one test file per
flow**. The manifest still has **one entry per rule**, so every rule keeps its own row in the
report, its own broken variants and its own sync record.

| Piece | How many | What it holds |
|---|---|---|
| `verification/checkout/Checkout.tla` + `.cfg` | one per flow | the flow's steps, including failures, crashes and retries; one named invariant per rule; one witness per rule |
| a test file in the existing test project | one per flow and side | shared fakes, generators and scheduling; one named test per rule |
| `[[check]]` in the manifest | one per rule and level | `id = "checkout-<rule>-<level>"`, `tags = ["checkout"]`, that rule's invariant or test |

```toml
[[check]]
id = "checkout-no-oversell-design"
rule = "Stock never goes below zero."
level = "design"
tags = ["checkout"]
run = "{verikit} tlc verification/checkout/Checkout.tla --inv TypeOK --inv NoOversell"
covers = ["src/Checkout/StockService.cs#Reserve"]

  [[check.must_fail]]
  why = "Broken variant: the stock update does not check the quantity"
  run = "{verikit} tlc verification/checkout/Checkout.tla --const CheckQuantity=FALSE --inv NoOversell"
  match = 'VIOLATION\] NoOversell'

  [[check.must_fail]]
  why = "Witness: the model can sell the last item"
  run = "{verikit} tlc verification/checkout/Checkout.tla --inv NeverSoldOut"
  match = 'VIOLATION\] NeverSoldOut\b'
```

What makes this layout work:

- **`--inv` replaces the invariant list of the `.cfg`.** Name `TypeOK` and the rule's own
  invariant on each `run` line. Keep every rule in the `.cfg` too, so
  `verikit tlc Checkout.tla` on its own checks the whole flow.
- **A broken variant names only its own rule.** With every invariant on, TLC stops at the first
  rule that breaks, which can be a different one, and `match` misses.
- **One witness per rule, not per model.** "If the charge fails, the item goes back to stock"
  passes vacuously in a model where a charge never fails. The witness says the situation the
  rule talks about never happens (`NeverFailedCharge`), and it must FAIL.
- **The same `rule` sentence, word for word**, in a rule's design, code and conformance
  entries. The report groups by that text; a wording difference splits one promise into two.
- **Code checks pick their test by name** with the runner's own filter. A filter that selects
  nothing passes, so watch each one fail with its broken variant.
- **`covers` stays per rule**: the function that keeps that rule, not the whole flow.

**Adding a rule to a flow that already has checks:**

1. Add the invariant and its witness to the flow's model, and a test to the flow's test file.
   Add actions to the model only when the rule needs behaviour the model lacks (a failure, a
   crash, a retry).
2. Add the manifest entries with the flow's tag.
3. The edit makes **every check of that flow out of date**: design and conformance checks share
   the model, code checks share the test file listed in `harness`. That is intended. It is how
   a new rule gets checked together with the ones already there, and a new action can break an
   older rule. Run them all and record the result:
   ```sh
   python verification/verikit.py run --tag checkout
   python verification/verikit.py sync --tag checkout      # reruns, then records; commit verification/sync/
   ```
   A rule that fails now is a finding, not a reason to take the new action out.
4. If the flow has a conformance check, new variables or actions need the trace recorder
   updated as well, or the model will reject the old recordings.

**A rule that spans two flows** (checkout and refunds both change stock) gets its own small
model: the shared state and only the actions of each flow that touch it. Do not merge the two
flow models; the number of orderings multiplies and TLC stops finishing. Tag it with both flows.

**What it costs.** Runs grow with the number of rules, not flows: each rule runs once, plus once
per broken variant.

- On the small checkout model, one TLC run takes about 3 seconds through Docker (one Windows
  laptop, one measurement). Larger models take longer; if one run takes minutes, lower the
  bounds or split the flow.
- Code checks with expensive setup (a database container, a legacy .NET build) pay it once
  per rule and variant. There is no way yet to share one run between rules
  ([roadmap](roadmap.md)).
- Pull requests run only the checks whose files changed (`run --ci`); the nightly run does all
  of them. `run --isolated --jobs N` runs the variants of several checks side by side.

Not measured: a repository with 25 or more rules. Start with one flow and 3 to 5 rules, look at
the run time and at how often checks go out of date, then add the next flow.

## Keeping it quiet and fast on a large repository

- **Narrow covers.** A check that covers `src/Payments/**` is stale after every edit there, and
  people stop reading warnings they see daily. Cover the files the promise lives in, or one
  function: `"src/Payments/CheckoutService.cs#PayForOrder"`.
- **Variants that survive refactors.** A patch with context lines stops applying when nearby code
  changes. `edits = [{ file, find, replace }]` names only the guard's own text.
- **Hands off the working tree.** `run --isolated` applies broken variants in a throwaway copy of
  the repository (what git tracks or would track, as it is on disk), so a run cannot collide with
  uncommitted work, an IDE or a file watcher. `--jobs N` runs the variants of N checks side by
  side; "holds on the current code" still runs in the real tree, one check at a time. List
  git-ignored dependency folders under `[isolation] link` (they are linked, never copied or
  deleted), or give a `setup` command to install into the copy. A .NET solution builds from
  scratch in the copy, so the first variant there is slower than in place.
- **Random failures become permanent tests.** A failed run prints
  `verikit seed add <id> <seed>`. Saved seeds (`verification/seeds/<id>.txt`) are replayed on every
  run. The CI templates also run every check nightly with a new random seed.
- **A page people can read.** `run --summary verify.md` (automatic with `--ci`: the GitHub job
  summary, or the Extensions tab of an Azure run) lists each promise, the failing step and the
  replay command. `verikit report --html status.html` shows every promise and its sync status
  without running anything.

## When the model and the code disagree: `verikit trace`

A design check proves things about the model. If the code drifts, the model keeps passing. A
conformance check closes that gap from the code's side:

1. A test records, for many orderings, the values of the model's variables as the real code
   runs: one JSON object per observation, one trace per line, appended to the file named by
   `VERIKIT_TRACE_FILE`. It asserts nothing; it writes down what happened.
2. `run = '{verikit} trace verification/checkout/Checkout.tla --record "node --test test/checkout.trace.test.js"'`
   replays every distinct trace against the model with TLC and fails with the first observation
   the model cannot reach.

By default the model may take steps the recording did not see; `--exact` forbids that. It shows
that the code stays inside the model, not that the code can do everything the model can.
Worked example: `checkout-conformance` in `examples/node-checkout`.

## Mutation testing

Stryker.NET and StrykerJS mutate code broadly and report which mutants no test kills. verikit's
`must_fail` variants are the targeted version for one promise: the specific guard that keeps
it. They work well together: Stryker for breadth across the suite, verikit for the promises
that matter most.
