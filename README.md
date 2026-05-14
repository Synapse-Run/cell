<p align="center">
  <strong>Synapse Cell</strong><br>
  The sandboxed execution engine for AI agents.<br>
  <em>140× faster than E2B. Cryptographic receipts. Self-hosted. Open source.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/synapserun/"><img src="https://img.shields.io/pypi/v/synapserun" alt="PyPI"></a>
  <a href="https://www.npmjs.com/package/@runsynapse/sdk"><img src="https://img.shields.io/npm/v/@runsynapse/sdk" alt="npm"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License: AGPL-3.0"></a>
</p>

---

## What is Synapse Cell?

Cell is a **WebAssembly-native sandbox** for executing AI agent tool calls. While E2B boots a Linux kernel per sandbox (Firecracker microVMs), Cell compiles code to Wasm and runs it on bare metal — no kernel, no container, no cold start.

**Why it exists:** AI agents make tool calls. Those calls take 80-500ms on cloud sandboxes. Cell makes them take <1ms. Every execution is cryptographically receipted with SHA-256 for compliance and auditability.

```python
pip install synapserun
```

## Quick Start

```python
from synapse.cell import Cell

cell = Cell(api_url="local")
result = cell.run("print(2 + 2)")
print(result.stdout)       # '4\n'
print(result.latency_ms)   # ~0.5ms
print(result.receipt)       # CellReceipt(receipt_hash='a4f2...')
cell.kill()
```

Zero configuration. No Docker. No API key. The Wasm runtimes ship inside the wheel.

## E2B Migration — One Line Change

```python
# Before:
# from e2b_code_interpreter import Sandbox

# After:
from synapse.e2b_compat import Sandbox

sbx = Sandbox(api_url="local")
result = sbx.run_code("print('hello')")
print(result.stdout)  # 'hello\n'
```

## Performance

Measured head-to-head against live E2B API. Reproducible in 2 minutes.

| Workload | Cell p50 | E2B p50 | Speedup |
|----------|----------|---------|---------|
| Simple eval (print, arithmetic) | **0.5ms** | 79ms | **~140×** |
| Math heavy (fib + sort 50K) | **0.5ms** | 80ms | **~140×** |
| File I/O (100KB write/read/hash) | 63ms | 269ms | **4.3×** |
| 5-step session | 235ms | 480ms | **2×** |
| Memory per sandbox | ~1 MB | ~133 MB | **133× density** |

**Methodology:** `benchmarks/e2b_apples_to_apples.py --runs 100 --warmup 10`  
**Hardware:** Apple M4 Pro, macOS 15.3, Cell gateway in release mode, E2B Pro tier.

> The speed advantage is structural. Wasm has no kernel to boot.
> E2B cannot match this without rewriting their stack.

## Features

### Execution
- **Python 3.12** (CPython-WASI) — full stdlib, decorators, generators, async
- **JavaScript** (QuickJS-WASI) — ES2023, closures, typed arrays
- **Sub-ms fast path** — simple code transpiles to .syn, skipping CPython entirely
- **Streaming** — `cell.run(code, on_stdout=callback)` with real-time SSE

### Sandbox Management
- Create / kill / pause / resume / snapshot
- Persistent state across runs (Pro tier)
- Volumes (persistent cross-sandbox storage)
- Templates (custom sandbox configurations)
- Metadata, env vars, timeout control

### Developer Tools
- **Filesystem** — `cell.files.read/write/list/exists/make_dir/rename/remove`
- **Git** — `cell.git.clone/commit/push/pull/checkout/status` (21 methods)
- **PTY** — `cell.pty.create(on_data=...)` for interactive terminals
- **CLI** — `synapse sandbox create`, `synapse sandbox run`, `synapse template build`

### AI Agent Integrations
```bash
pip install synapserun[langchain]    # LangChain tool
pip install synapserun[crewai]       # CrewAI tool
pip install synapserun[openai-agents] # OpenAI Agents SDK
pip install synapserun[autogen]      # AutoGen executor
pip install synapserun[llamaindex]   # LlamaIndex tool
pip install synapserun[all]          # Everything
```

