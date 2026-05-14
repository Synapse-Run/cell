---
title: TypeScript SDK Reference
description: Complete TypeScript SDK API reference.
---

## Installation

```bash
npm install @runsynapse/sdk
```

## Cell Class

```typescript
import { Cell } from '@runsynapse/sdk';
```

### Factory Methods

| Method | Description |
|--------|-------------|
| `Cell.create(opts?)` | Create a new cell |
| `Cell.connect(id, opts?)` | Attach to existing cell |
| `Cell.list(opts?)` | Returns `SandboxPaginator` |

### Execution

| Method | Description |
|--------|-------------|
| `cell.run(code, opts?)` | Execute code (blocking or SSE streaming) |
| `cell.command(cmd, opts?)` | Shell command (or `CommandHandle` if background) |
| `cell.terminal()` | Returns `WebSocket` for PTY |

### Filesystem

| Method | Description |
|--------|-------------|
| `cell.writeFile(path, content)` | Write a file |
| `cell.readFile(path)` | Read a file |
| `cell.listFiles(path?)` | List directory → `EntryInfo[]` |
| `cell.fileExists(path)` | Check existence |
| `cell.fileInfo(path)` | File metadata → `EntryInfo` |
| `cell.removeFile(path)` | Remove file/dir |
| `cell.makeDir(path)` | Create directory |
| `cell.renameFile(old, new)` | Rename/move |

### Lifecycle

| Method | Description |
|--------|-------------|
| `cell.kill()` | Destroy the cell |
| `cell.fetch(url, opts?)` | HTTP proxy → `FetchResult` |
| `cell.info()` | Raw cell info |
| `cell.getInfo()` | Typed `SandboxInfo` |

## Sandbox Class (E2B Compat)

```typescript
import { Sandbox } from '@runsynapse/sdk';

const sbx = await Sandbox.create();
const result = await sbx.runCode("print(42)");
await sbx.kill();
```

## CommandHandle

```typescript
const handle = await cell.command('sleep 5', { background: true });
await handle.wait(30000);
console.log(await handle.stdout);
console.log(await handle.exitCode);
```
