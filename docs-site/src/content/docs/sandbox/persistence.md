---
title: Persistence
description: Persistent sandboxes that survive restarts.
---

## Persistent vs Ephemeral

| Feature | Ephemeral | Persistent |
|---------|-----------|------------|
| State across calls | ✅ Yes | ✅ Yes |
| Survives gateway restart | ❌ No | ✅ Yes |
| Filesystem snapshots | ❌ No | ✅ Yes |
| Pause/Resume | ❌ No | ✅ Yes |
| Default | ❌ | ✅ |

## Creating Persistent Sandboxes

```python
cell = Cell(persistent=True)  # Default
```

## Snapshots

```python
# Create a snapshot
snapshot = cell.pause()
print(snapshot.snapshot_id)

# List snapshots
snapshots = cell.snapshots()

# Resume from snapshot
cell.resume()
```

## Reconnecting

```python
# Save the cell ID
cell_id = cell.cell_id

# Later, reconnect
cell = Cell.connect(cell_id)
result = cell.run("print('I remember everything!')")
```
