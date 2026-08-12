// Astro config for the shelfmark marketing site.
// Static output, no integrations — motion is vanilla GSAP + Lenis.
import { defineConfig } from 'astro/config';

export default defineConfig({
  // Cloudflare Pages production URL — swap when a custom domain is attached.
  site: 'https://shelfmark-cnv.pages.dev',
  trailingSlash: 'never',
  build: { format: 'file' },
});
