# Changelog

## 0.4.1 - 2026-11-24

Guidance for products with many rules. Until now nothing told an agent how to go from a
handful of promises to dozens, so it would have written one model per rule.

- **One model and one test file per flow, one manifest entry per rule.** Layout, the steps for
  adding a rule to a flow, rules that span two flows, and the costs are in
  docs/combining-with-tests.md ("Many rules"). The `verify-create`, `verify-survey` and
  `verify-maintain` skills and AGENTS.md carry the procedure, because a target repository gets
  the skills but not the kit's docs. PLAN.md gains a Flow column and a "Later rounds" section.
- **A witness per rule**, not per model: a rule about a failed charge passes vacuously in a
  model where no charge fails. Hard rule 3 and the done checklist say so now.
- **`sync --tag <flow>`** re-confirms every rule of a flow in one command. A tag that matches
  nothing is an error, not a silent success.
- Unit tests pin what the layout relies on: an edit to a shared model makes every rule of that
  flow stale and no rule of another flow; the report keeps one row per rule.

Checked by hand on a copy of `examples/node-checkout` with two rules on one model. Not run
with dozens of rules; the roadmap says what is open.

Upgrading from 0.4.0: re-run `init --hook --force` to replace the installed skills.

## 0.4.0 - 2026-11-24

Aimed at what makes a team stop using a tool like this in its first month: noise, brittle
variants, slow or intrusive runs, results nobody reads, and a model that drifts from the code.

- **Function-level covers:** `covers = ["src/CheckoutService.cs#PayForOrder"]` watches one
  declaration (overloads and the attributes or decorators above it included; brace languages
  and Python; `verikit:begin NAME` / `verikit:end NAME` comments for anything else). Edits
  elsewhere in the file are not drift. A name that is not found is loud: `stale` says
  "anchor not found" and `sync` refuses.
- **Find-and-replace broken variants:** `edits = [{ file, find, replace }]`. `find` must match
  exactly once; line endings are kept; files are restored from byte-exact backups. Unlike a
  diff, it keeps applying when code around the guard changes.
- **Saved seeds:** a failed run prints `verikit seed add <id> <seed>`; `run` replays every seed in
  `verification/seeds/<id>.txt` as its own step. Both CI templates run nightly with new random
  seeds.
- **`report` and `run --summary`:** every promise with its checks and sync status as Markdown
  or HTML; a run summary with the failing step and the replay command, published to the
  GitHub job summary or the Extensions tab of an Azure run with `--ci`. A passing check whose
  sync record is stale, missing or unknown is shown as a warning, never as plain green.
- **`run --isolated [--jobs N]`:** broken variants are applied and run in a throwaway copy of
  the repository, so working files are never edited. Variants of different checks run side by
  side; "holds on the current code" stays in the real tree. `[isolation] link` links
  git-ignored dependency folders into the copy; deleting the copy never follows a link. All
  eight examples pass with `--isolated` on Windows.
- **`verikit trace`:** traces recorded from the real code in tests are replayed against the
  TLA+ model (TLC) or a Python model; the first observation the model cannot reach is
  reported. `examples/node-checkout` gains a conformance check; in the end-to-end test it goes
  red on the regression that the design check misses.
- **`examples/node-postgres`:** a coupon is redeemed at most once across two service
  instances, checked with two real PostgreSQL connections whose SQL statements fast-check
  schedules. Ran on PostgreSQL 16 only.
- README states plainly that the kit has not yet been used on a large production codebase.

Fixes:
- **Sync records no longer include build output.** A `run` line that names a built file (a
  test dll, as in the legacy .NET example) put that file in the record, so in 0.3.1 `sync`
  failed there with "covered files changed while the check was running". Records now hold only
  files git tracks or would track. `check_examples.py` runs `sync`, then `stale --strict` on a
  tree with every git-ignored file removed, for every example.
- `sync` with no ids syncs every check.
- `init` says when an installed skill differs from the kit's version, instead of silently
  keeping the old text.

