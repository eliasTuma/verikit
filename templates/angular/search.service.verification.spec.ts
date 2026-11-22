// Template (NOT run by the kit): the same promise checked through Angular's TestBed with a
// fake HttpClient whose responses fast-check schedules. Prefer testing the plain store
// (examples/angular-search-store) - it needs no TestBed and runs in any runner.
import { TestBed } from '@angular/core/testing';
import { HttpClient } from '@angular/common/http';
import { from } from 'rxjs';
import fc from 'fast-check';
import { SearchService } from './search.service';

describe('SearchService', () => {
  it('verification: results shown always belong to the latest query', async () => {
    await fc.assert(
      fc.asyncProperty(fc.scheduler(), fc.array(fc.constantFrom('a', 'ab', 'abc'), { minLength: 1, maxLength: 4 }),
        async (s, typed) => {
          const http = { get: (_url: string, opts: any) =>
            from(s.schedule(Promise.resolve([`${opts.params.q} result`]), `response for "${opts.params.q}"`)) };
          TestBed.resetTestingModule();
          TestBed.configureTestingModule({ providers: [{ provide: HttpClient, useValue: http }] });
          const service = TestBed.inject(SearchService);
          s.scheduleSequence(typed.map((q) => ({ label: `user types "${q}"`, builder: async () => service.search(q) })));
          await s.waitIdle();
          if (service.shown.query !== typed[typed.length - 1])
            throw new Error(`stale results shown: "${service.shown.query}"`);
        }),
      { numRuns: 200, includeErrorInReport: true });
  });
});
