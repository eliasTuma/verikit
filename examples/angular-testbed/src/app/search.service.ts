import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, map, switchMap } from 'rxjs';

export interface SearchResults {
  query: string;
  items: string[];
}

/** Search-as-you-type: every keystroke sends a query, the latest results are shown. */
@Injectable({ providedIn: 'root' })
export class SearchService {
  private readonly http = inject(HttpClient);
  private readonly queries = new Subject<string>();
  private readonly shown = signal<SearchResults>({ query: '', items: [] });

  /** The results on screen. Promise: they always belong to the latest query. */
  readonly results = this.shown.asReadonly();

  constructor() {
    this.queries
      .pipe(
        // switchMap cancels the request for the previous query, so a slow old response can
        // never land after - and overwrite - the results of a newer query.
        switchMap((query) =>
          this.http
            .get<string[]>('/api/search', { params: { q: query } })
            .pipe(map((items) => ({ query, items }))),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((results) => this.shown.set(results));
  }

  search(query: string): void {
    this.queries.next(query);
  }
}
