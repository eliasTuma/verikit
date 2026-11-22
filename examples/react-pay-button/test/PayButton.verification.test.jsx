// Code-level check: real component, real React, fake network. fast-check's scheduler decides
// when each burst of clicks happens and when each server reply arrives.
import { afterEach, expect, test } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import fc from 'fast-check';
import { PayButton } from '../src/PayButton.jsx';

afterEach(cleanup);
const seed = process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined;

test('verification: the order is paid at most once, however the clicks and replies interleave', async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.scheduler({ act: (f) => act(f) }),
      fc.array(fc.integer({ min: 1, max: 2 }), { minLength: 1, maxLength: 4 }), // clicks per burst
      async (s, bursts) => {
        const payments = [];
        const api = {
          pay: (orderId) => {
            payments.push(orderId);
            return s.schedule(Promise.resolve({ receipt: `r${payments.length}` }), `reply ${payments.length}`);
          },
        };
        render(<PayButton orderId="A-1001" api={api} />);
        const button = screen.getByRole('button');
        bursts.forEach((n, i) =>
          s.schedule(Promise.resolve(), `burst ${i + 1}: ${n} click(s)`).then(() => {
            for (let k = 0; k < n; k++) fireEvent.click(button); // n clicks in one tick
          }),
        );
        await s.waitIdle();
        cleanup();
        if (payments.length > 1) throw new Error(`order paid ${payments.length} times`);
      },
    ),
    { numRuns: 200, seed, includeErrorInReport: true },
  );
});
