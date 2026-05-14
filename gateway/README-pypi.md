# synapse-cell

**PyO3-backed Wasm sandbox execution kernel for [Synapse Cell](https://synapserun.dev).**

This is the native backend for `synapserun`. You probably want to install that instead:

```bash
pip install synapserun
```

`synapse-cell` is pulled in automatically as a dependency. It provides:

- Real CPython 3.12 compiled to `wasm32-wasi` (via VMware Labs' WLR build)
- QuickJS 0.14.0 compiled to `wasm32-wasi` (via `quickjs-ng`)
- `wasmtime 43` runtime with Cranelift JIT
- Zero-IPC `Cell(api_url="local")` mode: sub-millisecond execution, no HTTP roundtrip

## Direct usage (for library authors)

```python
import synapse_rust_core
cell = synapse_rust_core.NativeCell(
    template="python3",
    cells_root="/tmp/synapse_cells/",
    template_dir="/path/to/templates/",  # needs python3.wasm + stdlib
)
stdout, stderr, exit_code, latency_ms = cell.run("print('hello')")
```

For most users, stick with the high-level `synapse.cell.Cell` class from `synapserun`.

## License

Apache 2.0 (Core) / AGPL v3 (Pro + Hub tiers) / Commercial dual-license available. See [LICENSE](https://github.com/Synapse-Run/cell/blob/main/LICENSE), [LICENSE-AGPL.md](https://github.com/Synapse-Run/cell/blob/main/LICENSE-AGPL.md), and [COMMERCIAL_LICENSE.md](https://github.com/Synapse-Run/cell/blob/main/COMMERCIAL_LICENSE.md).
