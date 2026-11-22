/**
 * Charge the customer for one order. If the payment provider is slow, give up on
 * that call after `timeoutMs` and retry, up to `maxRetries` times.
 *
 * Promise to customers: a customer is never charged twice for one purchase.
 */
export async function payForOrder(order, { gateway, timer, maxRetries = 2, timeoutMs = 5000 }) {
  // One key per purchase, reused by every retry, so the provider charges at most once.
  const idempotencyKey = `order-${order.id}`;
  for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
    const reply = gateway.charge({ customer: order.customer, amount: order.amount, idempotencyKey });
    const winner = await Promise.race([
      reply.then((receipt) => ({ kind: 'reply', receipt })),
      timer.wait(timeoutMs).then(() => ({ kind: 'timeout' })),
    ]);
    if (winner.kind === 'reply') return { status: 'paid', receipt: winner.receipt, attempts: attempt };
  }
  return { status: 'gave_up', attempts: maxRetries + 1 };
}
