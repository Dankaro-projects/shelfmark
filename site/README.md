# shelfmark — marketing site

Multi-page Astro site for shelfmark, built on the Dankaro slide brand
(ink `#111` / orange `#F26722` / warm cream canvas, Manrope + one
Instrument Serif italic word per headline) with tokens and conventions
adapted from `gaptiq-ui-kit`.

## Run

```sh
npm install
npm run dev        # dev server
npm run build      # static build → dist/
npm run preview    # serve dist/
```

## Structure

| Page | Slide it carries |
|---|---|
| `/` | hero + one beat per chapter, closing CTA |
| `/problem` | Enterprise knowledge already exists |
| `/model` | A governed map of what exists |
| `/coverage` | Works where your data lives |
| `/proof` | Fast to index. Cheap to query. |
| `/get-started` | install, MCP wiring, the five tools |
| `/docs` | technical instructions — curated from the repo README (commands, config sections, governance, guards, hooks); keep the two in sync |

- `src/styles/global.css` — tokens + base + shared components (panels,
  cards, banners, mono "instrument" readouts).
- `src/scripts/motion.js` — Lenis smooth scroll + GSAP ScrollTrigger.
  Rebuilt on `astro:page-load`, torn down on `astro:before-swap`
  (required for Astro view transitions). Markup opts in via
  `data-reveal`, `data-count-to`, `data-grow`, `data-draw`,
  `data-hero-fade`; reduced motion disables everything.
- `src/components/DotMap.astro` — the signature "governed map" visual,
  generated at build time with a fixed PRNG seed (deterministic builds).

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

Proof-point numbers (40,000 files / 65 GB under 2 s; 74 GB OneDrive corpus
on Windows ARM64; 500–1,000 token discovery footprint) come from the
Dankaro proof-points slide — update them there and here together.
