import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://dankaro-projects.github.io',
  base: '/shelfmark',
  trailingSlash: 'always',
  build: { format: 'directory' },
});
