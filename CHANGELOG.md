# Changelog

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
