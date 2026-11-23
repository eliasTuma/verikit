# Changelog

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
