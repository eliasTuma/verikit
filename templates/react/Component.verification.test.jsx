// Template: a React component verification test (Vitest or Jest + Testing Library + fast-check).
// Worked, runnable version: examples/react-pay-button/test/PayButton.verification.test.jsx
import { afterEach, test } from 'vitest';          // Jest: remove this line
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import fc from 'fast-check';
// import { TODO_Component } from '../src/TODO_Component.jsx';

afterEach(cleanup);
const seed = process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined;

test('verification: TODO rule, however user events and replies interleave', async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.scheduler({ act: (f) => act(f) }),
      fc.array(fc.integer({ min: 1, max: 2 }), { minLength: 1, maxLength: 4 }), // events per same-tick burst
      async (s, bursts) => {
        const requests = [];
        const api = { send: (x) => { requests.push(x); return s.schedule(Promise.resolve({}), `reply ${requests.length}`); } };
        // render(<TODO_Component api={api} />);
        // const target = screen.getByRole('button');
        bursts.forEach((n, i) => s.schedule(Promise.resolve(), `burst ${i + 1}`).then(() => {
          // for (let k = 0; k < n; k++) fireEvent.click(target);
        }));
        await s.waitIdle();
        cleanup();
        if (requests.length > 1) throw new Error(`TODO rule broken: ${requests.length} requests`);
      },
    ),
    { numRuns: 200, seed, includeErrorInReport: true },
  );
});
