// Redeems a coupon for an order: marks the coupon as used and records the discount.
//
// `db` is ONE database connection with a `query(text, params)` function, as node-postgres
// gives you: a `pg.Client`, or a client taken from a pool with `pool.connect()`. It must be
// one connection, because the statements below form one transaction.
//
// Several service instances run this at the same time, each on its own connection, so no
// lock or flag inside this process can keep the promise. The database has to.
export async function redeemCoupon(db, code, orderId) {
  await db.query('BEGIN');
  try {
    // The guard. One statement checks "still unused" and marks the coupon as used. If
    // another transaction is marking the same coupon, PostgreSQL makes this statement wait
    // for it, then looks at the row again: it is used by then, so no row comes back.
    const claimed = await db.query(
      `UPDATE coupons
          SET redeemed_by = $1, redeemed_at = now()
        WHERE code = $2 AND redeemed_by IS NULL
    RETURNING amount_cents`,
      [orderId, code],
    );
    if (claimed.rowCount === 0) {
      await db.query('ROLLBACK');
      return { redeemed: false, reason: 'coupon unknown or already redeemed' };
    }
    const amountCents = claimed.rows[0].amount_cents;
    await db.query(
      'INSERT INTO order_discounts (order_id, coupon_code, amount_cents) VALUES ($1, $2, $3)',
      [orderId, code, amountCents],
    );
    await db.query('COMMIT');
    return { redeemed: true, amountCents };
  } catch (err) {
    await db.query('ROLLBACK').catch(() => {});
    throw err;
  }
}
