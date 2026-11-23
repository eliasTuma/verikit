# Evidence: the hook in live Claude Code sessions

Recorded 2026-11-24 with Claude Code 2.1.319 (CLI, `-p --output-format stream-json
--include-hook-events`), model claude-sonnet-5-5, on Windows 11, with the 0.3.1 runner.
Fixture: a copy of `examples/node-checkout` as its own git repo, `verikit init --hook`, both
checks synced and committed. Local paths are shortened to `<fixture>`.

## Session A: the agent's own edit is blocked ($0.11)

Task given to the agent (nothing about verification):

> In src/checkout.js, rename the local variable `winner` to `outcome` everywhere in
> payForOrder. It is a pure rename with no behavior change. Make the edit, then finish.

| # | Event | Content |
|---|---|---|
| 1 | `hook_response` SessionStart | exit 0. Records each check's content for this session. Nothing is out of date, so it adds no context. |
| 2 | tool | `Grep` for `winner`, `Read src/checkout.js`, two `Edit`s |
| 3 | agent | "I renamed `winner` to `outcome` ... I didn't run any tests or the app." (tries to stop) |
| 4 | `hook_response` Stop | exit 0, stdout `{"decision": "block", "reason": "This session changed code that a formal check covers, and no passing run has seen these edits: - checkout-design ... -> cd <fixture> && python verification/verikit.py run checkout-design - checkout-code ... Run the check(s). If one fails, report the counterexample in plain words; do not weaken the rule or the model to make it pass."}` |
| 5 | fed back to the agent | "Stop hook feedback: This session changed code that a formal check covers ..." |
| 6 | tool | `Bash: python verification/verikit.py run checkout-design; ... run checkout-code` |
| 7 | result | checkout-design: `[ok] holds on the current code`, `[ok] must fail: Broken variant: every retry sends a different idempotency key`, `[ok] must fail: Reachability witness ...`; checkout-code: `[ok] holds`, `[ok] must fail: ... key is created inside the retry loop` |
| 8 | agent | "Both checks pass on the renamed code, so the rule 'A customer is never charged twice for one purchase' still holds ..." |
| 9 | `hook_response` Stop | exit 0, no block: the agent may stop (`stop_hook_active`) |

## Session B: drift that was already there is reported, not blamed ($0.07)

Before this session, a "teammate" commit changed `src/checkout.js` (more retries) without
re-syncing the checks, so both checks were stale on the branch. Task given to the agent:

> Add one sentence to README.md saying that amounts are in cents. Then finish.

| # | Event | Content |
|---|---|---|
| 1 | `hook_response` SessionStart | exit 0, stdout (added to the agent's context): "verikit: these machine-checked promises were already out of date before this session started (not your edits): - checkout-design (stale): ... - checkout-code (stale): ... If your task touches their code, follow the verify-maintain skill ... Otherwise leave them and mention them in your report." |
| 2 | tool | `Read README.md`, `Edit README.md` |
| 3 | agent | "I added 'All amounts are in cents.' to the end of `README.md`. ... The session-start hook reported two verikit checks as stale before I touched anything: `checkout-design` and `checkout-code` ... This change was documentation only, so I left them alone." |
| 4 | `hook_response` Stop | exit 0, no block |

With the 0.3.0 hook, session B's agent would have been blocked with "You changed code that a
formal check covers" although it had edited nothing a check covers.

## What this does not show

- The second-stage reminder ("these checks pass, but their sync record describes older
  content") appears on the next stop after a run. Session A did not reach it, because the hook
  never blocks twice in a row. Unit tests cover that path.
- These sessions ran with the hook command `python3 ... || python ...`. On this Windows machine
  `python3` is the Microsoft Store placeholder, which printed the word "Python" into the hook
  output. The released command probes quietly first (`if python3 -c "" ...; then ...; else ...;
  fi`). That form was run with the same payloads in Git Bash on Windows and in a Linux container
  that has only `python3`, but not in a live session.
- macOS was not tested.
