---
title: Self-Hosting
description: Deploy Synapse on your own infrastructure.
---

## Quick Start

```bash
docker run -d -p 8001:8001 ghcr.io/synapse-run/cell-gateway:latest
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_PORT` | `8001` | Gateway HTTP port |
| `SYNAPSE_AUTH_ENABLED` | `false` | Enable API key auth |
| `SYNAPSE_MAX_CELLS` | `1000` | Max concurrent sandboxes |
| `SYNAPSE_TIMEOUT_MS` | `3600000` | Default cell timeout |
| `SYNAPSE_DATA_DIR` | `/var/synapse` | Persistent data directory |

## System Requirements

- **OS**: Linux (x86_64 or ARM64), macOS (Apple Silicon)
- **Memory**: 2GB minimum, 8GB recommended
- **Disk**: 10GB for gateway + data
- **Runtime**: Docker or direct Rust binary
