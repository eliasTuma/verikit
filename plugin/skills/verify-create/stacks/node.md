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
- Node 22's `node --test` does not accept a bare directory, but it does accept a glob:
  `node --test "test/*.test.js"` (checked on Node 22.13). If the project's `npm test` lists
  files one by one, add the verification file or switch to a glob, so the team's normal
  test run includes the check.
- One in-memory guard (a `Set` of in-flight keys, a mutex) protects one Node process only.
  If the service runs with several instances or workers, the TLA+ model must have more than
  one instance, and a fast-check test with in-memory fakes cannot see that race. A test with
  two real database connections can: see the next section.

## Database-backed promises (two instances)

Status: **ran** on PostgreSQL 16 (`postgres:16-alpine`) with `pg` 8.23 and fast-check 4.11,
Node 22, see the kit's `examples/node-postgres`. Other database engines: not run.

### When to use
- The guard is in the database (conditional `UPDATE`, unique constraint, `SELECT ... FOR UPDATE`,
  isolation level), or the service runs as more than one instance or worker.
- An in-process lock does nothing across instances, and an in-memory fake cannot show what the SQL does.
- Keep fakes for logic that does not depend on SQL behaviour: a schedule here costs about 30 ms.

### Wiring
- One real connection per "instance" (`new pg.Client`, not a shared pool), plus one observer
  connection for setup, for reading the committed result and for asking who waits for a lock.
- Give the product code a `query` that first awaits `s.schedule(Promise.resolve(), 'A: <sql>')` and
  then sends. Schedule the **sending** only, never the reply.
- `fc.scheduler({ act })`, where `act` releases one statement and then waits until every connection
  is at rest: parked at the scheduler, finished, or waiting for a lock.
- Ask the database for the lock case, fresh every time:
  `SELECT pid FROM unnest($1::int[]) AS t(pid) WHERE cardinality(pg_blocking_pids(pid)) > 0`,
  with `client.processID` as the pid. Copy `test/support/scheduled-sql.js` from the example.
- `await s.waitFor(Promise.allSettled([callA, callB]))`. Assert both fulfilled, then assert on the
  **committed rows** read by the observer. Assert the exact count where you know it: "0 redemptions"
  must not pass "at most once".
- Each run: a fresh row (new key) and a raw `ROLLBACK` on each instance connection. Each test
  process: its own schema, dropped at the end.
- Set `lock_timeout` and `statement_timeout` on the instance connections, so a stuck run fails with
  a database error instead of hanging.
- Failure message: the rule, then the statements in the order sent, with lock waits marked.
- After `fc.assert`, assert the run was not hollow: several different statement orders, at least one
  lock wait, each side won at least once.
- Lifecycle: `run = "{python} verification/with_postgres.py node --test test/<file>.test.js"`.
  Copy the script from the example and list it under `harness`. It starts PostgreSQL in Docker
  and always removes the container; if the database URL variable is already set it uses that
  database and no Docker.

### Gotchas
- Without the "wait until at rest" step the scheduler releases B's statement before A's reply is
  back. Then the order depends on network timing, a seed does not replay, and near-serial orders
  are never tried.
- Never put the reply inside the scheduled task. A statement waiting for a row lock resolves only
  after the other connection commits, so the scheduler would wait on it forever.
- Connect directly, not through a transaction-mode pooler: the transaction needs one session, and
  `client.processID` must be the real backend pid.
- The product code must take the connection as a parameter. If it awaits other real I/O between two
  statements, give that boundary to the scheduler too.
- Tests must **fail**, not skip, when the database URL is missing.
- `docker` on PATH does not mean the daemon runs: ask `docker version` for the server.
- The manifest `timeout` must be larger than the helper's own limits added up, so the helper always
  stops by itself and removes its container before verikit gives up.
- `postgres:16-alpine` is a moving tag. Print the server version in the test.
- Say in `assumes`: the isolation level (READ COMMITTED in the example), one primary, no
  mid-transaction failures. It is sampling, not exhaustive.
- GitHub's Windows runners cannot run Linux containers: run this check on a Linux runner, or point
  it at a database the pipeline provides.

### .NET and SQL Server - not run
- Same shape: one `DbConnection` per instance, wrapped in a decorator `DbConnection`/`DbCommand`
  (or an EF Core `DbCommandInterceptor`) whose `Execute*Async`, `BeginTransaction`, `Commit` and
  `Rollback` first await a gate (`TaskCompletionSource`) that the test owns.
- FsCheck generates the order (a list of "which instance sends next"), seeded from `VERIKIT_SEED`.
  The test releases one gate, then waits for rest as above.
- The lock-wait question per engine: PostgreSQL `pg_blocking_pids`; SQL Server
  `sys.dm_exec_requests.blocking_session_id <> 0` for the session; MySQL
  `performance_schema.data_lock_waits`.
- Coyote controls rewritten `Task` code, not real socket I/O to a database, so it fits in-process
  races (`examples/dotnet-coyote`); the gate + FsCheck approach fits real databases.
- None of this was run.
