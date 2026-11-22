"""The checkout design from examples/checkout-demo/verification/checkout/Checkout.tla,
written for `verikit explore` (zero-install fallback when neither Java nor Docker exists).

    python verikit.py explore examples/checkout_model.py                        # holds
    python verikit.py explore examples/checkout_model.py --const ReuseKey=False # double charge
    python verikit.py explore examples/checkout_model.py --inv NeverCharged     # witness: must fail
    python verikit.py explore examples/checkout_model.py --inv NoSilentCharge   # the gap

A model is: CONSTANTS, init() -> states, next(state) -> (action, state) pairs, INVARIANTS.
States are dicts of hashable values (use frozenset for sets). The explorer walks every
reachable state breadth-first, so the first violation it reports has the shortest path.
"""

CONSTANTS = {"MaxAttempts": 3, "ReuseKey": True}
C = CONSTANTS


def key_of(n):
    return 0 if C["ReuseKey"] else n


def init():
    yield dict(client="sending", attempt=0, to_gateway=frozenset(), seen_keys=frozenset(),
               charges=0, to_client=frozenset())


def next(s):  # noqa: A001 - mirrors TLA+'s Next
    if s["client"] == "sending" and s["attempt"] < C["MaxAttempts"]:  # gateway.charge(...)
        n = s["attempt"] + 1
        yield "SendCharge", {**s, "attempt": n, "to_gateway": s["to_gateway"] | {n}, "client": "waiting"}
    if s["client"] == "waiting":  # the timer wins Promise.race
        yield "TimerFires", {**s, "client": "sending" if s["attempt"] < C["MaxAttempts"] else "gave_up"}
    for n in sorted(s["to_gateway"]):  # the provider handles request n, deduplicating by key
        k = key_of(n)
        yield f"GatewayHandles({n})", {**s, "to_gateway": s["to_gateway"] - {n},
                                        "charges": s["charges"] + (0 if k in s["seen_keys"] else 1),
                                        "seen_keys": s["seen_keys"] | {k}, "to_client": s["to_client"] | {n}}
    for n in sorted(s["to_client"]):  # only the current attempt's reply is raced
        if s["client"] == "waiting" and n == s["attempt"]:
            yield f"ClientGetsReply({n})", {**s, "to_client": s["to_client"] - {n}, "client": "paid"}


def terminal(s):  # finished states are not deadlocks
    return s["client"] in ("paid", "gave_up") and not s["to_gateway"]


INVARIANTS = {
    "NeverChargedTwice": lambda s: s["charges"] <= 1,
}
WITNESSES = {  # select with --inv; each must FAIL to prove the model can do the thing
    "NeverCharged": lambda s: s["charges"] == 0,
    "NoSilentCharge": lambda s: s["client"] != "gave_up" or s["charges"] == 0,
}
