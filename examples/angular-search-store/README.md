# angular-search-store

The RxJS logic of an Angular search service, kept framework-free so it can be property-tested
in any runner. The same promise through Angular itself (TestBed, `ng test`, Karma) is in `examples/angular-testbed`.
Promise: the results shown always belong to the latest query. The broken variant uses
`mergeMap` instead of `switchMap`, so a slow old response can land last.

```sh
npm install
npx vitest run -t verification
python <kit>/verikit.py init --no-skills && python verification/verikit.py run
```
