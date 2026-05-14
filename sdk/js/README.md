# @runsynapse/sdk

**TypeScript SDK for the current Synapse preview gateway.**

Current surface: execute `.syn`, execute the restricted Python subset, execute pre-compiled Wasm,
and query gateway health. The managed API should be treated as preview/demo; the supported
commercial path is self-hosted.

## Install

```bash
npm i @runsynapse/sdk
```

## Quick Start

```typescript
import { Synapse } from '@runsynapse/sdk';

const client = new Synapse({ baseUrl: 'http://127.0.0.1:8000' });

// Execute .syn code
const result = await client.execute('@f 0 main [ + 21 21 ]');
console.log(result.result);     // 42
console.log(result.latencyMs);

// Execute restricted Python
const py = await client.executePython('result = 21 + 21');
console.log(py.result);         // 42

// Check health
const health = await client.health();
console.log(health.status);
```

## API Reference

### `new Synapse(config)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `apiKey` | `string` | — | Optional edge/API key if your deployment enforces one |
| `baseUrl` | `string` | `https://api.synapserun.dev` | Gateway URL |
| `timeout` | `number` | `15000` | Timeout in ms |
| `maxRetries` | `number` | `3` | Retry count |

### Methods

| Method | Description |
|--------|-------------|
| `execute(code)` | Execute .syn source code |
| `executePython(code)` | Execute the restricted Python subset |
| `executeWasm(bytes)` | Execute pre-compiled .wasm binary |
| `health()` | Check gateway health |

## Compatibility Notes

- The exported `Sandbox` helper is a narrow preview compatibility shim for `runCode()`.
- Historical stateful/file APIs and E2B-style filesystem emulation are not part of the current
  Synapse preview surface.

## License

MIT
