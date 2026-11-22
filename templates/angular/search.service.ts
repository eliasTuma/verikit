// Template (NOT run by the kit): an Angular service that delegates its RxJS logic to a plain,
// property-testable store. The store itself is runnable: examples/angular-search-store/src/search.store.js
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { createSearchStore } from './search.store';

@Injectable({ providedIn: 'root' })
export class SearchService {
  private readonly store = createSearchStore({
    get: (q: string) => this.http.get<string[]>('/api/search', { params: { q } }),
  });

  constructor(private readonly http: HttpClient) {}

  search(query: string): void { this.store.search(query); }
  get shown() { return this.store.shown; }
}
