# Angular

Status: **partly ran.** The RxJS service logic ran with fast-check 4 and Vitest, see the kit's
`examples/angular-search-store`. The Angular wiring (`TestBed`, `HttpTestingController`,
Karma/Jasmine or Jest via the Angular builder) is a **template that has not been run**:
`templates/angular/`.

## What to verify

Most Angular promises live in services, stores (NgRx, signals, plain RxJS) and guards:
results always match the latest query (`switchMap` vs `mergeMap`), a save is never sent twice,
a route guard never lets an unauthenticated user through, an optimistic update is reverted
on error, polling stops when the component is destroyed.

## Where the checks go

Next to the service's existing `*.spec.ts`. Put `verification` in the `it`/`test` name:

```toml
run = "npx jest -t verification"                                         # Jest
run = "npx ng test --watch=false --include='**/*.verification.spec.ts'"  # Karma/Jasmine
```

## Pattern: keep the logic testable without TestBed

Put the RxJS pipeline in a plain function or class that takes its boundaries (`HttpClient`,
clock) as parameters, and have the `@Injectable` service delegate to it. Then fast-check's
scheduler can control every response:

```js
const http = { get: (q) => from(s.schedule(Promise.resolve([`${q} result`]), `response for "${q}"`)) };
const store = createSearchStore(http);
s.scheduleSequence(typed.map((q) => ({ label: `user types "${q}"`, builder: async () => store.search(q) })));
await s.waitIdle();
if (store.shown.query !== typed.at(-1)) throw new Error(`stale results shown: ...`);
```

`scheduleSequence` keeps user actions in order while responses arrive in any order - which is
exactly what users and networks do. The example's broken variant (`mergeMap`) shows the stale
result in 4 steps.

## Pitfalls

- `fakeAsync`/`tick` and zone.js control time differently from fast-check's scheduler; for
  property tests prefer plain promises/observables over `fakeAsync`.
- Observables that never complete keep `waitIdle()` waiting only if they schedule more tasks;
  cold `HttpClient` calls are fine.
