# Node backend

Status: **ran** with fast-check 4.11 on Node 22 (`node --test`) together with a TLA+ model,
see the kit's `examples/node-checkout`. Vitest and Jest work the same way (Vitest exercised
in the React and Angular examples).

## Where the checks go

In the service's existing test folder and runner. Name or tag verification tests so they
can run alone:

```toml
run = "node --test test/checkout.race.test.js"
run = "npx vitest run -t verification"
run = "npx jest -t verification"
```

## Which tool

| Question | Tool |
|---|---|
| Bad order of steps (retries, webhooks delivered twice, queues, sagas) | TLA+ model + `verikit tlc` |
| Real async interleavings of the handler code | **fast-check `fc.scheduler()`**: wrap every boundary promise (HTTP call, DB call, timer) in `s.schedule(...)`; `await s.waitFor(promise)` then `await s.waitIdle()` |
| Generated inputs for pure logic (pricing, parsing, permissions) | fast-check `fc.property` / `fc.asyncProperty` |

## Pattern

```js
const seed = process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined;
await fc.assert(fc.asyncProperty(fc.scheduler(), async (s) => {
  const gateway = fakeGateway(s);                // every request and reply goes through s.schedule
  const timer = { wait: (ms) => s.schedule(Promise.resolve(), `timer ${ms}ms`) };
  await s.waitFor(payForOrder(order, { gateway, timer }));
  await s.waitIdle();                            // requests still in flight land too
  assert.ok(gateway.charges.length <= 1, `customer charged ${gateway.charges.length} times`);
}), { numRuns: 400, seed, includeErrorInReport: true });
```

`includeErrorInReport: true` puts the assertion message in the report, so `match` finds it.
Inject boundaries (gateway, clock, DB) as parameters; if the code imports them directly,
add a seam first and say so in the hand-off.

## Pitfalls

- Use `s.waitIdle()` (fast-check 4.2+); `waitAll` is deprecated.
- Real timers and real network calls escape the scheduler and make runs non-reproducible.
- Node 22's `node --test` does not accept a directory argument; list the files.
