# React

Status: **ran** with React 18, Vitest 2, jsdom, Testing Library 16 and fast-check 4, see the
kit's `examples/react-pay-button`. Jest works the same way (not exercised).

## What to verify in a frontend

The UI's logic, not its pixels: what can be sent, shown or stored given any order of user
events and server replies. Typical promises: pay sends one request however fast it is
clicked; a stale response never overwrites a newer one; an optimistic update is rolled back
when the server refuses; a form never submits half-filled data; a logged-out user never sees
cached private data.

## Where the checks go

In the app's existing component tests. Put `verification` in the test name:

```toml
run = "npx vitest run -t verification"
run = "npx jest -t verification"
```

## Pattern: clicks and replies in any order

```jsx
await fc.assert(fc.asyncProperty(
  fc.scheduler({ act: (f) => act(f) }),                    // React state flushes inside act
  fc.array(fc.integer({ min: 1, max: 2 }), { minLength: 1, maxLength: 4 }),  // clicks per burst
  async (s, bursts) => {
    const payments = [];
    const api = { pay: (id) => { payments.push(id); return s.schedule(Promise.resolve({}), `reply ${payments.length}`); } };
    render(<PayButton orderId="A-1001" api={api} />);
    const button = screen.getByRole('button');
    bursts.forEach((n, i) => s.schedule(Promise.resolve(), `burst ${i + 1}`)
      .then(() => { for (let k = 0; k < n; k++) fireEvent.click(button); }));   // same-tick clicks
    await s.waitIdle();
    cleanup();
    if (payments.length > 1) throw new Error(`order paid ${payments.length} times`);
  }), { numRuns: 200, seed, includeErrorInReport: true });
```

Generate **bursts** of same-tick events, not just single events. The example found that a
guard on React state (`if (status === 'paying') return`) still pays twice when two clicks
land before React re-renders. A `useRef` guard does not.

## Pitfalls

- Pass `act` to `fc.scheduler`, or state updates will not flush between scheduled tasks.
- Call `cleanup()` inside the property: each run renders a fresh component.
- With Vitest and `.jsx`, set `esbuild: { jsx: 'automatic' }` in `vitest.config.js` if the
  project has no React plugin configured.
- For flows that span pages and the real backend, write the design in TLA+ and use a
  Playwright test as an extra code check; Playwright is not exercised by the kit.
