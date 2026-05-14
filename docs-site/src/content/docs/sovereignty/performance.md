---
title: Performance
description: 205× faster than E2B. Sub-millisecond execution.
---

## Benchmark Summary

| Metric | E2B | Synapse | Advantage |
|--------|-----|---------|-----------|
| Cold start | ~200ms | **<1ms** | **205×** |
| Code execution | ~50ms | **0.24ms** | **208×** |
| File write | ~30ms | **0.1ms** | **300×** |
| Isolation overhead | Docker (~50MB) | Wasm (~2MB) | **25×** less memory |

## Why So Fast?

Synapse uses **Wasm isolation** instead of Docker containers:

1. **No container boot** — Wasm modules instantiate in microseconds
2. **No filesystem overlay** — Direct memory-mapped I/O
3. **No network namespace** — Wasm sandboxes are memory-isolated by default
4. **Ahead-of-time compilation** — Templates are pre-compiled to native code
5. **Single binary** — No orchestration overhead (no containerd, runc, or shim)

## Reproducibility

Every execution is fully deterministic. Same code + same template = same result hash, every time.
