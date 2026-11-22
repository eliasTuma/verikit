---
name: verify-maintain
description: Bring the checks in verification/verification.toml back in line after the code changed - find what changed since each check's sync commit, update the model or harness from the code, rerun, and say whether each failure is drift in the verification or a bug in the code. Use for /verify-maintain, "is the model still true to the code", or after merges and refactors that touched covered files.
---

# Maintain verified promises

A model checks the design it describes, not the code. When the code changes and the
model does not, the check keeps passing while it describes code that no longer exists.
That pass is the dangerous case, because it looks like proof.

Edit only verification files (models, configs, harnesses, property tests, mutant
patches, the manifest). Never edit product code here unless the user asks. Never weaken
a rule to make it pass.

## Steps

1. **Find what is stale.** `python verification/verikit.py stale` lists each check whose
   covered files changed since its `synced_commit`, with the files. `list` shows rule,
   bounds and assumptions. In a monorepo, each service folder that holds a `verification/`
   is its own root: run the commands from inside it (or pass `--root`).
2. **Run it as it is**, before changing anything: `python verification/verikit.py run <id>`.
   Record the result. A pass only says the old description still holds.
3. **Read every change to covered code:** `git log -p <synced_commit>..HEAD -- <covers>`.
   Look for moved or renamed code too, and new code that writes the state the rule guards
   (it may need adding to `covers`). Classify each change:
   - **no effect** on what the model describes (check against the model, not the commit message);
   - **shape changed** - a step split in two, a branch added, a guard moved: update the model;
   - **promise changed on purpose** - with evidence (docs, tests, changelog, commit message):
     update the rule, and record old wording, new wording and the evidence;
   - **promise may be broken** - code changed, no evidence the promise did: a failure is a bug candidate.
4. **Update from the code, not from the result.** Write the model change from the code
   before rerunning. Keep one indivisible code step as one model step. Keep the bounds
   unless the change makes them wrong, and report it if you shrink one.
5. **Rerun and judge.**
   - Pass: re-prove it can fail. Every `must_fail` must still fail; if the code gained a
     new guard, add a broken variant for that guard alone. A broken-variant patch that "does
     not apply" is verification drift too: regenerate it against the new code
     (`git diff --relative` from the folder holding `verification/`), never delete it.
   - Fail: replay the counterexample on the real code. A step the code cannot take means the
     model is wrong - fix it and say what was wrong. If every step can happen, it is a bug:
     name the commit that introduced it, tell the steps in the code's words, state the harm.
6. **Sync.** Commit the verification change on its own (separate from any product fix),
   then `python verification/verikit.py sync <id>` and commit the manifest. Do not sync a
   check that is blocked or failing.

## Report, per check

One outcome - **in sync**, **updated**, **bug**, or **blocked** (updated + bug is possible) -
plus the commits you read, what changed in the verification and why, the rerun result,
the broken variants it still catches, each bug in plain words, and what is still not covered.
If a check passed before your update and fails after, say plainly that the old pass was on a
stale description.
