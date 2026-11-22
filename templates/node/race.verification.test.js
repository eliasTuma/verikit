// Template: a code-level verification test for a Node backend (node:test + fast-check).
// Worked, runnable version: examples/node-checkout/test/checkout.race.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import fc from 'fast-check';
// import { handler } from '../src/TODO.js';

const seed = process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined;

test('verification: TODO rule in plain words, in any order of events', async () => {
  await fc.assert(
    fc.asyncProperty(fc.scheduler(), async (s) => {
      // Route EVERY boundary promise through s.schedule so fast-check picks the order:
      const effects = [];
      const external = { call: (req) => { effects.push(req); return s.schedule(Promise.resolve({ ok: true }), `reply ${effects.length}`); } };
      const timer = { wait: (ms) => s.schedule(Promise.resolve(), `timer ${ms}ms`) };
      // const result = await s.waitFor(handler(input, { external, timer }));
      await s.waitIdle(); // let anything still in flight land
      assert.ok(effects.length <= 1, `TODO rule broken: ${effects.length} effects`);
    }),
    { numRuns: 400, seed, includeErrorInReport: true },
  );
});