**Upgrading from 0.3.x:** re-run `init --hook --force` in each verification root (`--force`
replaces the installed skills, which described the old commit-based sync), then
`python verification/verikit.py sync` once and commit `verification/sync/`.

## 0.3.1 - 2026-11-24

Two bugs that broke the kit in an ordinary team workflow, plus one portability bug. Upgrade by
re-running `init --hook --force` in each repo (`--force` also replaces the installed skills,
which described the old flow), then `verikit sync <id>` once per check. Known issue, fixed in
0.4.0: `sync` fails when a check's `run` line names a build output file.

- **Sync no longer depends on a commit staying reachable.** 0.3 stored a commit id per check;
  a squash merge, a rebase or a shallow clone made it unreachable, so every check showed as
  `unknown` in CI and for teammates. `sync` now records the *content* it confirmed, in
  `verification/sync/<id>.json` (one small file per check: it merges without conflicts, sits
  under CODEOWNERS, and stores per-file digests so `stale` can still name what changed).
  Line endings are normalised, git-ignored build output is not counted, wording edits in the
  manifest are not drift, and edits to the check's own model or mutants are. `sync` no longer
  rewrites the manifest and no longer needs a clean tree: sync, then commit once. `stale` prints
  a `review:` commit that is always in the branch's history. 0.3 manifests are still read; an
  unreachable `synced_commit` is `unknown` with the command that fixes it. Cross-repo checks
  record content the same way (`synced_sources` is gone).
- **The Stop hook blames only its own session.** It used to say "You changed code" to an agent
  that had edited nothing, whenever a check was already stale (a teammate's merge, or the bug
  above). `init --hook` now also registers SessionStart, which records each check's content
  when the session begins; Stop blocks only for what changed since. Checks that were already
  out of date are listed once at session start. Without a baseline the hook still reports
  drift, worded as "this session's edits or an earlier change". An edit to a never-synced
  check's code is now blocked too.
- **Hook command works where only `python3` exists** (macOS, many Linux machines): it tries
  `python3`, then `python`. The 0.3.0 command failed there with "python: not found". Checked
  in a python3-only Linux container and in Git Bash on Windows. A clear message replaces the
  import error on Python older than 3.11. `init --hook` upgrades an existing 0.3 entry in place.

## 0.3.0 - 2026-11-23

New examples, all run through `verikit run` with every broken variant caught:
- `examples/dotnet-legacy`: a non-SDK .NET Framework 4.8 solution (packages.config, MSTest v2,
  FsCheck), restored and built with MSBuild and run with vstest.console via `legacy_test.py`.
- `examples/dotnet-coyote`: Microsoft Coyote 1.7.11 explores real `async` interleavings of a
  gift-card redemption (net8.0, binary rewriting after build); two broken variants.
- `examples/angular-testbed`: an Angular 20 workspace, `ng test` with Karma + Jasmine and
  headless Chrome, TestBed + `HttpTestingController` + fast-check, asserted on the DOM.
  Replaces the untested `templates/angular/`.

CI, for GitHub Actions and Azure Pipelines:
- `run --ci`: in a pull-request build (GitHub Actions; Azure Pipelines on Azure Repos or GitHub
  repos; GitLab) run only the checks whose files changed vs the target branch; otherwise all.
  A missing or shallow base runs every check, never none.
- `init --ci github,azure` (or `both`). Running `init --ci` in another monorepo root adds that root
  to the workflow's `# verikit:roots` list. GitHub: one matrix job per root. Azure: one job runs
  every root and publishes JUnit to the Tests tab; JDK 21 via `JavaToolInstaller`; tla2tools cache.
- `scripts/check_ci_templates.py` validates both templates against the official schemas; the
  kit's CI also runs actionlint.

