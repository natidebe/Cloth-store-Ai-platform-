import { act, render } from '@testing-library/react';

import ui from '@/styles/ui.module.css';

import { App } from './App';

/** A stand-in for the browser's IntersectionObserver that the test drives. */
class FakeObserver {
  static last: FakeObserver;
  watched = new Set<Element>();
  constructor(private callback: IntersectionObserverCallback) {
    FakeObserver.last = this;
  }
  observe(el: Element) {
    this.watched.add(el);
  }
  unobserve(el: Element) {
    this.watched.delete(el);
  }
  disconnect() {
    this.watched.clear();
  }
  /** These elements scroll into view. */
  show(elements: Element[]) {
    const entries = elements.map((target) => ({ target, isIntersecting: true }));
    this.callback(entries as unknown as IntersectionObserverEntry[], this as never);
  }
}

describe('scroll animation, once per page load', () => {
  beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    // jsdom has no layout: everything is "below the screen" (top 0 < height 0 is false).
    vi.stubGlobal('innerHeight', 0);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('hides what is below the screen and reveals it once when it comes into view', () => {
    const { container } = render(<App lang="en" />);
    const waiting = [...container.querySelectorAll(`.${ui.revealHidden}`)];
    expect(waiting.length).toBeGreaterThan(20); // titles, cards, steps, plans, FAQ rows…

    const first = waiting[0]!;
    act(() => FakeObserver.last.show([first]));
    expect(first).toHaveClass(ui.revealShown!);
    expect(first).not.toHaveClass(ui.revealHidden!);
    // Not watched any more: scrolling back and forth doesn't replay it.
    expect(FakeObserver.last.watched.has(first)).toBe(false);
  });

  it('staggers items in a row', () => {
    const { container } = render(<App lang="en" />);
    const plans = [...container.querySelectorAll('#pricing article')] as HTMLElement[];
    expect(plans.map((plan) => plan.style.transitionDelay)).toEqual(['', '90ms', '180ms']);
  });

  it('does nothing for people who ask for less motion', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduce') }));
    const { container } = render(<App lang="en" />);
    expect(container.querySelectorAll(`.${ui.revealHidden}`)).toHaveLength(0);
  });
});
