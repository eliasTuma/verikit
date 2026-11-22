# Changelog

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
