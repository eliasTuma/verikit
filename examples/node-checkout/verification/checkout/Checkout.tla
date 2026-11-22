------------------------------ MODULE Checkout ------------------------------
(***************************************************************************)
(* Design model of payForOrder() in src/checkout.js.                       *)
(*                                                                         *)
(* One order. The client sends a charge request, races it against a timer, *)
(* and on timeout retries (up to MaxAttempts requests in total). The       *)
(* network may deliver requests and replies late and in any order. The     *)
(* gateway charges once per idempotency key it has not seen before.        *)
(*                                                                         *)
(* Each action below is one indivisible step in the code; the comment on   *)
(* each names the line it stands for.                                      *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS
    MaxAttempts,   \* 1 + maxRetries in payForOrder()
    ReuseKey       \* TRUE  : one idempotency key per order, reused by every retry
                   \* FALSE : every attempt sends a different key (or none at all)

VARIABLES
    client,      \* "sending" | "waiting" | "paid" | "gave_up"  (where payForOrder is)
    attempt,     \* number of charge requests the client has sent so far
    toGateway,   \* attempt numbers whose request is still travelling to the gateway
    seenKeys,    \* idempotency keys the gateway has already charged for
    charges,     \* how many times the customer's card was actually charged
    toClient     \* attempt numbers whose reply is travelling back to the client

vars == <<client, attempt, toGateway, seenKeys, charges, toClient>>

KeyOf(n) == IF ReuseKey THEN 0 ELSE n   \* 0 = the order's key, n = a per-attempt key

Init ==
    /\ client = "sending"
    /\ attempt = 0
    /\ toGateway = {}
    /\ seenKeys = {}
    /\ charges = 0
    /\ toClient = {}

\* checkout.js: const reply = gateway.charge({ ..., idempotencyKey })
SendCharge ==
    /\ client = "sending"
    /\ attempt < MaxAttempts
    /\ attempt' = attempt + 1
    /\ toGateway' = toGateway \cup {attempt + 1}
    /\ client' = "waiting"
    /\ UNCHANGED <<seenKeys, charges, toClient>>

\* checkout.js: timer.wait() wins the Promise.race -> loop retries, or the loop ends
TimerFires ==
    /\ client = "waiting"
    /\ client' = IF attempt < MaxAttempts THEN "sending" ELSE "gave_up"
    /\ UNCHANGED <<attempt, toGateway, seenKeys, charges, toClient>>

\* the payment provider handles request n (assumed atomic per key, as providers document)
GatewayCharges(n) ==
    /\ n \in toGateway
    /\ toGateway' = toGateway \ {n}
    /\ charges' = IF KeyOf(n) \in seenKeys THEN charges ELSE charges + 1
    /\ seenKeys' = seenKeys \cup {KeyOf(n)}
    /\ toClient' = toClient \cup {n}
    /\ UNCHANGED <<client, attempt>>

\* checkout.js: reply wins the Promise.race. Only the CURRENT attempt's reply is
\* raced; a late reply to an earlier attempt resolves a promise nobody awaits.
ReplyArrives(n) ==
    /\ n \in toClient
    /\ client = "waiting"
    /\ n = attempt
    /\ toClient' = toClient \ {n}
    /\ client' = "paid"
    /\ UNCHANGED <<attempt, toGateway, seenKeys, charges>>

\* payForOrder() has returned and every request has reached the gateway: nothing left to do.
Finished ==
    /\ client \in {"paid", "gave_up"}
    /\ toGateway = {}
    /\ UNCHANGED vars

\* Named wrappers, so a counterexample says which step happened (not just "Next").
GatewayHandlesARequest == \E n \in toGateway : GatewayCharges(n)
ClientGetsReply        == \E n \in toClient  : ReplyArrives(n)

Next ==
    \/ SendCharge
    \/ TimerFires
    \/ GatewayHandlesARequest
    \/ ClientGetsReply
    \/ Finished

Spec == Init /\ [][Next]_vars

----------------------------------------------------------------------------
TypeOK ==
    /\ client \in {"sending", "waiting", "paid", "gave_up"}
    /\ attempt \in 0..MaxAttempts
    /\ toGateway \subseteq 1..MaxAttempts
    /\ toClient \subseteq 1..MaxAttempts
    /\ charges \in 0..MaxAttempts

\* THE RULE: a customer is never charged twice for one purchase.
NeverChargedTwice == charges <= 1

\* Non-vacuity witness. Checked on its own and EXPECTED TO FAIL: it proves the
\* model can actually charge the card, so NeverChargedTwice is not passing
\* only because nothing ever happens (the "typo, so nobody can pay" trap).
NeverCharged == charges = 0

\* A promise nobody wrote down at first: if we tell the customer the payment
\* failed, we did not take their money.
NoSilentCharge == client = "gave_up" => charges = 0
=============================================================================
