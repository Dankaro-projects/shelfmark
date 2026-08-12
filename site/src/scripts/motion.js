/* shelfmark — motion engine.
   One Lenis instance + GSAP ScrollTrigger, rebuilt on every Astro page swap.
   Conventions (markup opts in, script stays generic):
     [data-reveal]                fade/rise when the element enters the viewport
     [data-reveal="group"]        stagger the element's direct children
     [data-reveal-delay="0.2"]    extra delay in seconds
     .hero-line > span            page-load headline lines (masked rise)
   Reduced motion: nothing animates, everything is simply visible. */

import Lenis from 'lenis';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

const EASE = 'expo.out'; // closest named ease to the house cubic-bezier(0.16,1,0.3,1)
let lenis = null;
let tickerFn = null;

const reducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function setupLenis() {
  if (reducedMotion()) return;
  // The home story uses native snap scrolling instead of a second scroll engine.
  if (document.querySelector('.home-story')) return;
  lenis = new Lenis({ duration: 1.15, touchMultiplier: 1.6 });
  lenis.on('scroll', ScrollTrigger.update);
  tickerFn = (t) => lenis.raf(t * 1000);
  gsap.ticker.add(tickerFn);
  gsap.ticker.lagSmoothing(0);
}

function heroIntro() {
  const lines = document.querySelectorAll('.hero-line > span');
  const rest = document.querySelectorAll('[data-hero-fade]');
  if (!lines.length && !rest.length) return;

  const tl = gsap.timeline({ defaults: { ease: EASE } });
  if (lines.length) {
    tl.fromTo(
      lines,
      { yPercent: 112 },
      { yPercent: 0, duration: 1.1, stagger: 0.09 },
      0.05
    );
  }
  if (rest.length) {
    tl.fromTo(
      rest,
      { opacity: 0, y: 22 },
      { opacity: 1, y: 0, duration: 0.9, stagger: 0.08 },
      0.45
    );
  }
}

function scrollReveals() {
  document.querySelectorAll('[data-reveal]').forEach((el) => {
    const delay = parseFloat(el.dataset.revealDelay || '0');
    const isGroup = el.dataset.reveal === 'group';
    const targets = isGroup ? Array.from(el.children) : el;

    if (isGroup) gsap.set(el, { opacity: 1 }); // group container never hides itself

    gsap.fromTo(
      targets,
      { opacity: 0, y: 30 },
      {
        opacity: 1,
        y: 0,
        duration: 1,
        delay,
        ease: EASE,
        stagger: isGroup ? 0.1 : 0,
        scrollTrigger: { trigger: el, start: 'top 80%', once: true },
      }
    );
  });
}

function copyChips() {
  document.querySelectorAll('.chip-copy').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const text = btn.dataset.copy || '';
      try {
        await navigator.clipboard.writeText(text);
        const prev = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(() => (btn.textContent = prev), 1400);
      } catch {
        /* clipboard unavailable — leave the command selectable */
      }
    });
  });
}

function init() {
  if (reducedMotion()) {
    document.documentElement.classList.remove('motion-ok');
    // Ambient films hold still too — the poster frame carries the content.
    document.querySelectorAll('video[autoplay]').forEach((v) => {
      v.pause();
      v.removeAttribute('autoplay');
    });
    copyChips();
    return;
  }
  document.documentElement.classList.add('motion-ok');
  setupLenis();
  heroIntro();
  scrollReveals();
  copyChips();
  // Late layout shifts (fonts) can move trigger positions.
  requestAnimationFrame(() => ScrollTrigger.refresh());
}

function cleanup() {
  ScrollTrigger.getAll().forEach((st) => st.kill());
  if (tickerFn) gsap.ticker.remove(tickerFn);
  if (lenis) lenis.destroy();
  lenis = null;
  tickerFn = null;
}

document.addEventListener('astro:page-load', init);
document.addEventListener('astro:before-swap', cleanup);
