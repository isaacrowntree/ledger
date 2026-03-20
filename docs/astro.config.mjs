import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://isaacrowntree.github.io',
  base: '/ledger',
  legacy: {
    collections: true,
  },
  integrations: [
    starlight({
      title: 'Ledger',
      favicon: '/favicon.svg',
      customCss: ['./src/styles/custom.css'],
      social: [
        { icon: 'github', label: 'GitHub', link: 'https://github.com/isaacrowntree/ledger' },
      ],
      sidebar: [
        {
          label: 'Getting Started',
          items: [
            { label: 'Installation', slug: 'installation' },
            { label: 'Getting Started', slug: 'getting-started' },
          ],
        },
        {
          label: 'Configuration',
          items: [
            { label: 'Accounts', slug: 'config-accounts' },
            { label: 'Categories & Rules', slug: 'config-categories' },
            { label: 'Tax (ATO)', slug: 'config-tax' },
          ],
        },
        {
          label: 'Usage',
          items: [
            { label: 'CLI Commands', slug: 'cli' },
            { label: 'Dashboard', slug: 'dashboard' },
            { label: 'API Reference', slug: 'api' },
          ],
        },
        {
          label: 'Concepts',
          items: [
            { label: 'Source of Truth', slug: 'source-of-truth' },
            { label: 'Business Splits', slug: 'business-splits' },
            { label: 'Adding a Bank', slug: 'adding-a-bank' },
          ],
        },
      ],
    }),
  ],
});
