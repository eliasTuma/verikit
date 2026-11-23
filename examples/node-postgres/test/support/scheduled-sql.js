// Lets fast-check's scheduler decide the order in which several database connections send
// their SQL statements. The statements are real and run on a real PostgreSQL.
//
//   const sql = scheduledSql(observer);                       // once per test
//   fc.scheduler({ act: sql.act })                            // the scheduler arbitrary
//   sql.start(s);                                             // at the start of each run
//   const a = sql.connection('A', clientA);                   // { query } for the product code
//   await s.waitFor(Promise.allSettled([work(a), work(b)]));
//   sql.order()                                               // 'A: BEGIN -> B: BEGIN -> ...'
//
// How one step works:
//   1. Every connection that wants to send a statement first parks at the scheduler
//      (`s.schedule`). Only the SENDING is scheduled, never the reply.
//   2. The scheduler picks one parked statement. It is sent.
//   3. Before the scheduler picks again, `act` waits until every connection is at rest:
//      parked at the scheduler again, finished, or waiting for a lock that another
//      connection holds. The last case is asked from the database (pg_blocking_pids), the
//      way PostgreSQL's own isolation tester does it. It is never guessed from elapsed time.
//
// Step 3 is what makes a run repeat from its seed: the set of statements the scheduler can
// choose from never depends on how fast a reply came back. It also keeps a statement that
// waits for a row lock from stalling the run: the scheduler goes on with the connection
// that holds the lock, and the waiting statement finishes when that one commits.
//
// Limit: between two statements the product code may only compute. If it awaits something
// else that is real (an HTTP call, a timer), give that boundary to the scheduler as well.
import { setTimeout as sleep } from 'node:timers/promises';

const oneLine = (text) => {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > 120 ? `${flat.slice(0, 117)}...` : flat;
};
const nextTurn = () => new Promise((resolve) => setImmediate(resolve));

export function scheduledSql(observer) {
  let scheduler = null;
  let connections = [];
  let sent = []; // { name, sql, waitedForLock } in the order the statements were sent

  // Which of these backends are waiting for a lock right now? Asked fresh every time.
  async function waitingForLock(pids) {
    const { rows } = await observer.query(
      'SELECT pid FROM unnest($1::int[]) AS t(pid) WHERE cardinality(pg_blocking_pids(pid)) > 0',
      [pids],
    );
    return new Set(rows.map((row) => row.pid));
  }

  async function settle() {
    let pause = 1;
    for (;;) {
      await nextTurn(); // code that just got a reply runs on, up to its next statement
      const flying = connections.filter((c) => c.flying);
      if (flying.length === 0) return;
      // Most statements answer within a millisecond: give them that before asking.
      await Promise.race([...flying.map((c) => c.flying.done), sleep(pause)]);
      if (flying.some((c) => c.flying === null)) continue; // one finished: look again
      const waiting = await waitingForLock(flying.map((c) => c.pid));
      if (flying.every((c) => c.flying !== null && waiting.has(c.pid))) {
        for (const c of flying) c.flying.entry.waitedForLock = true;
        return;
      }
      pause = Math.min(pause * 2, 20);
    }
  }

  return {
    // Wraps every step of the scheduler: release one statement, then wait for rest.
    act: async (release) => {
      await release();
      await settle();
    },

    start(s) {
      scheduler = s;
      connections = [];
      sent = [];
    },

    // `client` is a connected pg.Client. The returned object is what the product code gets.
    connection(name, client) {
      const state = { name, pid: client.processID, flying: null };
      connections.push(state);
      return {
        async query(text, params) {
          const sql = oneLine(text);
          await scheduler.schedule(Promise.resolve(), `${name}: ${sql}`); // parked until picked
          const entry = { name, sql, waitedForLock: false };
          sent.push(entry);
          const reply = client.query(text, params);
          const landed = () => {
            state.flying = null;
          };
          state.flying = { entry, done: reply.then(landed, landed) };
          return reply;
        },
      };
    },

    sent: () => sent,
    order: () =>
      sent.map((e) => `${e.name}: ${e.sql}${e.waitedForLock ? ' [waited for a row lock]' : ''}`).join('\n  -> '),
  };
}
