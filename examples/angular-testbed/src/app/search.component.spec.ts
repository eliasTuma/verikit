// An ordinary example test, the kind the team already has. It checks one hand-written order.
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { SearchComponent } from './search.component';

describe('SearchComponent', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [SearchComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
  });

  it('shows the results for what the user typed', () => {
    const fixture = TestBed.createComponent(SearchComponent);
    fixture.detectChanges();
    const input: HTMLInputElement = fixture.nativeElement.querySelector('input');
    input.value = 'cat';
    input.dispatchEvent(new Event('input'));

    TestBed.inject(HttpTestingController).expectOne('/api/search?q=cat').flush(['cat food', 'cat toys']);
    fixture.detectChanges();

    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.results-for')?.textContent).toContain('Results for "cat"');
    expect(el.querySelectorAll('li').length).toBe(2);
  });
});
