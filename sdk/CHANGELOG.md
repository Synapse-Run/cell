# Changelog

## 0.5.2 (2026-04-20)

### Fixed
- Gateway: 0 compilation errors, 0 warnings (38 warnings resolved)
- Path traversal hardening on `/files/watch`, `/files/concat` endpoints
- Removed duplicate route handlers (snapshot, template library)
- Fixed `send_stdin` method signature, `snapshot_cell` method name, `TemplateInfo` initialization

## 0.5.0 (2026-04-20) — 🏆 100% E2B Parity

### Added — Phase D: Hard Infrastructure (16 features)
- **Lifecycle events**: `get_lifecycle_events()`, `get_global_events()` — JSONL event bus with per-cell and global streams
- **Webhooks**: `register_webhook(url, events)`, `list_webhooks()`, `delete_webhook(id)` — background delivery with event filtering
- **Auto-pause**: Reaper upgraded — auto-pause at 80% timeout, kill at 100%, lifecycle event emission
- **Signed URLs**: `get_upload_url(path)`, `get_download_url(path)` — time-limited token-based file transfer
- **Stream stdin**: `POST /processes/{id}/stdin/stream` — chunked stdin delivery to running processes
- **Process env**: `PATCH /processes/{id}/env` — runtime environment variable mutation
- **Chart capture**: `exec_capture(code)` — matplotlib interception with base64 PNG extraction
- **Template library**: Official catalog with 5 templates (claude-code, data-science, web-dev, devops, ml-training)
- **SSH access**: `GET /cells/{id}/ssh` — connection info for SSH-enabled cells
- **Custom domains**: `POST /cells/{id}/domains` — CNAME mapping with auto-SSL
- **BYOC deployment**: `GET /deploy/info` — 4 deployment methods for 5 cloud providers
- **gRPC proto**: `GET /grpc/proto` — full CellService protobuf definition (9 RPCs)

## 0.4.0 (2026-04-20) — Phases A–C: Reconciliation + Feature Sprint

### Added — Phase A: Reconciliation (34 features matched to existing code)
- Cell lifecycle: `connect()`, `list()`, `get_info()`, `patch_metadata()`, `refresh()`
- Pause/Resume/Snapshots: `pause()`, `resume()`, `create_snapshot()`, `list_snapshots()`, `delete_snapshot()`
- Code contexts: `create_code_context(name)` → `CodeContextHandle` with Jupyter-style persistent namespaces
- Process management: `start_process()`, `get_process()`, `list_processes()`, `kill_process()`, `send_stdin()`
- PTY support: `PtyHandle` with create/resize/send_stdin/kill over WebSocket
- Git integration: `GitNamespace` with clone/status/diff/commit/push/pull
- Volumes: create/list/delete/connect/read/write
- Templates: register/list/get/build/delete with custom YAML schema

### Added — Phase B: Quick Wins (11 features)
- `Cell.get_logs()` — reads `__exec_log__.jsonl`
- `Cell.get_metrics()` — executions, uptime, idle time
- Batch execution: `POST /batch/exec` — parallel multi-cell code execution
- Fetch proxy array: `POST /cells/{id}/fetch` with multiple URLs
- MCP server: `POST /mcp/start` — stdio-mode Model Context Protocol server
- File search: `POST /cells/{id}/files/search` — recursive filename + content grep
- File watch: `POST /cells/{id}/files/watch` — filesystem change monitoring
- File concat: `POST /cells/{id}/files/concat` — multi-source file concatenation
- Network config: `PATCH /cells/{id}/network` — runtime network policy updates

### Added — Phase C: Medium Closures (21 features)
- `CodeContextHandle` with exec/list_vars/set_var/delete
- OpenAI Agents tool: `SynapseCodeInterpreterTool` (3→5 tool variants)
- Claude Code tool: `SynapseClaudeCodeTool` (5 tools)
- Vercel AI SDK tool: `SynapseVercelTool` (4 tools)
- Generic agent tool: `SynapseGenericTool` (Amp, OpenCode, OpenClaw)
- Team management: `list_teams()`, `get_team_metrics()`, `get_team_metrics_max()`
- Volume metadata: `patch_volume_metadata()`, `get_volume_metadata()`
- Template: build logs, registry auth, Docker transpiler

## 0.3.0 (2026-04-15)

### Added
- **Sandbox lifecycle** (milestone 1.11): `Sandbox.connect(id)`, `sandbox.get_info() -> SandboxInfo`, `Sandbox.list(query, limit, next_token) -> SandboxPaginator`, full `Sandbox.create(metadata, envs, network, volume_mounts, lifecycle, allow_internet_access)` parameter set
- **Filesystem completeness** (milestone 1.12): `files.exists`, `files.get_info -> EntryInfo`, `files.remove`, `files.make_dir`, `files.rename`, upgraded `files.list -> List[EntryInfo]`
- **Streaming callbacks** (milestone 1.13): Real-time `on_stdout`/`on_stderr`/`on_result`/`on_error` callbacks on `run_code` and `commands.run` via SSE streaming
- **Background commands** (milestone 1.13): `commands.run(background=True)` returns `CommandHandle` with `is_running`/`stdout`/`stderr`/`exit_code`/`wait()`/`kill()`
- **E2B compatibility**: `from synapse.e2b_compat import Sandbox` is a drop-in replacement for `from e2b_code_interpreter import Sandbox`

### Fixed
- `e2b_compat.Sandbox` now forwards `metadata` and `envs` to the gateway (previously silently dropped)
- `e2b_compat.Sandbox.run_code(on_stdout=fn)` uses real SSE streaming instead of post-hoc callback firing

## 0.2.0 (2026-04-15)

- Initial Cell API: `Cell.run()`, `Cell.command()`, `Cell.fetch()`, `Cell.write_file()`, `Cell.read_file()`
- E2B compatibility adapter: `Sandbox` class with `run_code()`, `files.write()`, `files.read()`
- Cryptographic execution receipts (SHA-256 chain)
- PyO3 local mode for sub-2ms in-process execution

## 0.1.0 (2026-04-14)

- Initial SDK scaffold
