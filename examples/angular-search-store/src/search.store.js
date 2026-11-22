import { Subject, map, switchMap } from 'rxjs';

/**
 * The logic of an Angular search service, framework-free so it can be property-tested.
 * In the app it is wrapped by an @Injectable service that passes HttpClient (see the kit's
 * templates/angular). Promise: the results shown always belong to the latest query.
 */
export function createSearchStore(http) {
  const queries = new Subject();
  let shown = { query: null, items: [] };
  queries
    .pipe(
      // switchMap cancels the previous request, so a slow old response can never land last.
      switchMap((query) => http.get(query).pipe(map((items) => ({ query, items })))),
    )
    .subscribe((result) => {
      shown = result;
    });
  return {
    search: (query) => queries.next(query),
    get shown() {
      return shown;
    },
  };
}
