# synapserun

**The E2B-compatible sandbox that runs your code on Wasm. 200× faster cold start. Canadian sovereign. Cryptographic execution receipts.**

[![PyPI](https://img.shields.io/pypi/v/synapserun)](https://pypi.org/project/synapserun/)
[![npm](https://img.shields.io/npm/v/synapserun)](https://www.npmjs.com/package/synapserun)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://github.com/Synapse-Run/cell/blob/main/LICENSE-AGPL.md)

## Install

```bash
pip install synapserun
```

The install bundles real CPython 3.12 and QuickJS 0.14 compiled to Wasm.
Local execution works with **zero configuration** — no Docker, no gateway,
no API key needed for the `api_url="local"` path.

For Node.js:

```bash
npm install synapserun
```

## Quick Start — 5 seconds to first execution

```python
from synapse.cell import Cell

cell = Cell(api_url="local")
result = cell.run("print(2 + 2)")
print(result.stdout)      # '4\n'
print(result.latency_ms)  # ~0.5ms (simple path) / ~63ms (full CPython)
cell.kill()
```

### Full CPython — decorators, generators, async, any stdlib import

```python
with Cell(api_url="local") as cell:
    result = cell.run("""
import hashlib, json
def sign(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
print(json.dumps({"sig": sign(b'hello')}))
""")
    print(result.stdout)
    # {"sig": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}
```

### JavaScript via QuickJS-WASI

```python
cell = Cell(api_url="local", template="javascript")
result = cell.run("console.log([1,2,3].reduce((a,b) => a+b))")
print(result.stdout)  # '6'
cell.kill()
```

### E2B drop-in replacement

Already using E2B? Change one import:

```python
# Before:
# from e2b_code_interpreter import Sandbox

# After:
from synapse.e2b_compat import Sandbox

sbx = Sandbox(api_url="local")
result = sbx.run_code("print('hello')")
print(result.stdout)
```

Every major E2B method is supported: `run_code`, `files.read/write/list`,
`commands.run`, `kill`, `connect`, `pause`/`resume`, `get_info`, and more.

## What's in the box

- **Real Python 3.12** (CPython-WASI) — decorators, generators, `async`,
  `eval`, `open`, arbitrary `import`, full stdlib
- **Real JavaScript** (QuickJS-WASI) — ES2023, `await`, closures, typed arrays
- **Sub-millisecond .syn transpile path** for simple arithmetic
- **Filesystem API** — `cell.files.write/read/list/exists/get_info/make_dir`
- **Git namespace** — `cell.git.clone/commit/push/pull/checkout/...` (21 methods)
- **PTY terminal** — `cell.pty.create(on_data=...)` (needs `pip install websocket-client`)
- **Dockerfile transpiler** — `synapse template build -f Dockerfile`
- **Framework integrations** (soft imports — install frameworks separately):
  - LangChain: `from synapse.langchain_tool import SynapseCellExecuteTool`
  - CrewAI: `from synapse.crewai_tool import SynapseCellCrewTool`
  - AutoGen: `from synapse.autogen_tool import SynapseCellExecutor`
  - LlamaIndex: `from synapse.llamaindex_tool import SynapseCellTool`
  - OpenAI Agents SDK: `from synapse.openai_agents_tool import synapse_cell_execute`
- **Persistent Volumes** — `cell.volumes.write/read/delete/list_all` (Pro license)
- **Async SDK** — `from synapse.async_cell import AsyncCell`

## Performance (measured 2026-04-15 vs live E2B)

| Metric | Cell | E2B | Delta |
|---|---|---|---|
| Simple eval (.syn transpile) | **0.44 ms** | 91 ms | **205×** faster |
| Warm Python (CPython-WASI) | **63 ms** | ~500 ms | **8×** faster |
| JavaScript execution | **1.2 ms** | — | — |
| Memory per sandbox | ~1 MB | ~133 MB | **133×** density |
| Execution receipts | ✅ SHA-256 | ❌ | — |
| Jurisdiction | 🇨🇦 / 🇪🇺 | 🇺🇸 | — |

Reproduce: `python3 cell/benchmarks/e2b_apples_to_apples.py --runs 100`

## Deployment modes

| Mode | How | When |
|---|---|---|
| **Local** (zero-config) | `Cell(api_url="local")` | Dev, CI, small workloads, bundled into your app |
| **Self-hosted gateway** | Run the Rust gateway via Docker or native binary | Production, custom hardware, fleet management |

## Dockerfile → Wasm template (the E2B replacement bridge)

```bash
synapse template build -f Dockerfile --install-packages
```

Handles 80% of AI-agent Dockerfiles:
- `FROM python:*` / `node:*` → runtime selection
- `RUN pip install` / `npm install` → package bundles
- `COPY`, `WORKDIR`, `ENV`, `USER`, `CMD`, `ENTRYPOINT` → all mapped
- `RUN apt-get install git` → warning + "Use `cell.git.*`"
- Custom base images → clear error + migration hint

Examples: see `dockerfile_examples/` in the repo.

## CLI

```bash
synapse auth --api-key cell_sk_...
synapse sandbox create --template python3 --persistent
synapse sandbox run <id> "print(42)"
synapse template build -f Dockerfile
synapse template list
```

## Framework integrations

Tool classes are bundled in the wheel; frameworks are opt-in extras:

```bash
pip install synapserun[langchain]   # pulls langchain-core + pydantic
pip install synapserun[crewai]      # pulls crewai + pydantic
pip install synapserun[all]         # everything
```

## Correctness guardrails

The SDK auto-selects between:
1. **Fast path (.syn transpile, sub-ms)** — simple arithmetic, basic control flow
2. **Full path (real CPython-WASI, ~63ms)** — anything with `import` of non-trivial
   modules, bytes literals, `sys.exit`, f-strings with format specs, string
   multiplication, etc.

You never have to pick. The fast path has strict correctness gates; anything
it can't faithfully execute falls through to real CPython automatically.

## Links

- **Homepage:** https://synapserun.dev
- **Documentation:** https://synapserun.dev/docs
- **Support:** hello@synapserun.dev

## License

AGPL-3.0 with commercial dual-license. Apache 2.0 on the core runtime. Free for self-hosted use.
Pro features require a license key.
Contact hello@synapserun.dev for commercial licensing.

---

Built by [Synapse Run](https://synapserun.dev), Canada 🇨🇦 — Wasm-native, sovereign, self-hosted.

