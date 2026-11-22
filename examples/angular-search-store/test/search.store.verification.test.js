// Code-level check: real RxJS pipeline, fake HttpClient. fast-check's scheduler decides when
// each response arrives relative to the user's typing (which stays in order).
import { expect, test } from 'vitest';
import { from } from 'rxjs';
import fc from 'fast-check';
import { createSearchStore } from '../src/search.store.js';

const seed = process.env.VERIKIT_SEED ? Number(process.env.VERIKIT_SEED) : undefined;

test('verification: results shown always belong to the latest query', async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.scheduler(),
      fc.array(fc.constantFrom('a', 'ab', 'abc', 'b'), { minLength: 1, maxLength: 4 }),
      async (s, typed) => {
        const http = { get: (q) => from(s.schedule(Promise.resolve([`${q} result`]), `response for "${q}"`)) };
        const store = createSearchStore(http);
        s.scheduleSequence(typed.map((q) => ({ label: `user types "${q}"`, builder: async () => store.search(q) })));
        await s.waitIdle();
        const latest = typed[typed.length - 1];
        if (store.shown.query !== latest)
          throw new Error(`stale results shown: "${store.shown.query}" while the latest query is "${latest}"`);
      },
    ),
    { numRuns: 300, seed, includeErrorInReport: true },
  );
});