### Compliance & Security
- **SHA-256 receipt chain** — every execution produces a cryptographic receipt
- **Z3 verification** — formal verification of code properties (sub-ms)
- **AGPL + Apache 2.0** — inspect every line, self-host forever
- **No CLOUD Act** — runs on your hardware, your jurisdiction
- **OSFI E-23 / EU AI Act** ready — receipted, auditable, reproducible

## Deployment Modes

| Mode | Command | When |
|------|---------|------|
| **Local** | `Cell(api_url="local")` | Development, CI, edge, embedded |
| **Self-hosted** | `docker run ghcr.io/synapse-run/cell` | Production, fleet management |
| **Managed** | `Cell(api_key="sk_...")` | Coming soon |

## Architecture

```
AI Agent → tool call → Synapse SDK → Wasm sandbox → result + receipt
                                         │
                                    No kernel boot
                                    No container
                                    No cold start
                                    ~0.5ms p50
```

Cell uses [wasmtime](https://wasmtime.dev/) to execute sandboxed code. The gateway is a 12,000-line Rust binary that manages sandbox lifecycle, handles HTTP/WebSocket APIs, and produces cryptographic receipts.

## Self-Hosting

```bash
# Option 1: Docker
docker run -p 8002:8002 ghcr.io/synapse-run/cell

# Option 2: Build from source
cd gateway && cargo build --release
./target/release/synapse-gateway
```

```bash
curl http://localhost:8002/v1/health
# {"status":"ok","service":"synapse-cell","version":"0.2.0"}
```

## API Reference

Full OpenAPI 3.1 spec: `gateway/openapi.yaml` (27 endpoints)

Key endpoints:
- `POST /v1/cells` — Create a sandbox
- `POST /v1/cells/{id}/exec` — Execute code
- `GET /v1/cells/{id}/files/list` — List files
- `POST /v1/cells/{id}/exec/stream` — SSE streaming execution
- `POST /v1/cells/{id}/pause` / `resume` — Lifecycle management
- `GET /v1/health` — Health check

## Project Structure

```
cell/
├── gateway/          # Rust gateway (12K LOC)
│   ├── src/
│   │   ├── main.rs         # HTTP server, WebSocket, rate limiter
│   │   ├── cell.rs         # Sandbox lifecycle, Wasm execution
│   │   ├── cell_api.rs     # REST API routes
│   │   ├── compiler.rs     # .syn compiler
│   │   ├── transpiler.rs   # Python → .syn transpiler
│   │   └── ...
│   ├── openapi.yaml        # API spec
│   └── Cargo.toml
├── sdk/              # Python + JS SDKs
│   ├── synapse/            # Python SDK (9.6K LOC, 22 modules)
│   ├── js/                 # TypeScript SDK
│   ├── tests/              # Test suite
│   └── pyproject.toml
├── benchmarks/       # Reproducible benchmarks
├── templates/        # Sandbox templates
└── docs/             # Documentation
```

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). We use conventional commits (`feat:`, `fix:`, `docs:`, `tests:`, `bench:`).

## License

- **Gateway + SDK:** [AGPL-3.0](./LICENSE-AGPL.md) — use freely, contribute changes back
- **Commercial License:** [Available](./COMMERCIAL_LICENSE.md) for closed-source deployments
- **Wasm Runtimes:** Apache 2.0 (wasmtime)

## Links

- **Website:** [synapserun.dev](https://synapserun.dev)
- **Documentation:** [synapserun.dev/docs](https://synapserun.dev/docs)
- **npm:** [@runsynapse/sdk](https://www.npmjs.com/package/@runsynapse/sdk) <sup>†</sup>
- **PyPI:** [synapserun](https://pypi.org/project/synapserun/)
- **Security:** [SECURITY.md](./SECURITY.md)

<sup>†</sup> The npm scope `@runsynapse/sdk` was published before the `synapserun.dev` brand was canonicalized; a migration to `@synapserun/sdk` is planned for a future major release. The current package remains supported and active.

---

<p align="center">
Built by <a href="https://synapserun.dev">Synapse Run</a>, Canada 🇨🇦<br>
<em>Wasm-native. Sovereign. Self-hosted.</em>
</p>
