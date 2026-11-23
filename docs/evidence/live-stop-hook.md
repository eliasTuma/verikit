# Evidence: the Stop hook in a live Claude Code session

Recorded 2026-11-23 with Claude Code 2.1.319 (CLI, `-p --output-format stream-json
--include-hook-events`), model claude-sonnet-5-5, on Windows 11. Fixture: a copy of
`examples/node-checkout` as its own git repo, `verikit init --hook`, both checks synced and
committed. Local paths are shortened to `<fixture>`. Cost of the session: $0.11.

Task given to the agent (nothing about verification):

> In src/checkout.js, rename the local variable `winner` to `outcome` everywhere in
> payForOrder. It is a pure rename with no behavior change. Make the edit, then finish.

What happened, from the event stream:

| # | Event | Content |
|---|---|---|
| 1 | tool | `Grep` for `winner`, `Read src/checkout.js`, two `Edit`s |
| 2 | agent | "I renamed `winner` to `outcome` ... I didn't run the tests." (tries to stop) |
| 3 | `hook_response` Stop | exit 0, stdout `{"decision": "block", "reason": "You changed code that a formal check covers, and no passing run has seen these edits: - checkout-design ... -> cd <fixture> && python verification/verikit.py run checkout-design - checkout-code ... Run the check(s). If one fails, report the counterexample in plain words; do not weaken the rule or the model to make it pass."}` |
| 4 | fed back to the agent | "Stop hook feedback: You changed code that a formal check covers ..." |
| 5 | tool | `Bash: cd <fixture> && python verification/verikit.py run checkout-design` (and `checkout-code`) |
| 6 | result | checkout-design: `[ok] holds on the current code`, `[ok] must fail: Broken variant: every retry sends a different idempotency key`, `[ok] must fail: Reachability witness ...`; checkout-code: `[ok] holds`, `[ok] must fail: ... key is created inside the retry loop` |
| 7 | agent | "Both checks covering `payForOrder` passed after the rename, so 'a customer is never charged twice for one purchase' still holds ..." |
| 8 | `hook_response` Stop | exit 0, no output: the agent may stop (`stop_hook_active`) |

An earlier run of the same task with the 0.2 hook (exit 2 + stderr) behaved the same way.
Either form makes Claude Code show the user a short "Stop hook" notice while it blocks; 0.3
uses the documented JSON `decision`/`reason` form.

What this does not show: the second-stage warning ("these checks pass but describe the code at
their sync commit") appears on the next stop after a run, which this session did not reach,
because the hook never blocks twice in a row. Unit tests cover that path.
