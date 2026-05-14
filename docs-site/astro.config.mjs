// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://docs.synapse.run',
  integrations: [
    starlight({
      title: 'Synapse Docs',
      description: 'Sovereign sandboxed code execution. 205× faster than E2B. Cryptographic receipts. Self-hosted.',
      logo: { alt: 'Synapse', replacesTitle: false, src: './src/assets/synapse-logo.svg' },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/Synapse-Run' },
        { icon: 'x.com', label: 'Twitter', href: 'https://x.com/synapserun' },
      ],
      customCss: ['./src/styles/custom.css'],
      head: [
        { tag: 'meta', attrs: { property: 'og:image', content: 'https://docs.synapse.run/og-image.png' } },
      ],
      editLink: { baseUrl: 'https://github.com/Synapse-Run/sdk/edit/main/docs-site/' },
      sidebar: [
        {
          label: 'Getting Started',
          items: [
            { label: 'Quickstart', slug: 'getting-started/quickstart' },
            { label: 'Installation', slug: 'getting-started/installation' },
            { label: 'Migrating from E2B', slug: 'getting-started/migrating-from-e2b' },
          ],
        },
        {
          label: 'Sandbox',
          items: [
            { label: 'Lifecycle', slug: 'sandbox/lifecycle' },
            { label: 'Running Code', slug: 'sandbox/running-code' },
            { label: 'Shell Commands', slug: 'sandbox/commands' },
            { label: 'Filesystem', slug: 'sandbox/filesystem' },
            { label: 'PTY Terminal', slug: 'sandbox/pty' },
            { label: 'Git Integration', slug: 'sandbox/git' },
            { label: 'Environment Variables', slug: 'sandbox/environment-variables' },
            { label: 'Persistence', slug: 'sandbox/persistence' },
            { label: 'Lifecycle Webhooks', slug: 'sandbox/webhooks' },
          ],
        },
        {
          label: 'Templates',
          items: [
            { label: 'Overview', slug: 'templates/overview' },
            { label: 'Custom Templates', slug: 'templates/custom' },
            { label: 'Dockerfile Transpiler', slug: 'templates/dockerfile' },
          ],
        },
        {
          label: 'CLI',
          items: [
            { label: 'Reference', slug: 'cli/reference' },
          ],
        },
        {
          label: 'Agent Integrations',
          items: [
            { label: 'LangChain', slug: 'integrations/langchain' },
            { label: 'CrewAI', slug: 'integrations/crewai' },
            { label: 'OpenAI Agents', slug: 'integrations/openai' },
            { label: 'Claude / Anthropic', slug: 'integrations/claude' },
            { label: 'AutoGen', slug: 'integrations/autogen' },
            { label: 'Vercel AI SDK', slug: 'integrations/vercel' },
          ],
        },
        {
          label: 'Sovereignty',
          badge: { text: 'Unique', variant: 'success' },
          items: [
            { label: 'Execution Receipts', slug: 'sovereignty/receipts' },
            { label: 'Data Sovereignty', slug: 'sovereignty/data-sovereignty' },
            { label: 'Self-Hosting', slug: 'sovereignty/self-hosting' },
            { label: 'Performance', slug: 'sovereignty/performance' },
          ],
        },
        {
          label: 'API Reference',
          items: [
            { label: 'REST API', slug: 'api/rest' },
            { label: 'Python SDK', slug: 'api/python' },
            { label: 'TypeScript SDK', slug: 'api/typescript' },
          ],
        },
      ],
    }),
  ],
});
