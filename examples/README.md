# Examples

Each folder is a small, self-contained project with a `verification/verification.toml`, at
least one check, and broken variants that must fail. Run them all through the real flow
(fresh git repo, `init`, `run`):

```sh
python scripts/check_examples.py                  # needs node/npm, .NET 8, and Java or Docker
python scripts/check_examples.py dotnet-checkout  # one example
```

| Example | Stack | Promise | Checks | Broken variants |
|---|---|---|---|---|
| [node-checkout](node-checkout) | Node + TLA+ | A customer is never charged twice for one purchase. | TLA+ design model; fast-check scheduler on the real `payForOrder()` | per-attempt keys (`--const`), reachability witness (`--inv`), key created inside the retry loop (patch) |
| [dotnet-checkout](dotnet-checkout) | .NET 8 + .NET Framework 4.8 | the same | xUnit + FsCheck generated event orderings on the real `CheckoutSession`, both frameworks | key built per attempt (patch) |
| [react-pay-button](react-pay-button) | React 18 + Vitest | One click or many, the order is paid at most once. | fast-check scheduler + Testing Library on the real `PayButton` | guard on React state instead of a ref (patch): pays twice on a same-tick double click |
| [angular-search-store](angular-search-store) | Angular service logic (RxJS) + Vitest | The results shown always belong to the latest query. | fast-check scheduler on the real RxJS pipeline | `mergeMap` instead of `switchMap` (patch) |
| [explorer](explorer) | Python model | the checkout design, for `verikit explore` | zero-install fallback | `--const ReuseKey=False`, `--inv NeverCharged` |
