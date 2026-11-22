------------------------------ MODULE Starter ------------------------------
(***************************************************************************)
(* Skeleton for a design model checked by `verikit tlc`. Copy it to        *)
(* verification/<area>/<Name>.tla, rename the module to match the file,    *)
(* and replace every TODO with this repo's real steps and names.           *)
(* A complete, worked model: examples/node-checkout/.../Checkout.tla       *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS
    MaxAttempts,  \* TODO: bound from the code, e.g. 1 + maxRetries (keep it small: 2-3)
    Fixed         \* TRUE = the guard as the code has it; FALSE = broken variant (--const Fixed=FALSE)

VARIABLES
    client,       \* TODO: where the caller is: "sending" | "waiting" | "done"
    attempt,      \* TODO: how many requests the caller has sent
    inFlight,     \* TODO: requests travelling to the other side (a SET, so they land in any order)
    handled,      \* TODO: has the other side already done the work for this request key?
    effects       \* TODO: what the promise is about (charges, emails sent, rows written...)

vars == <<client, attempt, inFlight, handled, effects>>

Init ==
    /\ client = "sending"
    /\ attempt = 0
    /\ inFlight = {}
    /\ handled = FALSE
    /\ effects = 0

\* TODO: one action per indivisible step in the code; name the file/function above each.
\* src/...: send a request
Send ==
    /\ client = "sending"
    /\ attempt < MaxAttempts
    /\ attempt' = attempt + 1
    /\ inFlight' = inFlight \cup {attempt + 1}
    /\ client' = "waiting"
    /\ UNCHANGED <<handled, effects>>

\* src/...: the timer fires before a reply: retry, or give up
TimeOut ==
    /\ client = "waiting"
    /\ client' = IF attempt < MaxAttempts THEN "sending" ELSE "done"
    /\ UNCHANGED <<attempt, inFlight, handled, effects>>

\* the other side handles request n (assumed atomic - write that in the manifest's `assumes`).
\* Fixed = the guard the code relies on (e.g. an idempotency key): repeat requests have no effect.
Handle(n) ==
    /\ n \in inFlight
    /\ inFlight' = inFlight \ {n}
    /\ effects' = IF Fixed /\ handled THEN effects ELSE effects + 1
    /\ handled' = TRUE
    /\ UNCHANGED <<client, attempt>>
HandleARequest == \E n \in inFlight : Handle(n)

\* src/...: a reply arrives while the caller is still waiting
Reply ==
    /\ client = "waiting"
    /\ handled
    /\ client' = "done"
    /\ UNCHANGED <<attempt, inFlight, handled, effects>>

\* nothing left to do: finished states stutter instead of being reported as deadlocks
Done ==
    /\ client = "done" /\ inFlight = {}
    /\ UNCHANGED vars

Next == Send \/ TimeOut \/ HandleARequest \/ Reply \/ Done   \* NAMED actions, so traces read well

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ client \in {"sending", "waiting", "done"}
    /\ attempt \in 0..MaxAttempts
    /\ effects \in 0..MaxAttempts

\* THE RULE, in the words of the manifest's `rule`
Rule == effects <= 1

\* Reachability witness: must FAIL (--inv Witness), proving the effect can happen at all
Witness == effects = 0
=============================================================================
