// Records what the real payForOrder() does, so `verikit trace` can replay it against the design
// model (verification/checkout/Checkout.tla). It asserts nothing about the rule itself: its job
// is to write down, for many orderings, the values of the model's variables as the code runs.
//   attempt  - charge requests the client has sent so far
//   charges  - times the customer's card was charged
//   client   - "waiting" right after a request is sent; "paid" / "gave_up" when payForOrder returns
// The model is checked with MaxAttempts = 3, so the code runs with maxRetries = 2.
import test from 'node:test';
import fs from 'node:fs';
import fc from 'fast-check';
import { payForOrder } from '../src/checkout.js';

test('record traces of payForOrder() for the design model', async () => {
  const file = process.env.VERIKIT_TRACE_FILE; // set by `verikit trace --record`; unset in a normal test run
  await fc.assert(
    fc.asyncProperty(fc.scheduler(), async (s) => {
      const trace = [];
      const chargedKeys = new Set();
      let attempt = 0;
      let charges = 0;
      const observe = (extra = {}) => trace.push({ attempt, charges, ...extra });
      const gateway = {
        async charge(req) {
          attempt += 1;
          observe({ client: 'waiting' }); // model step: SendCharge
          await s.schedule(Promise.resolve(), `request ${attempt} reaches the provider`);
          if (!req.idempotencyKey || !chargedKeys.has(req.idempotencyKey)) {
            charges += 1;
            if (req.idempotencyKey) chargedKeys.add(req.idempotencyKey);
          }
          observe(); // model step: GatewayCharges
          await s.schedule(Promise.resolve(), 'a reply reaches the client');
          return { id: 'ch_1' };
        },
      };
      const timer = { wait: (ms) => s.schedule(Promise.resolve(), `timer (${ms}ms) rings`) };
      const order = { id: 'A-1001', customer: 'cus_1', amount: 4999 };
      const outcome = await s.waitFor(payForOrder(order, { gateway, timer, maxRetries: 2 }));
      observe({ client: outcome.status }); // model step: ClientGetsReply, or the last TimerFires
      await s.waitIdle(); // requests still in flight land late
      observe();
      if (file) fs.appendFileSync(file, JSON.stringify(trace) + '\n');
    }),
    { numRuns: 200, seed: process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined },
  );
});
