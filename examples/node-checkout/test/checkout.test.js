// Example-based tests: each one walks ONE path somebody thought of.
import test from 'node:test';
import assert from 'node:assert/strict';
import { payForOrder } from '../src/checkout.js';

const order = { id: 'A-1001', customer: 'cus_1', amount: 4999 };

test('a fast reply pays the order on the first attempt', async () => {
  const charges = [];
  const gateway = { charge: async (req) => { charges.push(req); return { id: 'ch_1' }; } };
  const timer = { wait: () => new Promise(() => {}) }; // never fires
  const result = await payForOrder(order, { gateway, timer });
  assert.equal(result.status, 'paid');
  assert.equal(charges.length, 1);
});

test('a dead first call is retried and the retry pays', async () => {
  let calls = 0;
  const gateway = { charge: () => (++calls === 1 ? new Promise(() => {}) : Promise.resolve({ id: 'ch_2' })) };
  let waits = 0;
  const timer = { wait: () => (++waits === 1 ? Promise.resolve() : new Promise(() => {})) };
  const result = await payForOrder(order, { gateway, timer });
  assert.equal(result.status, 'paid');
  assert.equal(result.attempts, 2);
});
