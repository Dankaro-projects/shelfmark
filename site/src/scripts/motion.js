/* shelfmark — motion engine.
   One Lenis instance + GSAP ScrollTrigger, rebuilt on every Astro page swap.
   Conventions (markup opts in, script stays generic):
     [data-reveal]                fade/rise when the element enters the viewport
     [data-reveal="group"]        stagger the element's direct children
     [data-reveal-delay="0.2"]    extra delay in seconds
     [data-count-to="40000"]      mono counter, counts up on entry
     [data-draw]                  SVG stroke draw (expects pathLength-normalised dash)
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

function counters() {
  document.querySelectorAll('[data-count-to]').forEach((el) => {
    const target = parseFloat(el.dataset.countTo);
    const decimals = parseInt(el.dataset.countDecimals || '0', 10);
    const obj = { v: 0 };
    gsap.to(obj, {
      v: target,
      duration: 1.6,
      ease: 'expo.out',
      scrollTrigger: { trigger: el, start: 'top 85%', once: true },
      onUpdate() {
        el.textContent = obj.v.toLocaleString('en-US', {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        });
      },
    });
  });
}

function drawSVGs() {
  document.querySelectorAll('[data-draw]').forEach((el) => {
    gsap.fromTo(
      el,
      { strokeDashoffset: parseFloat(el.dataset.draw || '1') },
      {
        strokeDashoffset: parseFloat(el.dataset.drawTo || '0'),
        duration: 1.6,
        ease: 'power2.inOut',
        scrollTrigger: { trigger: el, start: 'top 80%', once: true },
      }
    );
  });
}

/* Meter fills: [data-grow="92"] animates width 0 → 92%. */
function growBars() {
  document.querySelectorAll('[data-grow]').forEach((el) => {
    gsap.fromTo(
      el,
      { width: '0%' },
      {
        width: `${parseFloat(el.dataset.grow)}%`,
        duration: 1.4,
        ease: 'expo.out',
        scrollTrigger: { trigger: el, start: 'top 85%', once: true },
      }
    );
  });
}

/* Token matrix: dots cascade in, the lit ones pop last. */
function tokenDots() {
  document.querySelectorAll('.token-dots').forEach((grid) => {
    const cells = grid.querySelectorAll('.td');
    gsap.fromTo(
      cells,
      { opacity: 0, scale: 0.4 },
      {
        opacity: 1,
        scale: 1,
        duration: 0.4,
        ease: 'back.out(2)',
        stagger: 0.02,
        scrollTrigger: { trigger: grid, start: 'top 85%', once: true },
      }
    );
  });
}

/* The governed-map hero: dots assemble, a scan sweeps, permitted dots light. */
function dotMap() {
  const map = document.querySelector('[data-dotmap]');
  if (!map) return;
  const dots = map.querySelectorAll('.dm-dot');
  const lit = map.querySelectorAll('.dm-dot.lit');
  const scan = map.querySelector('.dm-scan');

  const tl = gsap.timeline({ delay: 0.35 });
  tl.fromTo(
    dots,
    { opacity: 0, scale: 0 },
    {
      opacity: 0.2,
      scale: 1,
      duration: 0.5,
      ease: 'back.out(2)',
      stagger: { each: 0.004, from: 'random' },
      transformOrigin: '50% 50%',
      // lit dots start ink-coloured like everything else; the scan reveals them
      onStart: () => gsap.set(lit, { fill: '#111111' }),
    }
  );
  if (scan) {
    tl.fromTo(
      scan,
      { attr: { x1: 0, x2: 0 }, opacity: 1 },
      { attr: { x1: 520, x2: 520 }, duration: 1.4, ease: 'power2.inOut' },
      '-=0.1'
    ).to(scan, { opacity: 0, duration: 0.3 }, '-=0.2');
  }
  tl.to(
    lit,
    {
      fill: '#f26722',
      opacity: 1,
      scale: 1.45,
      duration: 0.45,
      ease: 'back.out(3)',
      stagger: 0.05,
      transformOrigin: '50% 50%',
    },
    '-=1.05'
  );
}

/* Vertical conduit line (home): fills as the page scrolls. */
function conduit() {
  const fill = document.querySelector('.conduit-fill');
  if (!fill) return;
  gsap.fromTo(
    fill,
    { height: '0%' },
    {
      height: '100%',
      ease: 'none',
      scrollTrigger: {
        trigger: document.body,
        start: 'top top',
        end: 'bottom bottom',
        scrub: 0.15,
      },
    }
  );
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
  counters();
  drawSVGs();
  growBars();
  tokenDots();
  dotMap();
  conduit();
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
