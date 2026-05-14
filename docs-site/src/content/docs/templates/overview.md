---
title: Templates Overview
description: Pre-configured runtime environments for sandboxes.
---

## Built-in Templates

| Template | Runtime | Description |
|----------|---------|-------------|
| `python3` | Python 3.11 | Default. Full stdlib + pip |
| `javascript` | Node.js 20 | V8 isolate |
| `synapse` | .syn native | Wasm-native execution |

## Using Templates

```python
# Python (default)
cell = Cell(template="python3")

# JavaScript
cell = Cell(template="javascript")
```

## Listing Templates

```python
from synapse.cli import main
# or via CLI:
# synapse template list
```
