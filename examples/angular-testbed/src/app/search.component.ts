import { Component, inject } from '@angular/core';
import { SearchService } from './search.service';

@Component({
  selector: 'app-search',
  template: `
    <input type="search" aria-label="Search" placeholder="Search" (input)="onInput($event)" />
    @if (results().query) {
      <h2 class="results-for">Results for "{{ results().query }}"</h2>
      <ul>
        @for (item of results().items; track $index) {
          <li>{{ item }}</li>
        }
      </ul>
    }
  `,
})
export class SearchComponent {
  private readonly search = inject(SearchService);
  protected readonly results = this.search.results;

  protected onInput(event: Event): void {
    this.search.search((event.target as HTMLInputElement).value);
  }
}
