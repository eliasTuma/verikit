import { useRef, useState } from 'react';

/**
 * Pays for one order. Promise: one click or many, the order is paid at most once.
 * `api.pay(orderId)` returns a promise that resolves with a receipt.
 */
export function PayButton({ orderId, api }) {
  const [status, setStatus] = useState('idle'); // idle | paying | paid | failed
  const inFlight = useRef(false);

  async function onClick() {
    // A ref, not state: two clicks in the same tick both see the old state before React re-renders.
    if (inFlight.current) return;
    inFlight.current = true;
    setStatus('paying');
    try {
      await api.pay(orderId);
      setStatus('paid');
    } catch {
      inFlight.current = false;
      setStatus('failed');
    }
  }

  const label = { idle: 'Pay', paying: 'Paying...', paid: 'Paid', failed: 'Try again' }[status];
  return (
    <button type="button" onClick={onClick} disabled={status === 'paying' || status === 'paid'}>
      {label}
    </button>
  );
}
