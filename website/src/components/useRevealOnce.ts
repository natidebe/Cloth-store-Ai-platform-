import { useEffect } from 'react';

import ui from '@/styles/ui.module.css';

import s from './sections.module.css';

/** What fades in and rises when it scrolls into view. */
const TARGETS = [
  ui.h2,
  s.card,
  s.step,
  s.group,
  s.control,
  s.demo,
  s.plan,
  s.note,
  s.person,
  s.promise,
  s.question,
  s.cta,
  s.other,
];

const STAGGER_MS = 90; // items in a row arrive one after another
const MAX_DELAY_MS = 360;

/**
 * Scroll animation, once per page load: each item below the screen waits
 * hidden and plays when it first comes into view; after that it stays put
 * (scrolling back doesn't replay it) until the page is reloaded.
 *
 * Safe by default: the page is complete without this (pre-rendered HTML, no
 * JavaScript, old browsers); items already on screen are never hidden; and
 * "reduce motion" turns it off.
 */
export function useRevealOnce() {
  useEffect(() => {
    if (!('IntersectionObserver' in window)) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const hidden = ui.revealHidden;
    const shown = ui.revealShown;
    if (!hidden || !shown) return;

    const selector = TARGETS.filter(Boolean)
      .map((name) => `.${name}`)
      .join(',');
    const items = [...document.querySelectorAll<HTMLElement>(selector)];

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const item = entry.target as HTMLElement;
          item.classList.add(shown);
          item.classList.remove(hidden);
          observer.unobserve(item); // once
        }
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.12 },
    );

    for (const item of items) {
      if (item.getBoundingClientRect().top < window.innerHeight) continue; // already on screen
      const siblings = [...(item.parentElement?.children ?? [])].filter((el) =>
        items.includes(el as HTMLElement),
      );
      const delay = Math.min(siblings.indexOf(item) * STAGGER_MS, MAX_DELAY_MS);
      if (delay > 0) item.style.transitionDelay = `${delay}ms`;
      item.classList.add(hidden);
      observer.observe(item);
    }
    return () => observer.disconnect();
  }, []);
}
