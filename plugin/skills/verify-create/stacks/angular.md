# Angular

Status: **ran.**
- Through Angular's own runner: `ng test` (`@angular/build:karma`, Karma + Jasmine, headless
  Chrome) with TestBed + `HttpTestingController` + fast-check 4 on Angular 20.3, see the kit's
  `examples/angular-testbed`. Windows locally; Linux in the kit's CI.
- The RxJS logic alone, without Angular, with Vitest: `examples/angular-search-store`.
- **Tried by hand, not through verikit:** the same spec unchanged under Jest (jest 30.5,
  jest-preset-angular 17.0.3) and under the Vitest runner that Angular 21 uses by default
  (`@angular/build:unit-test`, zoneless). Both passed on the correct code and found the same
  counterexample on the broken variant.
- **Not tried:** the older webpack builder `@angular-devkit/build-angular:karma`, Vitest browser
  mode, NgRx.

## What to verify

Most Angular promises live in services, stores (NgRx, signals, plain RxJS) and guards:
results always match the latest query (`switchMap` vs `mergeMap`), a save is never sent twice,
a route guard never lets an unauthenticated user through, an optimistic update is reverted
on error, polling stops when the component is destroyed.

## Where the checks go

`src/app/<area>.verification.spec.ts`, next to the existing specs. Plain `ng test` already
includes `**/*.spec.ts`, so the team's normal run picks it up.

```toml
run = 'npx ng test --watch=false --no-progress --browsers=ChromeHeadlessCI --include=src/app/search.verification.spec.ts'  # Karma
run = "npx jest -t verification"                                                       # Jest (tried by hand)
run = 'npx ng test --watch=false --include=src/app/search.verification.spec.ts'         # Angular 21+ Vitest runner (tried by hand)
```

Name the spec file explicitly: glob quoting differs between cmd.exe and sh (`'**/...'` is not
quoting in cmd.exe).

## Wiring (Karma)

- **Headless browser:** most `karma.conf.js` files say `browsers: ['Chrome']`, which opens a
  window. Add a launcher and point `angular.json` -> `test.options.karmaConfig` at the file:
  ```js
  customLaunchers: { ChromeHeadlessCI: { base: 'ChromeHeadless', flags: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'] } }
  ```
  `--no-sandbox` is for Linux CI runners.
- **Seed into the browser:** Karma runs the spec in Chrome, where `process.env` does not exist.
  In `karma.conf.js`: `client: { verikitSeed: process.env.VERIKIT_SEED || '' }`. In the spec:
  ```ts
  const seed = (typeof __karma__ !== 'undefined' && __karma__.config?.verikitSeed)
    || (globalThis as any).process?.env?.['VERIKIT_SEED'];     // fallback: Jest / Vitest (jsdom)
  ```
  List `karma.conf.js` in the check's `harness`.

## Pattern: TestBed per run, the scheduler decides reply order

```ts
await fc.assert(fc.asyncProperty(fc.scheduler(), queries, async (s, typed) => {
  TestBed.resetTestingModule();                       // beforeEach runs once per `it`, not per run
  TestBed.configureTestingModule({ imports: [SearchComponent],
    providers: [provideHttpClient(), provideHttpClientTesting()] });
  const fixture = TestBed.createComponent(SearchComponent);
  const http = TestBed.inject(HttpTestingController);
  const problems: string[] = [];
  for (const q of typed) {                            // user actions stay in order
    type(fixture, q);
    const req = http.expectOne(r => r.url === '/api/search' && r.params.get('q') === q);
    s.schedule(Promise.resolve(), `server answers "${q}"`).then(() => {
      if (req.cancelled) return;                      // switchMap unsubscribed: never flush it
      req.flush(resultsFor(q));
      fixture.detectChanges();
      checkDom(fixture, typed, problems);             // record, do not throw here
    });
  }
  await s.waitIdle();
  fixture.destroy();
  if (problems.length) throw new Error(problems.join('\n'));   // e.g. "stale results shown ..."
}), { seed, numRuns: 200 });
```

## Pitfalls (seen while building the example)

- **An error thrown inside a scheduled task does not fail the property.** With a direct `throw`
  there, the `mergeMap` variant passed with exit 0 and Karma only logged `ERROR:` after the spec.
  Collect problems and throw after `await s.waitIdle()` - and always watch the variant fail.
- **Flushing a cancelled request throws** `Cannot flush a cancelled request.`; skip
  `req.cancelled`, or the correct code fails.
- Assert on the DOM (`fixture.detectChanges()`, `fixture.nativeElement`), not on service state.
  No `fakeAsync` is needed: the scheduler and promises worked under zone.js and zoneless.
- Give the `it` an explicit timeout (`it(name, fn, 60_000)`), so a slow machine fails with the
  rule's message rather than Jasmine's 5 s timeout.
- `ng new` on Windows writes CRLF; broken-variant patches must match the committed (LF) sources.
- `ng test` leaves `dist/test-out` and `.angular/`; gitignore both.
- Vitest runner (Angular 21): `npm install` crashed with `Cannot read properties of null
  (reading 'edgesOut')` on npm 10.9.2; `--legacy-peer-deps` worked.
- Without TestBed: put the RxJS pipeline in a plain function that takes `HttpClient`-like
  boundaries, and test it in any runner (`examples/angular-search-store`).
