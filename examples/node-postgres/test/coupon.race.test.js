// verification: a coupon is redeemed at most once, even when two service instances handle
// requests for it at the same time.
//
// The real redeemCoupon() (src/coupons.js) runs twice against a real PostgreSQL, each call
// on its OWN connection, like two instances of the service. fast-check's scheduler decides
// which connection sends its next SQL statement, so every run is one interleaving of real
// statements (test/support/scheduled-sql.js explains how, and what happens when a
// statement has to wait for a row lock). The result is read from the committed rows.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fc from 'fast-check';
import { redeemCoupon } from '../src/coupons.js';
import { openTestDatabase } from './support/testdb.js';
import { scheduledSql } from './support/scheduled-sql.js';

const RUNS = 200;
const seed = process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined;

let db;
before(async () => {
  db = await openTestDatabase({ connections: 2 });
});
after(async () => {
  await db?.close();
});

test('verification: a coupon is redeemed at most once by two service instances', { timeout: 150_000 }, async () => {
  const [clientA, clientB] = db.instances;
  const sql = scheduledSql(db.observer);
  const seen = { orders: new Set(), lockWaits: 0, wins: { A: 0, B: 0 } };
  let run = 0;

  await fc.assert(
    fc.asyncProperty(fc.scheduler({ act: sql.act }), async (s) => {
      run += 1;
      // A clean start for every run: no transaction left open, and a coupon nobody has seen.
      await Promise.all([clientA.query('ROLLBACK'), clientB.query('ROLLBACK')]);
      const code = `SAVE10-${run}`;
      await db.observer.query('INSERT INTO coupons (code, amount_cents) VALUES ($1, 1000)', [code]);
      const orders = { A: `order-A-${run}`, B: `order-B-${run}` };

      sql.start(s);
      const outcomes = await s.waitFor(
        Promise.allSettled([
          redeemCoupon(sql.connection('A', clientA), code, orders.A),
          redeemCoupon(sql.connection('B', clientB), code, orders.B),
        ]),
      );
      const trace = `\nSQL statements, in the order they were sent:\n  -> ${sql.order()}`;

      // Both calls must come back with an answer. A call that throws redeems nothing, and
      // "0 redemptions" must never count as "at most once holds".
      for (const [i, name] of ['A', 'B'].entries()) {
        assert.equal(outcomes[i].status, 'fulfilled', `instance ${name}: redeemCoupon threw: ${outcomes[i].reason?.message}${trace}`);
      }

      // The truth is what was committed, read on a third connection.
      const discounts = (
        await db.observer.query('SELECT order_id FROM order_discounts WHERE coupon_code = $1 ORDER BY order_id', [code])
      ).rows.map((row) => row.order_id);
      assert.ok(
        discounts.length <= 1,
        `coupon redeemed ${discounts.length} times (${code}: ${discounts.join(', ')}); ` +
          `rule: a coupon is redeemed at most once.${trace}`,
      );
      assert.equal(discounts.length, 1, `coupon redeemed 0 times: ${code} was unused and two orders asked for it.${trace}`);

      // What each instance told its caller must match the committed rows.
      const saidYes = ['A', 'B'].filter((_, i) => outcomes[i].value.redeemed);
      assert.deepEqual(saidYes.map((name) => orders[name]), discounts, `answers and committed rows differ.${trace}`);
      const coupon = (await db.observer.query('SELECT redeemed_by FROM coupons WHERE code = $1', [code])).rows[0];
      assert.equal(coupon.redeemed_by, discounts[0], `the coupon row names another order than the discount.${trace}`);

      seen.orders.add(sql.sent().map((e) => `${e.name}:${e.sql.split(' ')[0]}`).join(' '));
      if (sql.sent().some((e) => e.waitedForLock)) seen.lockWaits += 1;
      seen.wins[saidYes[0]] += 1;
    }),
    { numRuns: RUNS, seed, includeErrorInReport: true },
  );

  // The check must not pass for a hollow reason: the two connections really interleaved,
  // a statement really waited for the other connection's row lock, and each side won.
  console.log(
    `[coupon-race] PostgreSQL ${db.serverVersion} (${db.isolation}); ${run} schedules, ` +
      `${seen.orders.size} different statement orders, ${seen.lockWaits} with a statement waiting for a row lock; ` +
      `A won ${seen.wins.A}, B won ${seen.wins.B}`,
  );
  assert.equal(run, RUNS, `expected ${RUNS} schedules, ran ${run}`);
  assert.ok(seen.orders.size > 2, `only ${seen.orders.size} statement orders in ${run} runs: the scheduler is not interleaving the two connections`);
  assert.ok(seen.lockWaits > 0, `no statement ever waited for a row lock in ${run} runs: the two connections did not meet on the same row`);
  assert.ok(seen.wins.A > 0 && seen.wins.B > 0, `one instance won every time (A ${seen.wins.A}, B ${seen.wins.B})`);
});
