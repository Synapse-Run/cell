---
title: Python SDK Reference
description: Complete Python SDK API reference.
---

## Cell Class

```python
from synapse import Cell
```

### Constructor / Factory

| Method | Description |
|--------|-------------|
| `Cell(**opts)` | Create a new cell |
| `Cell.create(**opts)` | Async factory method |
| `Cell.connect(id)` | Attach to existing cell |
| `Cell.list()` | List all cells |

### Execution

| Method | Description |
|--------|-------------|
| `cell.run(code, **opts)` | Execute code (blocking or streaming) |
| `cell.command(cmd, **opts)` | Run shell command |
| `cell.terminal()` | WebSocket PTY |

### Filesystem

| Method | Description |
|--------|-------------|
| `cell.write_file(path, content)` | Write a file |
| `cell.read_file(path)` | Read a file |
| `cell.list_files(path)` | List directory |
| `cell.file_exists(path)` | Check existence |
| `cell.file_info(path)` | File metadata |
| `cell.remove_file(path)` | Remove file/dir |
| `cell.make_dir(path)` | Create directory |
| `cell.rename_file(old, new)` | Rename/move |

### Git

| Method | Description |
|--------|-------------|
| `cell.git.clone(url)` | Clone repository |
| `cell.git.status()` | Working tree status |
| `cell.git.add(path)` | Stage files |
| `cell.git.commit(msg)` | Commit changes |
| `cell.git.push()` | Push to remote |
| _...and 14 more_ | See [Git Integration](/sandbox/git/) |

### Lifecycle

| Method | Description |
|--------|-------------|
| `cell.kill()` | Destroy the cell |
| `cell.pause()` | Pause + snapshot |
| `cell.resume()` | Resume |
| `cell.set_timeout(s)` | Set timeout |
| `cell.keep_alive(s)` | Reset timer |
| `cell.get_info()` | Get SandboxInfo |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `cell.cell_id` | `str` | Unique cell ID |
| `cell.template` | `str` | Runtime template |
| `cell.persistent` | `bool` | Persistence flag |
| `cell.executions` | `int` | Execution count |
