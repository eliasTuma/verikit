# Agent guide

You are an agent asked to add machine-checked promises to a repository, keep them honest,
or improve this kit. Read this file once, fully, before acting. The skills referenced here
are in `plugin/skills/`; after `init` they also live in the target repo's `.claude/skills/`.

## 0. Know which job you have

| The user asks... | Do |
|---|---|
| "set up verification in <repo>" | [Install](#1-install-into-a-target-repo), then [Survey](#2-survey) |
| "verify <promise>" / "prove <rule>" / "model-check <race>" | [Create](#3-create-one-check) (survey first if the repo is large and has no `verification/PLAN.md`) |
| "are the checks still right?", after merges or refactors | [Maintain](#4-maintain) |
| a Stop-hook message told you to run a check | [Daily loop](#5-the-daily-loop) |
| "improve / extend verikit" | [Taking it further](#8-taking-it-further) |

## 1. Install into a target repo

1. Clone the kit **outside** the target repo, at a short path (Windows: `C:\src\verikit`).
2. Decide the verification root: the repo root, or - in a monorepo - each service folder that
   owns its promises (`services/payments/`). Each root gets its own `verification/`.
3. In that folder:
   ```sh
   python <kit>/verikit.py init --hook --ci github      # --ci azure, --ci both, or no --ci
   python verification/verikit.py doctor
   ```
   In a monorepo, run `init --ci ...` in **each** root: the first writes the workflow, the next
   ones add their root to its `# verikit:roots` list. For Azure Repos, tell the user to add the
   pipeline as a *Build validation* branch policy (the YAML `pr:` trigger does not apply there).
   `init` copies the skills into `.claude/skills/`. A team that prefers the Claude Code plugin
   (`/plugin marketplace add eliasTuma/verikit`, then `/plugin install verikit@verikit`) can use
   `init --no-skills` instead; do not install both, or each skill appears twice.
4. Read `doctor`'s output: every project with its stack (.NET Framework vs .NET, legacy project
   format, React, Angular, Node backend) and test frameworks, plus missing tools. TLA+ needs
   Java 11+ or a running Docker; tell the user if neither exists.
5. Commit `verification/`, `.claude/`, the CI file and the `.gitignore` line in one commit of
   their own. Ask before pushing.

## 2. Survey

Follow `verify-survey` (`/verify-survey` in Claude Code). Output: `verification/PLAN.md`
with the top 3-10 promises ranked by harm x likelihood x (1 - test reach). **Stop and get the
user's approval of the plan** before creating any check.

## 3. Create one check

Follow `verify-create` (`/verify-create <promise>`), one promise at a time. Read the stack
note for every side the promise touches: `plugin/skills/verify-create/stacks/{dotnet,node,react,angular}.md`.

A promise that spans frontend and backend usually gets:
- one **design** check: a TLA+ model of the whole flow in `verification/<area>/`;
- one **code** check per side, written **inside that side's existing test project**, tagged
  `Verification` (or named `verification: ...`), selected by the manifest's `run`.

**Rules of one flow share one model and one test file; each rule keeps its own manifest
entry.** Before writing a model, look for one: `list --tag <flow>` and `verification/<flow>/`.
Adding a rule means one invariant, one witness, one test and one entry per level, then
`run --tag <flow>` and `sync --tag <flow>`, because the edit makes every check of that flow
out of date. Never one model per rule, never one model for the whole product. The full layout
is in `verify-create` (step 3) and in
[docs/combining-with-tests.md](docs/combining-with-tests.md#many-rules-one-model-and-one-test-file-per-flow).

## 4. Maintain

Follow `verify-maintain` (`/verify-maintain`). Start with `python verification/verikit.py stale`.
Each check ends as **in sync**, **updated**, **bug** or **blocked**. Put verification updates in
their own commit, separate from product fixes.

## 5. The daily loop

```sh
python verification/verikit.py run --changed           # checks whose covered code changed
python verification/verikit.py run <id> --seed <N>     # replay a failure with the seed it printed
python verification/verikit.py list                    # what exists, what is stale
python verification/verikit.py run <id> --isolated     # variants in a throwaway copy: your working files stay untouched
python verification/verikit.py report                  # every promise and its status, for a human
python verification/verikit.py run --tag <flow>        # every rule of one flow; then: sync --tag <flow>
```

- **A check fails on the current code:** it is a finding until proven otherwise. Replay the
  counterexample against the real code step by step. If every step can happen, report the bug
  in the code's words, with the harm. If a step cannot happen, the model is wrong: fix the model
  and say what was wrong.
- **A `must_fail` passes:** the check cannot see that break. Fix the check, not the variant.
- **A patch "does not apply":** the code moved; regenerate the patch (`git diff --relative`
  from the root), do not delete it.
- **The Stop hook blocked you:** run what it names. If a check passes but is stale, either
  re-confirm the model against the diff and `sync`, or say in your report that you did not.
  The hook records each check's content when your session starts, so it blocks only for
  what this session changed. Checks that were already out of date are listed once at the
  start of the session: leave them unless your task touches their code, and mention them.
  (Recorded live: [docs/evidence/live-stop-hook.md](docs/evidence/live-stop-hook.md).)
- **A property test failed with a random seed:** keep the case with
  `verikit seed add <id> <seed> --note "..."` and commit `verification/seeds/<id>.txt`; every
  later run replays it.
- **`TRACE REJECTED`:** the real code did something the model does not allow. Read the listed
  observations against the code and the model, and fix the one that is wrong.
- **In CI:** `run --ci` picks the PR's target branch from GitHub Actions, Azure Pipelines or
  GitLab and runs only the checks whose files changed; on a push, or a shallow clone, it runs all.

## 6. Hard rules

1. Never weaken a rule, a model, a bound or a `match` to make a check pass.
2. Never edit product logic while creating or maintaining checks, unless the user asks.
   Test files, test scripts, test project references and CI install steps needed to run the
   checks are part of the job; say in the hand-off which ones you changed.
3. Every check has at least one `must_fail`; every rule checked on a model has its own
   reachability witness (a model shared by five rules needs five); every guard has its own
   broken variant; every `must_fail` has a `match` that names the rule.
4. Never write or edit a sync record (`verification/sync/<id>.json`) by hand. Only
   `verikit sync` writes it, and only after a pass. Commit it with the change it confirms.
5. A rule is a product decision. Show the rule, the harm and the gaps to the user; changes to
   `verification/` should go through human review (CODEOWNERS).
6. Say what was **not** verified: bounds, assumptions, sampled vs exhaustive, sides not covered.
7. `verikit run` applies broken-variant patches to the working tree and reverts them. Do not
   run it while you have unrelated uncommitted edits in the same files.
8. Manifest `run` lines execute as shell commands. Never run verikit against a manifest you
   have not read.

## 7. Done checklist (for every new check)

- [ ] Rule, harm and gaps shown to the user, in plain words.
- [ ] Model actions / test steps each map to code a reviewer can open (comments name file + function).
- [ ] `python verification/verikit.py run <id>` shows `[ok] holds` and `[ok] must fail` for **every** variant.
- [ ] At least one variant per guard, plus a witness for every rule checked on a model.
- [ ] If the flow already had a model or test file, the rule was added to it (no second model),
      the entry carries the flow's tag and the same `rule` sentence at every level, and every
      check of the flow was rerun and synced (`run --tag <flow>`, `sync --tag <flow>`).
- [ ] Code checks live in the existing test projects and run in the team's normal test command too
      (check the test script: a file list needs the new file, or use a glob).
- [ ] The check's own test files are listed in `harness`; the product files it describes in `covers`.
- [ ] If the service can run as several instances, the model has more than one, or `assumes`
      says "single instance" and the user confirmed it.
- [ ] The CI workflow installs what the check needs (`npm ci` in the right folder, .NET SDK,
      Windows runner for `net4x`, Visual Studio/Build Tools for legacy projects) and its
      `# verikit:roots` list includes this root.
- [ ] A test filter that selects nothing cannot pass: you saw the check FAIL with its variant,
      and the run output names the verification test (MSTest filters on `TestCategory=`).
- [ ] `covers` is as narrow as the promise (files, or `file#Function`), not a whole busy folder.
- [ ] Broken variants in product code are find-and-replace `edits` where one place changes.
- [ ] `bounds` and `assumes` filled in honestly.
- [ ] `verikit sync <id>` run, and `verification/sync/<id>.json` committed with the check.
- [ ] Hand-off states: tool and why, bounds, result, variants caught, rerun command, what it leaves out.

## 8. Taking it further

### For a team adopting it (maturity levels)

| Level | You have | Next step |
|---|---|---|
| 1. First promise | One check with variants, run by hand | Add the CI workflow; add CODEOWNERS on `verification/` |
| 2. In the pipeline | CI runs `run --changed` on PRs, JUnit in the test tab | Add the Stop hook for agents; cover the top 3 promises from PLAN.md |
| 3. All three kinds | Top promises have a design model, code checks on each side, and a conformance check (`verikit trace`) tying them together | Schedule `/verify-maintain` (e.g. nightly) to catch drift; add mutation testing (Stryker.NET / StrykerJS) beside it |
| 4. Routine | New risky features get a promise in the same PR | Proofs (Lean/Dafny) for the core logic that must hold at every size; track counterexamples found as a metric |

Growing from a handful of promises to dozens: go flow by flow, one model and one test file per
flow, one manifest entry per rule. Not yet measured beyond a few rules; see
[the costs](docs/combining-with-tests.md#many-rules-one-model-and-one-test-file-per-flow).

### For an agent improving the kit itself

- Keep `verikit.py` a **single file using only the standard library**; it is vendored into
  every target repo.
- Before any change: `python -m unittest discover -s tests -v`. Before a PR, also
  `python scripts/check_examples.py` and `python scripts/e2e.py` (need Node, .NET 8, Java or Docker;
  Visual Studio or Build Tools for `dotnet-legacy`), and after CI template edits
  `python scripts/check_ci_templates.py` (needs `pip install pyyaml jsonschema`).
- A new stack = a runnable example in `examples/<name>/` with a `verification/verification.toml`,
  at least one patch variant and an `example.toml` (setup, needed tools, OS), a job in
  `.github/workflows/ci.yml` that names it (a named example that is SKIPPED fails), a
  `plugin/skills/verify-create/stacks/<name>.md`, and a row in the README support table.
  Until that CI job is green, label it "template, not run".
- Bump `VERSION` in `verikit.py` and `plugin/.claude-plugin/plugin.json`, and add a
  `CHANGELOG.md` entry. Target repos upgrade by re-running `init`.
- Open work, in priority order: [docs/roadmap.md](docs/roadmap.md).

## 9. Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md). The three you are most likely to hit:
.NET Framework tests "not available" (path too long), TLC "needs Java 11+ or Docker", and a
TOML error from a backslash in `match` (use single-quoted literal strings).
