# node-postgres

A promise whose guard lives in the **database**, checked against a real PostgreSQL with two
real connections that stand for two instances of the service.

`src/coupons.js` redeems a coupon for an order with the [`pg`](https://node-postgres.com) driver:
it marks the coupon as used and records the discount, in one transaction.

**Promise:** a coupon is redeemed at most once, even when two service instances handle requests
for it at the same time.
**Harm:** one coupon gives its discount to two orders. The shop pays twice, and a leaked code can
be used without limit.
**Why a database check:** a lock, a flag or a semaphore inside one Node process does nothing when
a second instance runs. Only something both instances share can keep this promise, and that is
the database. A test with an in-memory fake cannot show whether the SQL really does it.

## How it is wired

| File | Role |
|---|---|
| `src/coupons.js` | `redeemCoupon(db, code, orderId)`. `db` is one connection (`pg.Client` or a pool client). |
| `src/schema.sql` | The two tables: `coupons` and `order_discounts`. |
| `test/coupon.race.test.js` | The check. Two connections call the real `redeemCoupon()` for the same coupon; fast-check's scheduler decides which connection sends its next SQL statement; 200 statement orders per run, seeded from `VERIKIT_SEED`. The result is read from the committed rows. |
| `test/support/scheduled-sql.js` | The wrapper around `query` that hands each statement to the scheduler, and waits for the database between two steps. About 100 lines; copy it. |
| `test/support/testdb.js` | Opens the connections named by `COUPON_DATABASE_URL`. Each test process works in its own schema and drops it at the end. |
| `test/coupon.test.js` | The team's ordinary example tests, one connection, same database. |
| `verification/with_postgres.py` | Starts `postgres:16-alpine` in Docker, runs a test command against it, and always removes the container. Python standard library only. |

### The guard, and why this one

```sql
UPDATE coupons SET redeemed_by = $1, redeemed_at = now()
 WHERE code = $2 AND redeemed_by IS NULL
RETURNING amount_cents
```

One statement checks "still unused" and marks the coupon as used. If another transaction is
doing the same, PostgreSQL makes the second statement wait for the first one's row lock, then
looks at the row again. It is used by then, so no row comes back and the second order gets no
discount.

The other common guard is `SELECT ... FOR UPDATE` and then a plain `UPDATE`. It also works, but
the check and the write are two statements, and the safety hangs on two words that a later edit
can drop without any single-request test noticing. That is exactly the first broken variant
below. The conditional `UPDATE` keeps check and write in one statement, holds the row lock for a
shorter time, and reads the same in SQL Server and MySQL.

### How the scheduler orders real SQL statements

The product code gets a `query` function that first parks at fast-check's scheduler
(`s.schedule`) and only then sends the statement. Only the **sending** is scheduled, never the
reply. After the scheduler has released one statement, it waits until every connection is at
rest before it picks the next one. "At rest" means one of:

- parked at the scheduler with its next statement,
- finished,
- **waiting for a lock that the other connection holds**. This is not guessed from elapsed time.
  A third connection asks PostgreSQL (`pg_blocking_pids`), the way PostgreSQL's own isolation
  tester does.

So a statement that waits for a row lock does not stall the run: the scheduler goes on with the
connection that holds the lock, and the waiting statement finishes when that one commits. And
the choices the scheduler has never depend on how fast a reply came back, so a seed replays the
same statement orders (measured below).

## Run it

You need Node 22, Python 3.11+ and Docker running Linux containers.

```sh
npm ci
python <kit>/verikit.py init --no-skills        # once, inside a git repo
python verification/verikit.py run              # the check, plus both broken variants
python verification/verikit.py run --seed 12345 # replay a printed seed
npm run test:docker                             # all tests, with a throwaway PostgreSQL
```

`verification/with_postgres.py <command>`:

- starts `postgres:16-alpine` under a unique name, on a free port of `127.0.0.1`, data in memory;
- waits until PostgreSQL answers on that port, then runs the command with `COUPON_DATABASE_URL` set;
- removes the container when the command ends, fails, runs longer than `WITH_POSTGRES_TIMEOUT`
  seconds (180), or on Ctrl+C. If the script itself is killed, the container stops and removes
  itself after 15 minutes;
- exits with 2 and a clear message when `docker` is missing or its daemon does not answer.

**Your own test database:** set `COUPON_DATABASE_URL` and Docker is not used at all.

```sh
COUPON_DATABASE_URL=postgres://user:password@127.0.0.1:5432/testdb npm test
COUPON_DATABASE_URL=postgres://user:password@127.0.0.1:5432/testdb python verification/verikit.py run
```

The tests create a schema `coupon_test_<pid>_<time>` there and drop it at the end; they touch
nothing else. Without `COUPON_DATABASE_URL` and without the helper the tests **fail**; they never skip.

## What a failure looks like

From the check-then-act variant, seed 1, first schedule:

```
Property failed after 1 tests
{ seed: 1, path: "0", endOnFailure: true }
...
Got AssertionError [ERR_ASSERTION]: coupon redeemed 2 times (SAVE10-1: order-A-1, order-B-1); rule: a coupon is redeemed at most once.
SQL statements, in the order they were sent:
  -> B: BEGIN
  -> A: BEGIN
  -> A: SELECT amount_cents, redeemed_by FROM coupons WHERE code = $1
  -> B: SELECT amount_cents, redeemed_by FROM coupons WHERE code = $1
  -> A: UPDATE coupons SET redeemed_by = $1, redeemed_at = now() WHERE code = $2
  -> B: UPDATE coupons SET redeemed_by = $1, redeemed_at = now() WHERE code = $2 [waited for a row lock]
  -> A: INSERT INTO order_discounts (order_id, coupon_code, amount_cents) VALUES ($1, $2, $3)
  -> A: COMMIT
  -> B: INSERT INTO order_discounts (order_id, coupon_code, amount_cents) VALUES ($1, $2, $3)
  -> B: COMMIT
```

Both instances read "unused" before either wrote. B's `UPDATE` waited for A's row lock, and then
overwrote A's row, because nothing in it checks again.

## Broken variants (`verification/coupons/mutants/`)

| Patch | What it changes | Caught |
|---|---|---|
| `check-then-act.patch` | `SELECT`, decide in JavaScript, then an `UPDATE` that no longer checks. Still inside a transaction, at the default READ COMMITTED level. | yes, `coupon redeemed 2 times`. The ordinary tests in `test/coupon.test.js` all **pass** on this variant: one request at a time cannot see it. |
| `no-unused-condition.patch` | the `UPDATE` loses `AND redeemed_by IS NULL` | yes, `coupon redeemed 2 times`; the ordinary test "a used coupon is refused" catches it too |

## Measured

On Windows 11 with Docker Desktop 29.8 (Linux containers), Node 22.13, `pg` 8.23.1,
fast-check 4.11.0, PostgreSQL 16.15 (`postgres:16-alpine`):

| | |
|---|---|
| Check-then-act variant | found in 200 of 200 seeds; first failing schedule: median 1, max 5 |
| Variant without `AND redeemed_by IS NULL` | found in 50 of 50 seeds, on schedule 1 |
| Correct code | 10 seeds x 200 schedules, no failure |
| Statement orders in one passing run | 24 different orders in 200 schedules, with each of the 6 seeds looked at. By counting, 24 is every order this code can produce for two callers (12 per winner). In about 6 of 10 schedules a statement waited for the other connection's row lock. |
| Same seed, run twice | the same statement orders and the same lock waits, in all 260 seeds compared |
| One passing run of 200 schedules | 5-7 s, about 30 ms per schedule (some 15 round trips to the database). Once 41 s, while other containers kept the machine busy. |
| Start PostgreSQL in Docker, wait until ready, remove it | 3-4 s per run, image already pulled |
| `verikit run` (holds + 2 variants = three containers) | 19-22 s; up to 34 s on the busy machine |

The bug needs very few schedules: both variants fail on the first or second one almost always,
because the window is wide (B only has to read before A commits). The 200 schedules are for the
passing run.

The Node check has run on Windows 11 with Docker Desktop only. The helper script alone also ran
on Linux (Ubuntu under WSL 2, Docker Engine 29.1, Python 3.14): normal run, failing command,
timeout, SIGINT, SIGTERM, and a SIGKILL of the helper, after which the container removed itself.
The kit's CI job for this example has not run yet.

## Limits

- **Sampling, not proof.** 200 random statement orders per run. In this small case (two
  connections, one coupon, one call each) the samples happened to reach all 24 possible orders,
  but nothing enforces that. A larger case, such as three callers or more statements, will not
  be covered this way.
- **PostgreSQL only.** This ran on PostgreSQL 16 with the `pg` driver. SQL Server, MySQL, Oracle
  and SQLite were **not** run. The pattern carries over (one connection per instance, schedule the
  sending of each statement, ask the database who is waiting for a lock), but lock and isolation
  behaviour differs between engines, so run it on the engine you ship on.
- **READ COMMITTED.** The check runs at PostgreSQL's default isolation level. Other levels were not run.
- **One primary.** Both connections talk to the same server. Read replicas, connection poolers in
  transaction mode and failover are not exercised.
- **No failures in the middle.** A lost connection, a statement timeout or a crash between two
  statements is not generated. `redeemCoupon()` rolls back on an error, and that path has no check.
- **Between statements the code only computes.** If `redeemCoupon()` awaited an HTTP call or a
  timer between two statements, that boundary would have to go through the scheduler as well.
- **One guard.** `order_discounts` has no `UNIQUE (coupon_code)`. Adding it would be a second
  guard, and a good one. It would then need its own broken variant, and the two variants here
  would have to be caught with it still in place.
- **Docker.** The helper needs Linux containers. In the kit's CI this example runs on
  `ubuntu-latest` only; GitHub's Windows runners cannot run Linux containers.
