# Changelog

All notable changes to Synapse Cell will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.6.0] - 2026-05-14

### Added

- **TypeScript SDK parity** — 14 new methods in the published npm package:
  - Lifecycle: `pause()`, `resume()`, `setTimeout()`, `keepAlive()`, `isRunning()`
  - Streaming: `runCodeStream()` (SSE)
  - Processes: `listProcesses()`, `sendStdin()`, `killProcess()`
  - Environment: `getEnvs()`, `setEnvs()`
  - Metadata: `setMetadata()`, `getMetadata()`
  - Events: `getLifecycleEvents()`, `registerWebhook()`, `listWebhooks()`, `deleteWebhook()`
  - Static: `Sandbox.list()`, `listSnapshots()`
- **Documentation site** — 31-page Starlight docs deployed to GitHub Pages
  - Getting started: quickstart, installation, E2B migration guide
  - 9 sandbox docs: lifecycle, code execution, commands, filesystem, git, env vars, PTY, persistence, webhooks
  - 6 integration guides: LangChain, CrewAI, OpenAI, Claude, AutoGen, Vercel
  - 4 sovereignty docs: receipts, data sovereignty, self-hosting, performance
  - 3 API references: REST, Python SDK, TypeScript SDK
  - CLI reference, full-text search via Pagefind
- **Dashboard rewrite** — production-grade management UI with cell list, log streaming, template management
- **OpenAPI spec** — webhook and lifecycle event endpoint definitions + schemas
- **GitHub Pages deployment** — automated via `.github/workflows/docs.yml`

### Changed

- **CI hardened** — Clippy now runs in strict mode (`-D warnings`), rustfmt check is gating
- **Benchmark numbers updated** — README reflects latest 205× p50 speedup measurements
- **README badges** — added docs site link, CI status badge

### Fixed

- 20+ Clippy warnings in gateway Rust code (mechanical auto-fixes: `is_some_and`, unused imports, redundant borrows)
- Docs site base path for GitHub Pages (`/cell/` prefix)

## [0.5.2] - 2026-05-14

### Added

- Initial public release on PyPI and npm
- Rust gateway with 40+ API routes
- Python SDK (10,734 lines, 22 modules)
- TypeScript SDK (basic: create, run, files, kill)
- E2B compatibility shim
- Benchmark suite
- CI pipeline (Rust build, Python tests, JS build, CodeQL, Military Audit)

[0.6.0]: https://github.com/Synapse-Run/cell/compare/v0.5.2...v0.6.0
[0.5.2]: https://github.com/Synapse-Run/cell/releases/tag/v0.5.2
