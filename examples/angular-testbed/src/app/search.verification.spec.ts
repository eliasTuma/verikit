// Code-level check, run by Angular's own test runner (`ng test`: Karma + Jasmine + TestBed).
// Real SearchComponent, real SearchService, real HttpClient; Angular's HttpTestingController
// plays the server. fast-check's scheduler decides when each reply arrives relative to the
// user's typing (which stays in order), so every run tries a different interleaving.
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import fc from 'fast-check';
import { SearchComponent } from './search.component';

// VERIKIT_SEED, so a failure replays with `verikit run --seed N`. Under Karma the spec runs in
// the browser, so karma.conf.js forwards it as client.verikitSeed; under Jest or Vitest the
// spec runs in Node and reads process.env.
declare const __karma__: { config?: { verikitSeed?: string } } | undefined;
const rawSeed =
  (typeof __karma__ !== 'undefined' && __karma__.config?.verikitSeed) ||
  (globalThis as any).process?.env?.['VERIKIT_SEED'];
const seed = rawSeed ? Number(rawSeed) : undefined;

/** What the server returns for a query; every item names the query it answers. */
const resultsFor = (q: string) => [`${q}: result 1`, `${q}: result 2`];

/** What the user sees: the heading and the list items, read from the DOM. */
function shownOn(fixture: ComponentFixture<SearchComponent>) {
  const el: HTMLElement = fixture.nativeElement;
  const heading = el.querySelector('.results-for')?.textContent ?? '';
  return {
    query: /Results for "(.*)"/.exec(heading)?.[1] ?? null,
    items: Array.from(el.querySelectorAll('li'), (li) => li.textContent ?? ''),
  };
}

function staleness(fixture: ComponentFixture<SearchComponent>, latest: string): string | null {
  const shown = shownOn(fixture);
  const ok = shown.query === latest && shown.items.every((item) => item.startsWith(`${latest}: `));
  return ok ? null : `results for "${shown.query}" (${shown.items.join(', ')}) while the latest query is "${latest}"`;
}

describe('SearchComponent', () => {
  it('verification: the search results shown always belong to the latest query', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.scheduler(),
        fc.array(fc.constantFrom('a', 'ab', 'abc', 'b'), { minLength: 1, maxLength: 4 }),
        async (s, typed) => {
          TestBed.resetTestingModule(); // a fresh app per run: beforeEach runs once per `it`
          TestBed.configureTestingModule({
            imports: [SearchComponent],
            providers: [provideHttpClient(), provideHttpClientTesting()],
          });
          const fixture = TestBed.createComponent(SearchComponent);
          const server = TestBed.inject(HttpTestingController);
          fixture.detectChanges();
          const input: HTMLInputElement = fixture.nativeElement.querySelector('input');

          // Errors inside scheduled tasks would not fail the property; collect them instead.
          const problems: string[] = [];
          const record = (fn: () => void) => {
            try {
              fn();
            } catch (e) {
              problems.push(String(e));
            }
          };
          let latest = '';

          s.scheduleSequence(
            typed.map((q, i) => ({
              label: `user types "${q}"`,
              builder: async () =>
                record(() => {
                  latest = q;
                  input.value = q;
                  input.dispatchEvent(new Event('input')); // SearchComponent.onInput -> SearchService.search
                  fixture.detectChanges();
                  const req = server.expectOne(
                    (r) => r.url === '/api/search' && r.params.get('q') === q,
                    `GET /api/search?q=${q}`,
                  );
                  // The reply for this request arrives whenever the scheduler says.
                  s.schedule(Promise.resolve(), `server answers "${q}" (request ${i + 1})`).then(() =>
                    record(() => {
                      if (req.cancelled) return; // the app dropped this request; nobody reads the reply
                      req.flush(resultsFor(q));
                      fixture.detectChanges();
                      const stale = staleness(fixture, latest);
                      if (stale) problems.push(`stale results shown after the reply to "${q}": ${stale}`);
                    }),
                  );
                }),
            })),
          );
          await s.waitIdle();

          record(() => server.verify()); // no request left unanswered, none sent twice
          const stale = staleness(fixture, typed[typed.length - 1]);
          if (stale) problems.push(`stale results shown once every reply is in: ${stale}`);
          fixture.destroy();
          if (problems.length) throw new Error(`typed ${JSON.stringify(typed)}: ${problems.join('; ')}`);
        },
      ),
      { numRuns: 200, seed, includeErrorInReport: true },
    );
  }, 60_000);
});
