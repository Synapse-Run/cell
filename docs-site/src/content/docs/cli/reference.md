---
title: CLI Reference
description: Synapse CLI commands for managing sandboxes and templates.
---

## Installation

The CLI is included with the Python SDK:

```bash
pip install synapserun
synapse --help
```

## Sandbox Commands

| Command | Description |
|---------|-------------|
| `synapse sandbox create` | Create a new sandbox |
| `synapse sandbox list` | List running sandboxes |
| `synapse sandbox info <id>` | Get sandbox details |
| `synapse sandbox run <id> <code>` | Execute code in a sandbox |
| `synapse sandbox exec <id> <cmd>` | Run a shell command |
| `synapse sandbox kill <id>` | Kill a sandbox |
| `synapse sandbox pause <id>` | Pause a sandbox |
| `synapse sandbox resume <id>` | Resume a paused sandbox |
| `synapse sandbox snapshot <id>` | Create a filesystem snapshot |

## Template Commands

| Command | Description |
|---------|-------------|
| `synapse template list` | List available templates |
| `synapse template info <name>` | Get template details |
| `synapse template create` | Register a new template |
| `synapse template build <dockerfile>` | Build from Dockerfile |
| `synapse template delete <name>` | Delete a template |

## Examples

```bash
# Create and use a sandbox
synapse sandbox create --template python3 --persistent
# Returns: cell_id: abc123...

synapse sandbox run abc123 "print('Hello!')"
# stdout: Hello!

synapse sandbox list
# ID         STATUS   TEMPLATE   CREATED
# abc123...  running  python3    2026-05-14T12:00:00Z

synapse sandbox kill abc123
```

## Configuration

```bash
# Set API key
export SYNAPSE_API_KEY="cell_sk_live_..."

# Set gateway URL (for self-hosted)
export SYNAPSE_API_URL="http://localhost:8001"
```
