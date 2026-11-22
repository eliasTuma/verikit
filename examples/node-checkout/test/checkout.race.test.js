// Code-level check of the SAME rule as verification/checkout/Checkout.tla, run on the
// real payForOrder(). fast-check's scheduler decides the order in which every request,
// reply and timer resolves, so each run is a different path through the tree.
import test from 'node:test';
import assert from 'node:assert/strict';
import fc from 'fast-check';
import { payForOrder } from '../src/checkout.js';

// A payment provider that, like real ones, charges once per idempotency key.
function fakeGateway(s) {
  const receiptsByKey = new Map();
  const charges = [];
  let requests = 0;
  return {
    charges,
    async charge(req) {
      const n = ++requests;
      await s.schedule(Promise.resolve(), `request ${n} reaches the provider`);
      let receipt = req.idempotencyKey && receiptsByKey.get(req.idempotencyKey);
      if (!receipt) {
        receipt = { id: `ch_${charges.length + 1}` };
        charges.push({ request: n, ...req });
        if (req.idempotencyKey) receiptsByKey.set(req.idempotencyKey, receipt);
      }
      await s.schedule(Promise.resolve(), `reply ${n} reaches the client`);
      return receipt;
    },
  };
}

const fakeTimer = (s) => ({ wait: (ms) => s.schedule(Promise.resolve(), `timer (${ms}ms) rings`) });

test('a customer is never charged twice for one purchase, in any order of events', async () => {
  await fc.assert(
    fc.asyncProperty(fc.scheduler(), fc.integer({ min: 0, max: 3 }), async (s, maxRetries) => {
      const gateway = fakeGateway(s);
      const order = { id: 'A-1001', customer: 'cus_1', amount: 4999 };
      const outcome = await s.waitFor(payForOrder(order, { gateway, timer: fakeTimer(s), maxRetries }));
      await s.waitIdle(); // let requests that are still in flight land too
      assert.ok(gateway.charges.length <= 1, `customer charged ${gateway.charges.length} times for one purchase`);
      if (outcome.status === 'paid') assert.equal(gateway.charges.length, 1, 'paid without a charge');
    }),
    { numRuns: 400, includeErrorInReport: true, seed: process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined },
  );
});
