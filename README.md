# verikit

[![ci](https://github.com/eliasTuma/verikit/actions/workflows/ci.yml/badge.svg)](https://github.com/eliasTuma/verikit/actions/workflows/ci.yml)

**Machine-checked promises, beside your existing tests, in any repo.**

AI agents now write more code than people can read. Tests check the paths someone thought
of; the expensive bugs live on the paths nobody did: the retry that lands after the
timeout, the double click, the stale response, the job that runs twice. verikit lets you
state a few promises in plain English - *"a customer is never charged twice for one
purchase"* - and have each one checked against **every ordering in a stated scope** (a TLA+
model of the design) and/or **hundreds of generated orderings of the real code** (property
tests inside your own test projects), with proof that every check can actually fail.

It is a small Python runner (stdlib only), three agent skills (also installable as a Claude
Code plugin), seven runnable examples for .NET (including legacy .NET Framework and Coyote),
Node, React and Angular, and CI templates for GitHub Actions and Azure Pipelines. Version
**0.3.0**; see [what has been tested](#supported-stacks).

---

## Best used for

- The **3-10 promises per product where a bug is expensive and tests rarely reach the
  breaking case**: payments and refunds, retries and timeouts, idempotency, job leases and
  queues, inventory and quotas, multi-tenant access, order/workflow state machines, offline
  sync, double-submit and stale-response races in the UI.
- Codebases where **agents write and merge code**: the checks are cheap to rerun on every
  change, and a Claude Code hook stops an agent from finishing with unchecked edits.
- **Mixed stacks**: one design model can span browser, API, queue and payment provider
  while each side keeps its own code-level check in its own language and test runner.

Not for: replacing your unit or end-to-end tests, verifying every function, UI layout, or
performance. Keep your tests; this is for the promises they cannot reach.

## How it works

1. **One promise, one sentence**, plus the harm and what it leaves out. A human owns it.
2. **The question picks the tool.** Can steps happen in a bad order? -> a small **TLA+ model**
   checked exhaustively by TLC. Does the real code hold for every input/ordering? -> a
   **property or concurrency test** in the code's own test project (FsCheck, fast-check, ...)
   or a code verifier where the language has one.
3. **A check you have never seen fail proves nothing.** Every check lists `must_fail`
   variants - a patch that removes a guard, a model constant that disables it, a witness that
   the good thing can happen at all. `verikit run` fails if any of them *passes*.
4. **Record what it covers.** Each check names its files and the commit where it last matched
   the code. `verikit stale` flags drift; `/verify-maintain` decides drift vs. real bug.
5. **Enforce it.** CI runs the checks whose code changed on every PR and reports JUnit like
   your tests. A Stop hook keeps agents from finishing with unchecked edits.

Details: [docs/methodology.md](docs/methodology.md) · [docs/combining-with-tests.md](docs/combining-with-tests.md)

## Supported stacks

| Stack | Design checks (TLA+) | Code checks | Status |
|---|---|---|---|
| **.NET** (net8.0) | yes | xUnit + FsCheck generated event orderings | **ran** - [examples/dotnet-checkout](examples/dotnet-checkout) |
| **.NET Framework** (net48, SDK-style project) | yes | same test, multi-targeted `net8.0;net48` | **ran on Windows** |
| **.NET Framework, legacy (non-SDK) projects** (packages.config, MSTest v2) | yes | FsCheck; MSBuild build + `vstest.console` via `legacy_test.py` | **ran on Windows** - [examples/dotnet-legacy](examples/dotnet-legacy) |
| **.NET async/Task races** (Microsoft Coyote 1.7.11, net8.0) | - | systematic exploration of real `async` interleavings | **ran** - [examples/dotnet-coyote](examples/dotnet-coyote) |
| **Node backend** | yes | fast-check `fc.scheduler()` on real handlers | **ran** - [examples/node-checkout](examples/node-checkout) |
| **React** | yes | fast-check + Testing Library (Vitest) | **ran** - [examples/react-pay-button](examples/react-pay-button) |
| **Angular** - RxJS services/stores | yes | fast-check (Vitest) | **ran** - [examples/angular-search-store](examples/angular-search-store) |
| **Angular** - TestBed through `ng test` (Karma + Jasmine, headless Chrome) | yes | fast-check + `HttpTestingController`, asserted on the DOM | **ran** - [examples/angular-testbed](examples/angular-testbed); Jest and the Angular 21 Vitest runner tried by hand only |
| Python, Rust, Go, Java, C/C++ | yes | see [routing](plugin/skills/verify-create/routing.md) | pointers only, not run |
| **CI: GitHub Actions** | | [templates/ci/github-actions.yml](templates/ci/github-actions.yml), one job per root | schema + actionlint valid; its `run --ci` path runs in the e2e test |
| CI: Azure Pipelines | | [templates/ci/azure-pipelines.yml](templates/ci/azure-pipelines.yml), JUnit to the Tests tab | schema-valid; `run --ci` with Azure's PR variables runs in the e2e test; **not yet run in an Azure DevOps project** |
| **Agents: Claude Code** (skills, plugin, Stop hook) | | | hook: [recorded live session](docs/evidence/live-stop-hook.md); plugin: installed from GitHub with `claude plugin`; skills: used by a fresh agent |
| Agents: Cursor, Codex, others | | read [AGENTS.md](AGENTS.md) and the skill files directly | not tested |

"Ran" means: executed through `verikit run` with the stated versions, every broken variant
caught (`python scripts/check_examples.py`), on Windows 11 and in the kit's CI (Linux for the
JS and net8.0 examples, Windows for .NET Framework).

## Quick start

Requirements: **git**, **Python 3.11+**. For TLA+ checks: **Java 11+ or Docker**. For code
checks: your stack's own SDK (`dotnet`, `node`).

```sh
# 1. get the kit (once per machine) - keep the path short on Windows, e.g. C:\src\verikit
git clone https://github.com/eliasTuma/verikit

# 2. install into a repo: in the folder that should own the checks
#    (the repo root, or one service folder in a monorepo)
cd path/to/your-repo
python path/to/verikit/verikit.py init --hook --ci both       # or --ci github / --ci azure
python verification/verikit.py doctor                         # projects, stacks, test frameworks, tools
#    monorepo: run init again in each service folder; it adds that root to the CI workflow

# 3. in Claude Code
/verify-survey                    # ranks the promises worth verifying -> verification/PLAN.md
/verify-create <the top promise>  # one promise at a time; proves each check can fail

# 4. every day
python verification/verikit.py run --changed          # checks whose covered code changed
python verification/verikit.py stale                  # drift since each sync commit
/verify-maintain                                      # after merges that skipped the checks
```

`init` writes `verification/verification.toml`, a vendored `verification/verikit.py`
(commit both), the skills into `.claude/skills/`, the Stop hook into `.claude/settings.json`,
and the CI workflow(s). Re-running `init` upgrades the vendored runner and leaves everything else alone.

**Skills as a Claude Code plugin instead** (one install per machine, all repos):
`/plugin marketplace add eliasTuma/verikit`, then `/plugin install verikit@verikit`; run `init`
with `--no-skills` so the skills are not listed twice.

**Azure Repos:** the YAML `pr:` trigger does not apply there; add the pipeline as a *Build
validation* branch policy so pull requests run it.

To try it first on known code: `python scripts/check_examples.py` runs every example your
machine has the tools for.

## For agents: read this before cloning

- **Read [AGENTS.md](AGENTS.md) first.** It is the operating guide: install steps, the three
  workflows, hard rules, a done-checklist, troubleshooting and how to extend the kit.
- **Clone the kit outside the target repo**, then run `init` inside the target. Never copy the
  whole kit into a product repo; `init` vendors only what is needed.
- **Hard rules:** never weaken a rule or a model to make a check pass; report the
  counterexample instead. Do not change product code while creating or maintaining checks
  unless the user asks. Every `must_fail` needs a `match`. Never set `synced_commit` by hand.
- **`verikit run` temporarily edits product files** to apply broken-variant patches, then
  restores them byte for byte from backups in `.verikit/` (also on the next start, if a run was
  interrupted). Still, do not run it on a working tree with unrelated uncommitted edits to the same files.
- **Manifest commands are executed as shell commands.** Review `verification/` changes like
  code; never run verikit on an untrusted repo's manifest.
- **Windows + .NET Framework:** keep paths short (under ~200 characters). Test discovery
  fails *silently* near the 260-character limit.
- **TLA+ first run** downloads `tla2tools.jar` 1.7.4 (checksum-pinned) to `~/.cache/verikit`;
  with Docker and no Java it pulls `eclipse-temurin:21-jre`. Behind a proxy, pre-seed the cache
  or set `VERIKIT_CACHE`.
- Python 3.11+ is required even in non-Python repos (the runner uses `tomllib`).

## Commands

| Command | What it does | Exit |
|---|---|---|
| `doctor [--depth N]` | Lists every project (.NET Framework vs .NET, legacy project format, React, Angular, Node backend...), its test frameworks, the tools installed, and what fits. | 0 |
| `init [--hook] [--ci github,azure\|both] [--no-skills] [--force]` | Scaffolds `verification/` in the current folder (monorepo-aware); in another root, adds that root to the existing CI workflow. | 0 / 2 |
| `run [ID...] [--tag T] [--changed [REF] \| --ci] [--junit F] [--json F] [--seed N]` | Each check must pass; each `must_fail` must fail with its `match`. `--ci` = in a PR build (GitHub Actions, Azure Pipelines, GitLab) only the checks whose files changed vs the target branch, else all. `--changed REF` = vs `REF...HEAD`; bare `--changed` = vs each sync commit. The seed is exported as `VERIKIT_SEED`. | 0 / 1 / 2 |
| `stale [--strict]` | Checks whose covered files changed since `synced_commit`. | 0 / 1 |
| `sync ID...` | Refuses with uncommitted covered files; reruns; records HEAD. | 0 / 1 / 2 |
| `list [--tag T]` | Rule, harm, covers, bounds, variants, sync state, last local pass. | 0 |
| `tlc SPEC.tla [-c CFG] [--const K=V] [--inv NAME]` | TLC on local Java, else Docker. Shortest counterexample as per-step changes; TLC warnings surfaced; a self-contradicting action is an error (vacuous pass). | 0 / 1 / 2 |
| `explore MODEL.py` | Breadth-first explorer for small Python models; zero install. | 0 / 1 / 2 |
| `hook` | Claude Code Stop hook: blocks (JSON `decision: block`) while edited covered code is unchecked; warns once when a passing check describes older code; never blocks twice in a row. | 0 |

Exit codes: 0 = holds, 1 = a property or check failed, 2 = tool or setup error.

## The manifest

```toml
[[check]]
id = "checkout-design"
rule = "A customer is never charged twice for one purchase."
harm = "The customer pays twice for one order."
level = "design"                                   # design = model of the design | code = real code
tool = "TLA+ / TLC"
tags = ["payments"]
run = "{verikit} tlc verification/checkout/Checkout.tla"
covers = ["src/Payments/**/*.cs", "web/src/app/checkout/**/*.ts"]   # backend AND frontend if it spans both
bounds = "1 order, up to 3 attempts"
assumes = ["The provider charges at most once per idempotency key."]
timeout = 900
synced_commit = ""                                 # written by `verikit sync`

  [[check.must_fail]]
  why = "Broken variant: a fresh idempotency key on every retry"
  run = "{verikit} tlc verification/checkout/Checkout.tla --const ReuseKey=FALSE"
  match = 'VIOLATION\] NeverChargedTwice'          # single quotes: TOML literal string, no escaping

  [[check.must_fail]]
  why = "Reachability witness: a charge can happen at all"
  run = "{verikit} tlc verification/checkout/Checkout.tla --inv NeverCharged"
  match = 'VIOLATION\] NeverCharged\b'

[[check]]
id = "checkout-api"
rule = "A customer is never charged twice for one purchase."
level = "code"
tool = "FsCheck in Payments.Tests"
run = "dotnet test tests/Payments.Tests --filter Category=Verification"
covers = ["src/Payments/**/*.cs"]                                   # the product code it describes
harness = ["tests/Payments.Tests/CheckoutVerificationTests.cs"]     # the check's own test files
  [[check.must_fail]]
  why = "Broken variant: key built per attempt"
  patch = "verification/payments/mutants/key-per-attempt.patch"   # relative to the folder holding verification/
  match = 'customer charged [2-9] times'
```

`{verikit}` expands to the runner, `{python}` to the interpreter. `run` is any shell command,
executed in the folder that holds `verification/`.

Frontend and backend in **separate repositories**: add `[sources.web] path = "../web-app"` and
cover `web:src/app/checkout/**/*.ts`; `sync` records that repo's commit too, and `stale`, `--ci`
and the hook report drift there. Details: [combining-with-tests](docs/combining-with-tests.md#frontend-and-backend-in-separate-repositories).

## Repository layout

```
verikit.py                 the runner (single file, stdlib only)
plugin/skills/             verify-survey, verify-create (+ routing, stacks/), verify-maintain
templates/                 tla/ starter, dotnet/ node/ react/ test skeletons, ci/ (GitHub, Azure), CODEOWNERS
examples/                  seven runnable examples, each with broken variants (see examples/README.md)
scripts/                   check_examples.py (all examples), e2e.py (monorepo + CI end-to-end),
                           check_ci_templates.py (CI templates against the official schemas)
tests/                     unit tests for the runner
docs/                      methodology, combining with tests, roadmap, troubleshooting, evidence/
AGENTS.md                  the agent guide
```

## Limits

- verikit cannot tell you the rule is the right rule. People review `verification/` (see
  [templates/CODEOWNERS.snippet](templates/CODEOWNERS.snippet)).
- A model checks the design, not the code; code checks and `stale` narrow that gap, they do
  not close it. Property tests sample: a pass is evidence, not proof.
- Across separate repositories verikit tracks drift only; broken-variant patches run in the
  repository that owns the check. Checks run one after another (no parallelism yet).
- See [docs/roadmap.md](docs/roadmap.md) for what is next.

## Credits and license

The method follows Lauren Tan's pstack verification skills (`cursor/plugins`, MIT); see
[NOTICE](NOTICE). TLA+ and TLC by Leslie Lamport and the TLA+ community. fast-check by
Nicolas Dubien, FsCheck by Kurt Schelfthout and contributors.

MIT - see [LICENSE](LICENSE).
