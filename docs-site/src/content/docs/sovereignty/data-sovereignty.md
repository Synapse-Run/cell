---
title: Data Sovereignty
description: Your code and data never leave your infrastructure.
---

## Sovereignty Guarantees

- **No data exfiltration** — Code executes in Wasm isolates with no network access by default
- **EU/CA data residency** — Production gateway runs in Helsinki (EU) with Canadian fallback
- **Self-hosted option** — Run everything on your own hardware. Single binary. No cloud dependency
- **No telemetry** — The gateway sends zero usage data to Synapse or any third party
- **Open source** — Core runtime is Apache 2.0 licensed. Audit every line of code

## Architecture

```
┌──────────────────────────────────┐
│  Your Infrastructure             │
│  ┌────────────┐  ┌─────────────┐│
│  │ Synapse    │  │ Wasm Sandbox││
│  │ Gateway    │──│ (isolated)  ││
│  │ (Rust)     │  │ No network  ││
│  └────────────┘  └─────────────┘│
│         ↕                        │
│  ┌────────────┐                  │
│  │ Your App   │                  │
│  └────────────┘                  │
└──────────────────────────────────┘
     No data leaves this box.
```
