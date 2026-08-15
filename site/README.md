# shelfmark — marketing site

Two-page Astro site for shelfmark, built on the Dankaro slide brand
(ink `#111` / orange `#F26722` / warm cream canvas, Manrope + one
Instrument Serif italic word per headline) with tokens and conventions
adapted from `gaptiq-ui-kit`.

## Run

```sh
pnpm install --frozen-lockfile
pnpm dev        # dev server
pnpm build      # static build → dist/
pnpm preview    # serve dist/
```

## Structure

| Page | Purpose |
|---|---|
| `/` | five-chapter product story with the problem, model, governance, proof, and CTA |
| `/docs` | compact operator reference curated from the repo README; keep the two in sync |

The previous marketing routes redirect to the relevant home chapter or docs
section so existing links continue to work.

- `src/styles/global.css` — tokens + base + shared components (panels,
  cards, banners, mono "instrument" readouts).
- `src/scripts/motion.js` — Lenis smooth scroll + GSAP ScrollTrigger.
  Rebuilt on `astro:page-load`, torn down on `astro:before-swap`
  (required for Astro view transitions). Markup opts in via
  `data-reveal` and `data-hero-fade`; reduced motion disables everything.

## Media assets

`public/media/` holds the web-ready copies (WebP/JPEG/MP4, all compressed);
`asset-src/` keeps the full-resolution originals (untracked — local only). Generated with the
Higgsfield CLI (GPT Image 2 for the isometric diagrams and OG banner,
Recraft V4.1 for the vector logo tile, Seedance 2.0 / Kling 3.0 for the two
films), palette-locked to ink `#111` / orange `#F26722` / cream `#EDE9E2`.
To re-render a film from its still, pass the illustration as `--start-image`.
Videos are ambient: muted, looped, paused under `prefers-reduced-motion`.

## Gotcha worth knowing

A `class` passed to a child component (e.g. `<Icon class="pipe-arrow">`)
does **not** get the parent page's scoped-style hash, so scoped selectors
silently miss it. Style such classes as
`.scoped-parent :global(.the-class) { … }`.

Proof points deliberately communicate scale without publishing one machine's
exact corpus measurements as universal benchmarks.
