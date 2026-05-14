---
title: PTY Terminal
description: Interactive terminal sessions via WebSocket.
---

## WebSocket Terminal

Connect to a persistent terminal inside the sandbox:

```typescript
import { Cell } from '@runsynapse/sdk';

const cell = await Cell.create({ persistent: true });
const ws = cell.terminal();

ws.onmessage = (event) => {
  process.stdout.write(event.data);
};

ws.send('echo "Hello from PTY!"\n');
```

## Python

```python
import asyncio
import websockets

async def pty_session():
    cell = Cell(persistent=True)
    ws_url = f"ws://localhost:8001/v1/cells/{cell.cell_id}/ws"
    
    async with websockets.connect(ws_url) as ws:
        await ws.send('echo "Hello from PTY!"')
        response = await ws.recv()
        print(response)
    
    cell.kill()
```