Runner:
- Cross-repo drift: `[sources.<name>] path = "../other-repo"` and `name:glob` entries in `covers`
  and `harness`. `sync` records that repo's commit (`synced_sources`); `stale`, `--changed`, `--ci`
  and the hook report drift there. A missing checkout is `unknown` (fails `stale --strict`) and
  does not block the hook on its own. `VERIKIT_SOURCE_<NAME>` overrides the path.
- Broken-variant patches are reverted by restoring byte-exact backups (also after an
  interrupted run). Fixes `could not revert` when `core.autocrlf=true` turned LF files into CRLF.
- Stop hook blocks with Claude Code's JSON `decision`/`reason` (exit 0) instead of exit 2.
- `doctor`: legacy projects route to MSBuild + vstest (never `dotnet test`, which runs nothing
  there and exits 0), packages.config is read, vswhere is reported, long paths warned.

Tested: the Stop hook in a live Claude Code session (docs/evidence/live-stop-hook.md); the
plugin installed from GitHub with `claude plugin marketplace add` + `install` in an isolated
config. `check_examples.py` now reads `examples/<name>/example.toml`, and an example named on
the command line that is SKIPPED fails the run.

Docs: MSTest filters on `TestCategory=` (a wrong property passes silently); legacy, Coyote and
Angular TestBed stack notes; Azure Repos needs a Build validation branch policy for PRs.

## 0.2.1 - 2026-11-23

Fixes from the first fresh-agent acceptance test (an agent given only AGENTS.md added a
verified promise to a new repo, and found two real bugs in that repo's code with it).

- New `harness` field: the check's own test files. Editing them makes the check stale, selects
  it in `run --changed` and wakes the Stop hook, so a weakened assertion cannot slip through.
- Fingerprints cover only what decides a check (covered code, harness, patches, its
  `verification/<area>/` folder), not the whole `verification/` folder or the manifest:
  editing one check no longer marks every check as changed.
- `sync` records its passing rerun; `list` no longer says "code changed since" right after a sync.
- Line endings: `init` writes LF on every OS and appends to `.gitignore` in the file's own
  style; `sync` rewrites one line and keeps the manifest's line endings.
- `doctor` routes plain Node/TypeScript packages to fast-check and points at the installed
  skill paths.
- Docs: design vs code check vs both; in-memory guards protect one process (model instances);
  what counts as a guard; test/CI configuration edits are part of the job; `npm test` must run
  the verification file (Node 22 accepts globs); CI install steps; accurate wording on how TLC
  labels trace steps.

## 0.2.0 - 2026-11-23 (initial public version)

- Monorepo support: the nearest folder holding `verification/verification.toml` is the root;
  broken-variant patches are applied from the git top level with `--directory`, and the run
  confirms the patch really changed the files (a subfolder `git apply` silently skips them).
- Patch safety: each applied patch is recorded in `.verikit/` and reverted on the next start
  if a run was interrupted.
- `run --changed [REF]`, `--tag`, `--junit` (JUnit XML for CI test reports), `--seed`
  (exported as `VERIKIT_SEED`), per-check `timeout`.
- `tlc --const NAME=VALUE --inv NAME`: variants and witnesses without copied `.cfg` files.
- TLC warnings are surfaced; an action that changes a variable it declares UNCHANGED makes a
  pass an error (vacuous pass, cf. cursor/plugins#1235).
- `must_fail` requires `match`; patch files are validated when the manifest loads.
- `doctor` scans recursively and tells .NET Framework from .NET, legacy project format, React,
  Angular and Node backends, and lists test frameworks.
- Stop hook: scans monorepo roots, blocks committed or uncommitted unchecked edits, warns once
  when a passing check is stale, never blocks twice in a row.
- New `verify-survey` skill; stack notes for .NET, Node, React and Angular; four runnable
  examples; GitHub Actions and Azure Pipelines templates; unit and end-to-end tests.

## 0.1.0 - 2026-11-23 (prototype, not published)

- Runner with `run`, `stale`, `sync`, `list`, `tlc`, `explore`, `doctor`, `init`, `hook`;
  `verify-create` and `verify-maintain` skills; Node checkout demo.
