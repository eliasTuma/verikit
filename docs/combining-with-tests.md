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
