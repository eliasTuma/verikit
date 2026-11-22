# angular-search-store

The RxJS logic of an Angular search service, kept framework-free so it can be property-tested
in any runner. The Angular `@Injectable` wrapper is in the kit's `templates/angular` (not run).
Promise: the results shown always belong to the latest query. The broken variant uses
`mergeMap` instead of `switchMap`, so a slow old response can land last.

```sh
npm install
npx vitest run -t verification
python <kit>/verikit.py init --no-skills && python verification/verikit.py run
```
