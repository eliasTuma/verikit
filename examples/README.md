# Examples

Each folder is a small, self-contained project with a `verification/verification.toml`, at
least one check, and broken variants that must fail. Run them all through the real flow
(fresh git repo, `init`, `run`):

```sh
python scripts/check_examples.py                  # every example this machine can run
python scripts/check_examples.py dotnet-checkout  # one example (SKIPPED counts as a failure)
```

Each example's `example.toml` says what it needs: node/npm, .NET 8, Java or Docker (TLA+),
Docker with Linux containers (PostgreSQL), Chrome (Angular TestBed), Visual Studio or Build
Tools on Windows (legacy .NET Framework). `python scripts/check_examples.py --isolated` runs the
broken variants in a throwaway copy of each repo instead of in place.

| Example | Stack | Promise | Checks | Broken variants |
|---|---|---|---|---|
| [node-checkout](node-checkout) | Node + TLA+ | A customer is never charged twice for one purchase. | TLA+ design model; fast-check scheduler on the real `payForOrder()`; recorded traces of the real code replayed against the model (`verikit trace`) | per-attempt keys (`--const`), reachability witness (`--inv`), per-attempt key in the code (find-and-replace edit), model with the wrong retry bound |
| [node-postgres](node-postgres) | Node + PostgreSQL 16 in Docker | A coupon is redeemed at most once, even when two service instances handle it at the same time. | fast-check schedules the real SQL statements of two real connections | check-then-act (`SELECT`, then `UPDATE`), `UPDATE` without its condition (patches) |
| [dotnet-checkout](dotnet-checkout) | .NET 8 + .NET Framework 4.8 | the same | xUnit + FsCheck generated event orderings on the real `CheckoutSession`, both frameworks | key built per attempt (patch) |
| [dotnet-legacy](dotnet-legacy) | Legacy (non-SDK) .NET Framework 4.8, packages.config, MSTest v2 | the same | FsCheck generated event orderings; built with MSBuild, run with vstest.console (`legacy_test.py`) | key built per attempt (patch) |
| [dotnet-coyote](dotnet-coyote) | .NET 8 + Microsoft Coyote | A gift card is redeemed at most once, even when two requests arrive at once. | Coyote explores real async interleavings of `RedeemAsync` (binary-rewritten after build) | no `SemaphoreSlim` (check-then-act race), no "already redeemed" check (patches) |
| [react-pay-button](react-pay-button) | React 18 + Vitest | One click or many, the order is paid at most once. | fast-check scheduler + Testing Library on the real `PayButton` | guard on React state instead of a ref (patch): pays twice on a same-tick double click |
| [angular-search-store](angular-search-store) | Angular service logic (RxJS) + Vitest | The results shown always belong to the latest query. | fast-check scheduler on the real RxJS pipeline | `mergeMap` instead of `switchMap` (patch) |
| [angular-testbed](angular-testbed) | Angular 20 workspace, `ng test` (Karma + Jasmine, headless Chrome) | the same | fast-check scheduler + TestBed + `HttpTestingController` on the real component, asserted on the DOM | `mergeMap` instead of `switchMap` (patch) |
| [explorer](explorer) | Python model | the checkout design, for `verikit explore` | zero-install fallback | `--const ReuseKey=False`, `--inv NeverCharged` |
