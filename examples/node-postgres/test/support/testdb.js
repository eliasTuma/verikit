// Opens real connections to the PostgreSQL test database named by COUPON_DATABASE_URL.
//
// Every test process works in its own schema (created here, dropped in close()), so the
// tests can share a database with each other and with a team's other test data.
import { readFileSync } from 'node:fs';
import pg from 'pg';

const SCHEMA_SQL = new URL('../../src/schema.sql', import.meta.url);

export async function openTestDatabase({ connections }) {
  const url = process.env.COUPON_DATABASE_URL;
  if (!url) {
    // Fail, never skip: a check that silently runs nothing would look like a pass.
    throw new Error(
      'COUPON_DATABASE_URL is not set. Run the tests through verification/with_postgres.py ' +
        '(it starts PostgreSQL in Docker), or set COUPON_DATABASE_URL to a PostgreSQL test database, ' +
        'for example postgres://user:password@127.0.0.1:5432/dbname',
    );
  }
  const schema = `coupon_test_${process.pid}_${Date.now().toString(36)}`;
  const open = [];
  const connect = async (applicationName) => {
    const client = new pg.Client({ connectionString: url, application_name: applicationName });
    await client.connect();
    open.push(client);
    return client;
  };

  try {
    // The observer is the test's own connection: it prepares rows, reads the committed
    // result, and asks PostgreSQL which connection is waiting for a lock.
    const observer = await connect('coupon-test-observer');
    await observer.query(`CREATE SCHEMA ${schema}`);
    await observer.query(`SET search_path TO ${schema}`);
    await observer.query(readFileSync(SCHEMA_SQL, 'utf8'));
    const serverVersion = (await observer.query('SHOW server_version')).rows[0].server_version;
    const isolation = (await observer.query('SHOW default_transaction_isolation')).rows[0]
      .default_transaction_isolation;

    // One connection per "service instance". The timeouts only stop a broken run from
    // hanging: a statement that waits 5 s for a lock fails with a clear database error.
    const instances = [];
    for (let i = 0; i < connections; i++) {
      const client = await connect(`coupon-test-instance-${i + 1}`);
      await client.query(`SET search_path TO ${schema}`);
      await client.query("SET lock_timeout = '5s'");
      await client.query("SET statement_timeout = '15s'");
      instances.push(client);
    }

    return {
      observer,
      instances,
      serverVersion,
      isolation,
      async close() {
        await Promise.allSettled(instances.map((c) => c.end()));
        await observer.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`).catch(() => {});
        await observer.end().catch(() => {});
      },
    };
  } catch (err) {
    await Promise.allSettled(open.map((c) => c.end()));
    throw err;
  }
}
