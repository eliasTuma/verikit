// The team's ordinary example tests: one connection, one request at a time.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { redeemCoupon } from '../src/coupons.js';
import { openTestDatabase } from './support/testdb.js';

let db;
let client;
before(async () => {
  db = await openTestDatabase({ connections: 1 });
  client = db.instances[0];
});
after(async () => {
  await db?.close();
});

const discountsFor = async (code) =>
  (await db.observer.query('SELECT order_id, amount_cents FROM order_discounts WHERE coupon_code = $1', [code])).rows;

test('an unused coupon gives the order its discount', async () => {
  await db.observer.query("INSERT INTO coupons (code, amount_cents) VALUES ('WELCOME', 500)");
  assert.deepEqual(await redeemCoupon(client, 'WELCOME', 'order-1'), { redeemed: true, amountCents: 500 });
  assert.deepEqual(await discountsFor('WELCOME'), [{ order_id: 'order-1', amount_cents: 500 }]);
});

test('a used coupon is refused for the next order', async () => {
  await db.observer.query("INSERT INTO coupons (code, amount_cents) VALUES ('ONCE', 700)");
  assert.equal((await redeemCoupon(client, 'ONCE', 'order-2')).redeemed, true);
  assert.equal((await redeemCoupon(client, 'ONCE', 'order-3')).redeemed, false);
  assert.deepEqual(await discountsFor('ONCE'), [{ order_id: 'order-2', amount_cents: 700 }]);
});

test('an unknown coupon is refused', async () => {
  assert.equal((await redeemCoupon(client, 'NO-SUCH-CODE', 'order-4')).redeemed, false);
  assert.deepEqual(await discountsFor('NO-SUCH-CODE'), []);
});
