# node-checkout

A Node `payForOrder()` that retries a payment when the provider is slow, verified three ways:

- **design:** `verification/checkout/Checkout.tla`, a TLA+ model where requests and replies
  arrive late and in any order, checked by `verikit tlc`;
- **code:** `test/checkout.race.test.js`, fast-check's scheduler driving the real function;
- **conformance:** `test/checkout.trace.test.js` records what the real function does (requests
  sent, charges made, the outcome) for 200 orderings, and `verikit trace` replays those traces
  against the model. If the code and the model disagree, it shows the first step the model
  cannot reach. Its two broken variants are the code with a per-attempt key, and the model with
  the wrong retry bound.

The code check's broken variant is a find-and-replace edit, and `[isolation]` in the manifest
lets `verikit run --isolated` apply it in a throwaway copy instead of your working files.

```sh
npm install
python <kit>/verikit.py init --no-skills      # once, inside a git repo
python verification/verikit.py run
python verification/verikit.py tlc verification/checkout/Checkout.tla --inv NoSilentCharge
```

The last command shows what the rule leaves out: after every timeout the app reports "gave up"
while the first charge still lands. The rule "never charged twice" holds; the promise "if we
say it failed, we did not take the money" does not. It is left open as an exercise: add a
pending state or a reconciliation step, make `NoSilentCharge` part of the main config, and add
its broken variant.
