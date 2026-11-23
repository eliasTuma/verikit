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
| Karma/Jasmine (Angular) | `*.verification.spec.ts` | `npx ng test --watch=false --include="**/*.verification.spec.ts"` |
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

## Mutation testing

Stryker.NET and StrykerJS mutate code broadly and report which mutants no test kills. verikit's
`must_fail` variants are the targeted version for one promise: the specific guard that keeps
it. They work well together: Stryker for breadth across the suite, verikit for the promises
that matter most.
