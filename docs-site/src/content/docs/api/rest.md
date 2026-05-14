---
title: REST API Reference
description: Complete HTTP API reference for the Synapse Cell Gateway.
---

## Base URL

- **Managed**: `https://cell.synapserun.dev`
- **Self-hosted**: `http://localhost:8001`

## Authentication

```
Authorization: Bearer cell_sk_live_...
```

## Endpoints

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/health` | Health check |
| GET | `/v1/metrics` | Usage metrics |
| GET | `/v1/stats` | Dashboard stats |

### Cells (Sandboxes)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/cells` | Create a cell |
| GET | `/v1/cells` | List cells (paginated) |
| GET | `/v1/cells/{id}` | Get cell info |
| DELETE | `/v1/cells/{id}` | Kill a cell |

### Lifecycle

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/v1/cells/{id}/timeout` | Set timeout |
| POST | `/v1/cells/{id}/refresh` | Reset timer |
| GET | `/v1/cells/{id}/is_running` | Heartbeat |
| POST | `/v1/cells/{id}/pause` | Pause + snapshot |
| POST | `/v1/cells/{id}/resume` | Resume |
| GET | `/v1/cells/{id}/snapshots` | List snapshots |

### Execution

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/cells/{id}/exec` | Execute code (blocking) |
| POST | `/v1/cells/{id}/exec/stream` | Execute code (SSE) |
| POST | `/v1/cells/{id}/cmd` | Shell command |

### Filesystem

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/cells/{id}/files` | Write file |
| GET | `/v1/cells/{id}/files?path=` | Read file |
| DELETE | `/v1/cells/{id}/files?path=` | Remove file |
| GET | `/v1/cells/{id}/files/list` | List directory |
| GET | `/v1/cells/{id}/files/exists` | Check existence |
| GET | `/v1/cells/{id}/files/info` | File metadata |
| POST | `/v1/cells/{id}/files/mkdir` | Create directory |
| POST | `/v1/cells/{id}/files/rename` | Rename/move |

### Processes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/cells/{id}/processes` | List processes |
| GET | `/v1/cells/{id}/processes/{cmd}` | Poll process |
| POST | `/v1/cells/{id}/processes/{cmd}/kill` | Kill process |
| POST | `/v1/cells/{id}/processes/{cmd}/stdin` | Send stdin |

### Templates

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/templates` | Register template |
| GET | `/v1/templates` | List templates |
| GET | `/v1/templates/{name}` | Get template info |
| PATCH | `/v1/templates/{name}` | Update template |
| DELETE | `/v1/templates/{name}` | Delete template |

### Network

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/cells/{id}/fetch` | HTTP proxy |

### Events & Webhooks

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/events` | Global lifecycle events (last 100) |
| GET | `/v1/cells/{id}/events` | Per-cell lifecycle events |
| POST | `/v1/webhooks` | Register a webhook |
| GET | `/v1/webhooks` | List all webhooks |
| DELETE | `/v1/webhooks/{webhook_id}` | Delete a webhook |

## OpenAPI Spec

The full OpenAPI 3.0.3 spec is available at:

```
GET /v1/openapi.yaml
```
