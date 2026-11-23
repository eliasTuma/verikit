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
| legacy .NET Framework project | any of the above | `vstest.console.exe <dll> /TestCaseFilter:Category=Verification` |
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

`verikit run --changed origin/main` runs only the checks whose covered files (or any
verification file) changed relative to the PR base, plus uncommitted edits. Run everything on
main or nightly.

## Mutation testing

Stryker.NET and StrykerJS mutate code broadly and report which mutants no test kills. verikit's
`must_fail` variants are the targeted version for one promise: the specific guard that keeps
it. They work well together: Stryker for breadth across the suite, verikit for the promises
that matter most.
