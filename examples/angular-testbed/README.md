# angular-testbed

A real Angular CLI workspace (Angular 20.3, `@angular/build:karma`, Karma + Jasmine, headless
Chrome) whose verification test runs through Angular's own test runner with `TestBed`: the
same `ng test` the team already runs. It complements
[angular-search-store](../angular-search-store), which checks the same kind of RxJS logic
outside Angular.

**Promise:** the search results shown always belong to the latest query.

- Product code: `src/app/search.service.ts` (every keystroke goes through a `Subject`, then
  `switchMap` to `HttpClient.get('/api/search')`, then a signal) and
  `src/app/search.component.ts` (the input box and the results list).
- Check: `src/app/search.verification.spec.ts`, next to the ordinary
  `search.component.spec.ts`. The component, the service and `HttpClient` are real, set up by
  `TestBed`, and Angular's `HttpTestingController` plays the server. fast-check's
  `fc.scheduler()` keeps the user's typing in order and lets each reply arrive at any later
  point, so every run tries a different interleaving (200 runs, 1-4 queries). After every
  reply that reaches the app, and once all replies are in, the spec reads the heading and list
  items from the DOM. If they are not for the latest query, it fails with
  `stale results shown`.
- Broken variant: `verification/search/mutants/merge-map.patch` swaps `switchMap` for
  `mergeMap`. The old request stays alive, so its reply can land after the user typed
  something newer. With `--seed 1`, fast-check shrinks the failure to: the user types "a",
  then "abc", then the reply to "a" arrives, and the page shows results for "a" while the
  latest query is "abc".

```sh
npm ci
# the check alone: headless, single run, exit 0 = holds
npx ng test --watch=false --no-progress --browsers=ChromeHeadlessCI --include=src/app/search.verification.spec.ts
# the team's normal run (watch mode, Chrome) runs it too, beside the ordinary spec
npx ng test
python <kit>/verikit.py init --no-skills && python verification/verikit.py run
```

## Wiring

- `karma.conf.js` (referenced by `angular.json`, `test.options.karmaConfig`) is a minimal
  Karma config (Jasmine, the Chrome launcher, the progress reporter) with two additions:
  - the `ChromeHeadlessCI` launcher: `ChromeHeadless` plus `--no-sandbox`. Linux CI often
    needs it: Chrome will not start its sandbox as root (Docker images), and Ubuntu 23.10+
    restricts the user namespaces the sandbox uses. (Run here on Windows only.)
  - `client.verikitSeed: process.env.VERIKIT_SEED`. `verikit run` exports `VERIKIT_SEED`, but
    the spec runs in the browser and cannot see the environment. karma.conf.js runs in Node
    and passes the value to the browser as client config. The spec reads
    `__karma__.config.verikitSeed`, or `process.env.VERIKIT_SEED` under a Node-based runner,
    and hands it to `fc.assert`. To check it works: `verikit run --seed 1` on the broken
    variant prints `{ seed: 1, ... }` in fast-check's report.
- `--include=<spec>` builds and runs only the verification spec. Plain `ng test` still picks
  up every `*.spec.ts`, so the check also runs in the team's normal test command.
- One `TestBed` per fast-check run: `beforeEach` runs once per `it`, so the property calls
  `TestBed.resetTestingModule()` and `configureTestingModule()` itself and destroys the fixture.
- Replies to requests the app has cancelled are skipped (`req.cancelled`). `switchMap`
  unsubscribes from them, and flushing one throws `Cannot flush a cancelled request.` Without
  the guard, the correct code fails with that error.
- An error thrown inside a scheduled task does not fail the property. When the spec threw
  there directly, the broken variant passed (exit 0) and Karma only logged the errors after
  the spec. So the spec collects problems and throws after `s.waitIdle()`.
- The `it` timeout is 60 s, not Jasmine's default 5 s. That is a precaution: runs here took
  under a second. It means a slow machine reports `stale results shown` and not a timeout.

Needs Node 20.19+, 22.12+ or 24+ (Angular 20's range) and Chrome or Chromium.
karma-chrome-launcher finds an installed Chrome; otherwise set `CHROME_BIN`.

## The same spec under Jest or Vitest

Both were tried once, on Windows, in scratch copies. Neither is part of this example, and
`check_examples.py` does not run them. The spec file needed no changes. Under both runners it
reads `VERIKIT_SEED` from `process.env`, and with `VERIKIT_SEED=1` both printed the same
shrunk counterexample as Karma.

- **Jest** (this workspace, Angular 20.3, zone.js): jest 30.5.2, jest-preset-angular 17.0.3,
  jsdom 26.1.0 and @types/jest 30.0.0. In `tsconfig.spec.json`, set `"types": ["jest"]`.

  ```js
  // jest.config.js
  const { createCjsPreset } = require('jest-preset-angular/presets');
  module.exports = {
    ...createCjsPreset({ testEnvironment: 'jest-preset-angular/environments/jest-jsdom-env' }),
    setupFilesAfterEnv: ['<rootDir>/setup-jest.ts'], // setupZoneTestEnv() from 'jest-preset-angular/setup-env/zone'
  };
  ```

  Run it with `npx jest -t verification`. The preset's default `testEnvironment: 'jsdom'`
  needs the separate `jest-environment-jsdom` package. Without it, point to the preset's own
  environment, as above.
- **Vitest**, the default for `ng new` from Angular 21: a fresh 21.2 workspace (CLI 21.2.28,
  `@angular/build:unit-test`, vitest 4.1.12, jsdom 28.1.0, zoneless) with this `src/app`
  copied in. Run it with
  `npx ng test --watch=false --include=src/app/search.verification.spec.ts`. On that
  workspace, `npm install` (npm 10.9.2) crashed with
  `Cannot read properties of null (reading 'edgesOut')`, and `--legacy-peer-deps` got past
  it. Vitest browser mode was not tried. In browser mode the spec cannot read `process.env`,
  so the seed would need another route.

## Not covered

- Debounce, retry and caching (there are none here). A `debounceTime` would need a controlled
  clock next to fast-check's scheduler; `fakeAsync` was not tried with it.
- Error replies, and a second tab or window.
- Results that are still on screen from an older query while a newer request is in flight
  are not counted as stale. The check looks at the page after each reply lands.
