"""
Synapse .cell SDK — Persistent sandboxed execution for AI agents.

Drop-in E2B replacement with 67×+ faster cold starts, cryptographic
receipts, and $0.001/exec pricing.

Usage:
    from synapse.cell import Cell

    # Persistent session (state survives between exec calls)
    cell = Cell(api_key="cell_sk_live_...")
    cell.run("x = 42")
    cell.run("print(x * 2)")  # stdout: "84"

    # Fetch external data
    resp = cell.fetch("https://api.example.com/data")
    cell.run("import json; data = json.load(open('/data/api_data.json'))")

    # Clean up
    cell.kill()
"""
import json
import urllib.request
import urllib.error
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Union

import sys
if sys.version_info >= (3, 11):
    from typing import TypedDict, NotRequired
else:
    try:
        from typing_extensions import TypedDict, NotRequired
    except ImportError:
        from typing import TypedDict
        # NotRequired not available — define a subscriptable identity for older Python
        class _NotRequired:
            """Passthrough for NotRequired on Python <3.11 without typing_extensions."""
            def __class_getitem__(cls, item):
                return item
        NotRequired = _NotRequired  # type: ignore[assignment,misc]


@dataclass
class CellReceipt:
    """Cryptographic execution receipt for verifiable computation.

    `receipt_hash` is the SHA-256 chain hash binding all other fields:
    SHA-256(execution_id || code_hash || result_hash || template || timestamp).
    Auditors recompute this from the other fields to verify integrity. Empty
    string if the gateway returned a receipt without a chain hash (older
    binaries before JC-014, 2026-04-28).
    """
    execution_id: str
    code_hash: str
    result_hash: str
    template: str
    timestamp: int
    receipt_hash: str = ""


@dataclass
class CellResult:
    """Result of a .cell code execution."""
    stdout: str
    stderr: str
    exit_code: int
    latency_ms: float
    receipt: Optional[CellReceipt] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        status = "✓" if self.ok else "✗"
        out = self.stdout[:80] + ("..." if len(self.stdout) > 80 else "")
        return f"CellResult({status} {self.latency_ms:.0f}ms | {out!r})"


@dataclass
class FetchResult:
    """Result of a host-side HTTP fetch."""
    status: int
    body: str
    content_type: str
    body_size: int
    latency_ms: float
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        """Parse body as JSON."""
        return json.loads(self.body)

    def __repr__(self) -> str:
        return f"FetchResult({self.status} {self.body_size}B {self.latency_ms:.0f}ms)"


class SandboxState(str, Enum):
    """E2B-compatible sandbox state."""
    RUNNING = "running"
    PAUSED = "paused"
    KILLED = "killed"   # Cell-specific; E2B's list omits killed by default


class SandboxNetworkOpts(TypedDict, total=False):
    """Sandbox network configuration (E2B-compatible)."""
    allow_out: NotRequired[List[str]]
    deny_out: NotRequired[List[str]]
    allow_public_traffic: NotRequired[bool]
    mask_request_host: NotRequired[bool]


class SandboxLifecycle(TypedDict, total=False):
    """Sandbox lifecycle config: on_timeout can be 'pause' or 'kill'."""
    on_timeout: NotRequired[str]    # "pause" | "kill"
    auto_resume: NotRequired[bool]


@dataclass
class SandboxInfo:
    """E2B-compatible typed sandbox info. Returned by Cell.get_info() /
    Sandbox.get_info() / Sandbox.list() paginator items."""
    sandbox_id: str
    template_id: str
    metadata: Dict[str, str]
    started_at: datetime
    end_at: datetime
    state: SandboxState
    cpu_count: int = 1              # Placeholder until milestone 2.14 metrics
    memory_mb: int = 512            # Placeholder
    envd_version: str = "0.2.0"     # Matches Cell's current gateway version string
    name: Optional[str] = None
    sandbox_domain: Optional[str] = None
    allow_internet_access: Optional[bool] = None
    network: Optional[Dict[str, Any]] = None
    lifecycle: Optional[Dict[str, Any]] = None
    volume_mounts: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_gateway_json(cls, data: Dict[str, Any]) -> "SandboxInfo":
        """Build a SandboxInfo from the gateway's GET /v1/cells/{id} response.

        Accepts BOTH the original CellInfo shape (cell_id, template, created_at,
        timeout_ms, status) and the E2B-shaped output of to_sandbox_info_json()
        (sandbox_id, template_id, started_at, end_at, state). Prefers the E2B
        shape if present.
        """
        # E2B shape first
        sandbox_id = data.get("sandbox_id") or data.get("cell_id", "")
        template_id = data.get("template_id") or data.get("template", "python3")
        state_raw = data.get("state") or data.get("status", "running")
        try:
            state = SandboxState(state_raw) if isinstance(state_raw, str) else SandboxState.RUNNING
        except ValueError:
            state = SandboxState.RUNNING

        # started_at: prefer E2B ISO string, else derive from created_at (ms)
        if "started_at" in data and isinstance(data["started_at"], str):
            started_at = datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))
        else:
            ms = int(data.get("created_at", 0))
            started_at = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

        # end_at: prefer E2B ISO string, else derive from created_at + timeout_ms
        if "end_at" in data and isinstance(data["end_at"], str):
            end_at = datetime.fromisoformat(data["end_at"].replace("Z", "+00:00"))
        else:
            ms = int(data.get("created_at", 0))
            timeout_ms = int(data.get("timeout_ms", 3_600_000))
            end_at = datetime.fromtimestamp((ms + timeout_ms) / 1000.0, tz=timezone.utc)

        return cls(
            sandbox_id=sandbox_id,
            template_id=template_id,
            metadata=data.get("metadata", {}) or {},
            started_at=started_at,
            end_at=end_at,
            state=state,
            cpu_count=int(data.get("cpu_count", 1)),
            memory_mb=int(data.get("memory_mb", 512)),
            envd_version=data.get("envd_version", "0.2.0"),
            name=data.get("name"),
            sandbox_domain=data.get("sandbox_domain"),
            allow_internet_access=data.get("allow_internet_access"),
            network=data.get("network"),
            lifecycle=data.get("lifecycle"),
            volume_mounts=data.get("volume_mounts", []) or [],
        )


@dataclass
class SandboxQuery:
    """Filter for Cell.list() / Sandbox.list()."""
    metadata: Optional[Dict[str, str]] = None
    state: Optional[List[SandboxState]] = None


@dataclass
class EntryInfo:
    """Filesystem entry info (E2B-compatible)."""
    name: str
    type: Optional[str] = None      # "file" | "dir"
    path: str = ""
    size: int = 0
    mode: int = 0
    permissions: str = ""
    owner: str = "sandbox"
    group: str = "sandbox"
    modified_time: Optional[datetime] = None
    symlink_target: Optional[str] = None

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "EntryInfo":
        mt = data.get("modified_time")
        if isinstance(mt, str):
            try:
                mt = datetime.fromisoformat(mt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                mt = None
        return cls(
            name=data.get("name", ""),
            type=data.get("type"),
            path=data.get("path", ""),
            size=data.get("size", 0),
            mode=data.get("mode", 0),
            permissions=data.get("permissions", ""),
            owner=data.get("owner", "sandbox"),
            group=data.get("group", "sandbox"),
            modified_time=mt,
            symlink_target=data.get("symlink_target"),
        )


class VolumesAdapter:
    """Interface for interacting with persistent volumes mounted to this cell or remotely on the gateway."""

    def __init__(self, cell: "Cell"):
        self._cell = cell

    def read(self, volume_id: str, path: str) -> str:
        """Read a file from a volume without creating a sandbox."""
        import urllib.parse
        import base64
        safe_path = urllib.parse.quote(path, safe="")
        resp = self._cell._request("GET", f"/v1/volumes/{volume_id}/files?path={safe_path}")
        b64_data = resp.get("data", "")
        return base64.b64decode(b64_data).decode("utf-8")

    def write(self, volume_id: str, path: str, content: str) -> None:
        """Write a file to a volume without creating a sandbox."""
        import base64
        b64_data = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        self._cell._request("POST", f"/v1/volumes/{volume_id}/files", body={"path": path, "data": b64_data})

    def delete(self, volume_id: str) -> dict:
        """Delete a persistent volume."""
        return self._cell._request("DELETE", f"/v1/volumes/{volume_id}")

    def list_all(self) -> list:
        """List all volumes on the gateway."""
        return self._cell._request("GET", "/v1/volumes")


class CellError(Exception):
    """Raised when .cell execution or API call fails."""
    pass


class Cell:
    """Persistent sandboxed execution environment for AI agents.

    Each Cell is an isolated Python sandbox with:
      - Persistent state (variables, classes, imports survive between runs)
      - Outbound HTTP via host proxy (fetch URLs without WASI networking)
      - Filesystem (read/write files in /data/)
      - Cryptographic receipts (SHA-256 chain for verifiable computation)
      - 67x+ faster cold starts than E2B

    Args:
        api_url: Base URL of the .cell API
        api_key: API key for authenticated access
        template: Language runtime ("python3" or "javascript")
        persistent: Enable persistent state between exec calls
        volume_id: Optional ID of a persistent volume to mount at /data/
        timeout_ms: Session timeout in milliseconds (default: 1 hour)
        request_timeout: HTTP request timeout in seconds (default: 30)
        metadata: Optional dict of string key/value metadata stored on the cell
        envs: Optional dict of environment variables for the cell
        allow_internet_access: Whether the cell can make outbound network calls
        network: Network config (E2B SandboxNetworkOpts shape)
        volume_mounts: List of volume mount dicts [{path, name}]
        lifecycle: Lifecycle config (E2B SandboxLifecycle shape)
        secure: Whether to enable enhanced security (default True)

    Note:
        When api_url="local" (PyO3 mode), the new kwargs (metadata, envs,
        allow_internet_access, network, volume_mounts, lifecycle, secure) are
        accepted but not enforced. The PyO3 NativeCell has no server-side
        storage for these fields.

    Example:
        >>> cell = Cell(api_key="cell_sk_live_...")
        >>> cell.run("import math")
        >>> result = cell.run("print(math.pi)")
        >>> print(result.stdout)    # "3.141592653589793"
        >>> print(result.receipt)   # CellReceipt(...)
        >>> cell.kill()
    """

    @classmethod
    def create_volume(cls, volume_id: Optional[str] = None, api_key: Optional[str] = None, api_url: str = "http://localhost:8002") -> dict:
        """Create a new persistent volume for use by cells."""
        import os
        import urllib.request
        import json
        api_key = api_key or os.environ.get("SYNAPSE_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{api_url.rstrip('/')}/v1/volumes"
        body = json.dumps({"volume_id": volume_id}).encode("utf-8") if volume_id else b"{}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def __init__(
        self,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
        template: str = "python3",
        persistent: bool = True,
        volume_id: Optional[str] = None,
        timeout_ms: int = 3_600_000,
        request_timeout: int = 60,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        allow_internet_access: Optional[bool] = None,
        network: Optional[Dict[str, Any]] = None,
        volume_mounts: Optional[List[Dict[str, str]]] = None,
        lifecycle: Optional[Dict[str, Any]] = None,
        secure: bool = True,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key or os.environ.get("SYNAPSE_API_KEY")
        self.template = template
        self.persistent = persistent
        self.volume_id = volume_id
        self.timeout_ms = timeout_ms
        self.request_timeout = request_timeout
        self._metadata = metadata
        self._envs = envs
        self._allow_internet_access = allow_internet_access
        self._network = network
        self._volume_mounts = volume_mounts
        self._lifecycle = lifecycle
        self._secure = secure

        # ── PyO3 local-mode statefulness honesty (JC-014, 2026-04-28) ────
        # Persistent sessions in PyO3 local mode are gated to Cell Pro per the
        # cell.rs:1197 license check + the EdgeCell-vs-Pro pricing matrix in
        # cell/CLAUDE.md. The current PyO3 NativeCell ctor (lib.rs:51) calls
        # the free-tier `create_cell` (ephemeral) regardless of this flag.
        # Until lib.rs takes a `persistent` param + tier-aware cert handling,
        # `persistent=True` is silently downgraded to stateless EdgeCell mode
        # in `api_url="local"` without a license. We always downgrade
        # self.persistent so it reflects Rust reality; suppression only
        # silences the user-facing warning (CELL_SUPPRESS_PERSISTENT_WARNING=1).
        if (
            self.api_url == "local"
            and persistent
            and not os.environ.get("SYNAPSE_LICENSE_CERT")
        ):
            self.persistent = False
            if not os.environ.get("CELL_SUPPRESS_PERSISTENT_WARNING"):
                import sys as _sys
                print(
                    "[Cell SDK] persistent=True requires a Cell Pro license for "
                    "PyO3 local mode. No SYNAPSE_LICENSE_CERT detected — "
                    "downgrading to stateless EdgeCell. Variables, imports, and "
                    "classes will NOT survive between cell.run() calls. For "
                    "stateful sessions today, use HTTP gateway mode "
                    '(api_url="http://your-gateway:8002") with a Pro license. '
                    "Suppress this warning with CELL_SUPPRESS_PERSISTENT_WARNING=1.",
                    file=_sys.stderr,
                )

        if self.api_url == "local":
            try:
                import synapse_rust_core
                # Spawn Zero-IPC Native Wasm Container directly inside the Python process
                # Sprint C: resolve template_dir — env var > synapse-root/templates > /tmp fallback
                template_dir = os.environ.get("CELL_TEMPLATE_DIR")
                if not template_dir:
                    # 1. Check bundled templates (packaged inside the wheel)
                    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_templates")
                    if os.path.isdir(bundled) and os.path.exists(os.path.join(bundled, "python3.wasm")):
                        template_dir = bundled
                if not template_dir:
                    # 2. Walk up from this file to find synapse/templates/ (dev mode)
                    here = os.path.dirname(os.path.abspath(__file__))
                    for _ in range(5):  # cell/sdk/synapse → cell/sdk → cell → synapse
                        candidate = os.path.join(here, "templates")
                        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "python3.wasm")):
                            template_dir = candidate
                            break
                        here = os.path.dirname(here)
                if not template_dir:
                    template_dir = "/tmp/synapse_templates/"
                cells_root = os.environ.get("CELL_DATA_DIR", "/tmp/synapse_cells/")
                self._native_cell = synapse_rust_core.NativeCell(
                    self.template,
                    cells_root,
                    template_dir
                )
                self.cell_id = getattr(self._native_cell, "cell_id", "local-dev-001")
                self.executions = 0
            except ImportError:
                raise CellError("PyO3 backend not built. Run `maturin develop` inside `cell/gateway`.")
            # Local mode: still attach namespaces so cell.pty / cell.git work
            self.pty = PtyNamespace(self)
            from synapse.git_client import GitNamespace
            self.git = GitNamespace(self)
            self.volumes = VolumesAdapter(self)
            return

        # Parse URL for persistent HTTP connection
        from urllib.parse import urlparse
        parsed = urlparse(self.api_url)
        self._host = parsed.hostname or "localhost"
        self._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._scheme = parsed.scheme

        # Persistent HTTP connection (keep-alive)
        self._conn = None
        self._conn_attempts = 0

        # Create the cell on the remote server
        self._native_cell = None
        self._info = self._create_cell()
        self.cell_id = self._info["cell_id"]
        self.executions = 0
        self.pty = PtyNamespace(self)
        from synapse.git_client import GitNamespace
        self.git = GitNamespace(self)
        self.volumes = VolumesAdapter(self)

    def _get_conn(self):
        """Get or create a persistent HTTP connection."""
        import http.client
        import ssl

        if self._conn is not None:
            return self._conn

        if self._scheme == "https":
            ctx = ssl.create_default_context()
            self._conn = http.client.HTTPSConnection(
                self._host, self._port,
                timeout=self.request_timeout,
                context=ctx,
            )
        else:
            self._conn = http.client.HTTPConnection(
                self._host, self._port,
                timeout=self.request_timeout,
            )
        return self._conn

    def _reset_conn(self):
        """Reset the persistent connection on error."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Make an authenticated HTTP request using a persistent connection."""
        data = json.dumps(body).encode("utf-8") if body else None

        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if data:
            headers["Content-Length"] = str(len(data))

        # Try with persistent connection, fall back to new connection on failure
        for attempt in range(2):
            try:
                conn = self._get_conn()
                conn.request(method, path, body=data, headers=headers)
                resp = conn.getresponse()
                resp_body = resp.read().decode("utf-8")

                if resp.status >= 400:
                    try:
                        err = json.loads(resp_body)
                        raise CellError(err.get("error", f"HTTP {resp.status}"))
                    except (json.JSONDecodeError, AttributeError):
                        raise CellError(f"HTTP {resp.status}: {resp.reason}")

                return json.loads(resp_body)

            except (ConnectionError, OSError, BrokenPipeError, ConnectionResetError) as e:
                self._reset_conn()
                if attempt == 0:
                    continue  # Retry with fresh connection
                raise CellError(f"Connection failed: {e}") from e
            except CellError:
                raise
            except Exception as e:
                self._reset_conn()
                if attempt == 0:
                    continue
                raise CellError(f"Request failed: {e}") from e

    def _create_cell(self) -> dict:
        """Create the cell on the server."""
        req_body = {
            "template": self.template,
            "persistent": self.persistent,
            "timeout_ms": self.timeout_ms,
        }
        if self.volume_id:
            req_body["volume_id"] = self.volume_id
        if self._metadata is not None:
            req_body["metadata"] = self._metadata
        if self._envs is not None:
            req_body["envs"] = self._envs
        if self._allow_internet_access is not None:
            req_body["allow_internet_access"] = self._allow_internet_access
        if self._network is not None:
            req_body["network"] = self._network
        if self._volume_mounts is not None:
            req_body["volume_mounts"] = self._volume_mounts
        if self._lifecycle is not None:
            req_body["lifecycle"] = self._lifecycle
        if self._secure is not None:
            req_body["secure"] = self._secure

        return self._request("POST", "/v1/cells", req_body)

    # ─── Static Factory: Attach to Existing Cell ─────────────────

    @classmethod
    def connect(
        cls,
        cell_id: str,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
        timeout_ms: int = 3_600_000,
        request_timeout: int = 60,
    ) -> "Cell":
        """Attach to an existing running Cell by ID.

        Matches E2B's Sandbox.connect(sandbox_id) shape. No new cell is created;
        the gateway is asked for the existing cell's info and a fresh Cell
        Python object is bound to it.

        PyO3 local mode (api_url="local") is not supported because in-process
        state is not shared across processes.

        Raises CellError if the cell does not exist or has been killed.
        """
        if api_url == "local":
            raise CellError("Cell.connect() is not supported in local (PyO3) mode")

        inst = cls.__new__(cls)   # bypass __init__ so we don't POST /v1/cells
        inst.api_url = api_url.rstrip("/")
        inst.api_key = api_key or os.environ.get("SYNAPSE_API_KEY")
        inst.template = "python3"   # overwritten below from gateway response
        inst.persistent = True
        inst.volume_id = None
        inst.timeout_ms = timeout_ms
        inst.request_timeout = request_timeout

        # Parse URL for persistent HTTP connection (mirrors __init__)
        from urllib.parse import urlparse
        parsed = urlparse(inst.api_url)
        inst._host = parsed.hostname or "localhost"
        inst._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        inst._scheme = parsed.scheme
        inst._conn = None
        inst._conn_attempts = 0
        inst._native_cell = None
        inst.executions = 0

        # Verify cell exists + hydrate from gateway response
        try:
            info = inst._request("GET", f"/v1/cells/{cell_id}")
        except CellError as e:
            if "404" in str(e) or "not found" in str(e).lower():
                raise CellError(f"Cell not found: {cell_id}") from e
            raise

        # Handle both legacy CellInfo and E2B SandboxInfo shapes
        state = info.get("status") or info.get("state")
        if state == "killed":
            raise CellError(f"Cell {cell_id} has been killed")

        # Sprint A Batch 5: Auto-resume paused cells (E2B connect() behavior)
        if state == "paused":
            try:
                inst._request("POST", f"/v1/cells/{cell_id}/resume")
            except CellError:
                pass  # Best-effort; continue with connection even if resume fails

        inst.cell_id = info.get("cell_id") or info.get("sandbox_id") or cell_id
        inst.template = info.get("template") or info.get("template_id", "python3")
        inst.persistent = info.get("persistent", True)
        inst.volume_id = info.get("volume_id")
        inst.executions = info.get("executions", 0)
        inst._info = info
        inst.pty = PtyNamespace(inst)
        from synapse.git_client import GitNamespace
        inst.git = GitNamespace(inst)
        return inst

    # ─── Core API ────────────────────────────────────────────────

    def run(
        self,
        code: str,
        language: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        on_result: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> CellResult:
        """Execute code in the cell.

        For persistent cells, state (variables, imports, classes) survives
        between run() calls.

        When any callback (on_stdout, on_stderr, on_result, on_error) is
        provided and the cell is in HTTP mode, execution uses the SSE
        streaming endpoint (``run_stream()``) and fires callbacks in
        real-time as output arrives from the gateway. Without callbacks,
        behavior is unchanged (blocking POST).

        In PyO3 local mode, callbacks are fired post-hoc after execution
        completes (no SSE available in-process).

        Args:
            code: Python source code to execute
            language: Override language (default: cell's template)
            on_stdout: Callback fired for each stdout line (real-time in HTTP mode)
            on_stderr: Callback fired for each stderr line (real-time in HTTP mode)
            on_result: Callback fired with the result event dict when execution completes
            on_error: Callback fired with error message string on execution error

        Returns:
            CellResult with stdout, stderr, latency, and receipt

        Example:
            >>> cell.run("x = [1, 2, 3]")
            >>> result = cell.run("print(sum(x))")
            >>> print(result.stdout)  # "6"

            >>> # With streaming callbacks
            >>> cell.run("for i in range(5): print(i)",
            ...          on_stdout=lambda line: print(f">> {line}"))
        """
        has_callbacks = any([on_stdout, on_stderr, on_result, on_error])

        # ─── PyO3 local mode: callbacks fire post-hoc ───────────
        if getattr(self, "_native_cell", None) is not None:
            # JC-014 (2026-04-28): receipt fields now plumbed through PyO3 so
            # free-tier local-mode cells produce real receipts (was None before
            # — contradicted the Show HN "every execution produces a SHA-256
            # hash chain" claim). Tuple unpacks 10 elements: 4 result fields +
            # 6 receipt fields.
            stdout, stderr, exit_code, latency_ms, exec_id, code_hash, result_hash, template, timestamp, receipt_hash = \
                self._native_cell.run(code)
            local_receipt = CellReceipt(
                execution_id=exec_id,
                code_hash=code_hash,
                result_hash=result_hash,
                template=template,
                timestamp=timestamp,
                receipt_hash=receipt_hash,
            )
            self.executions += 1
            if has_callbacks:
                if on_stdout and stdout:
                    for line in stdout.splitlines():
                        on_stdout(line)
                if on_stderr and stderr:
                    for line in stderr.splitlines():
                        on_stderr(line)
                result = CellResult(
                    stdout=stdout, stderr=stderr,
                    exit_code=exit_code, latency_ms=latency_ms,
                    receipt=local_receipt,
                )
                if on_result:
                    on_result({"type": "result", "exit_code": exit_code, "latency_ms": latency_ms})
                if on_error and exit_code != 0:
                    on_error(stderr)
                return result
            return CellResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                latency_ms=latency_ms,
                receipt=local_receipt,
            )

        # ─── HTTP mode with callbacks: real-time SSE streaming ──
        if has_callbacks:
            stdout_parts: List[str] = []
            stderr_parts: List[str] = []
            exit_code = 0
            latency_ms = 0.0
            receipt = None
            for event in self.run_stream(code):
                etype = event.get("type")
                if etype == "stdout":
                    text = event.get("text", "")
                    stdout_parts.append(text)
                    if on_stdout:
                        on_stdout(text)
                elif etype == "stderr":
                    text = event.get("text", "")
                    stderr_parts.append(text)
                    if on_stderr:
                        on_stderr(text)
                elif etype == "result":
                    exit_code = event.get("exit_code", 0)
                    latency_ms = event.get("latency_ms", 0.0)
                    if on_result:
                        on_result(event)
                elif etype == "error":
                    if on_error:
                        on_error(event.get("message", str(event)))
            self.executions += 1
            return CellResult(
                stdout="\n".join(stdout_parts),
                stderr="\n".join(stderr_parts),
                exit_code=exit_code,
                latency_ms=latency_ms,
                receipt=receipt,
            )

        # ─── HTTP mode without callbacks: blocking POST ─────────
        payload = {"code": code}
        if language:
            payload["language"] = language

        body = self._request("POST", f"/v1/cells/{self.cell_id}/exec", payload)
        self.executions += 1

        receipt = None
        if "receipt" in body:
            r = body["receipt"]
            receipt = CellReceipt(
                execution_id=r["execution_id"],
                code_hash=r["code_hash"],
                result_hash=r["result_hash"],
                template=r["template"],
                timestamp=r["timestamp"],
                receipt_hash=r.get("receipt_hash", ""),
            )

        return CellResult(
            stdout=body.get("stdout", ""),
            stderr=body.get("stderr", ""),
            exit_code=body.get("exit_code", 0),
            latency_ms=body.get("latency_ms", 0.0),
            receipt=receipt,
        )

    def command(
        self,
        cmd: str,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        background: bool = False,
    ) -> Union[CellResult, "CommandHandle"]:
        """Execute a shell command in the cell.

        Supports common commands: ls, cat, echo, pwd, mkdir, rm, cp, mv,
        touch, head, tail, wc, env, find, python3 -c.

        When ``on_stdout`` or ``on_stderr`` callbacks are provided in HTTP
        mode, the command is wrapped as Python subprocess code and streamed
        via the SSE endpoint so callbacks fire in real-time.

        When ``background=True``, the command runs asynchronously and a
        :class:`CommandHandle` is returned for polling status and output.

        Args:
            cmd: Shell command string (e.g., "ls -la /data")
            on_stdout: Callback fired for each stdout line (real-time in HTTP mode)
            on_stderr: Callback fired for each stderr line (real-time in HTTP mode)
            background: If True, run asynchronously and return a CommandHandle

        Returns:
            CellResult with command output, or CommandHandle if background=True

        Example:
            >>> cell.command("ls /data")
            >>> cell.command("echo hello world")
            >>> cell.command("cat /data/config.json")

            >>> # Streaming callbacks
            >>> cell.command("ls -la /data", on_stdout=print)

            >>> # Background execution
            >>> handle = cell.command("sleep 5 && echo done", background=True)
            >>> handle.wait()
            >>> print(handle.stdout)
        """
        # ─── Background execution ───────────────────────────────
        if background:
            body = self._request("POST", f"/v1/cells/{self.cell_id}/cmd", {
                "command": cmd,
                "background": True,
            })
            return CommandHandle(body.get("command_id", ""), self)

        # ─── Streaming via SSE (HTTP mode only) ─────────────────
        if any([on_stdout, on_stderr]) and self._native_cell is None:
            wrapper_code = (
                "import subprocess, sys\n"
                f"result = subprocess.run({cmd!r}, shell=True, capture_output=True, text=True)\n"
                "if result.stdout:\n"
                "    print(result.stdout, end='')\n"
                "if result.stderr:\n"
                "    print(result.stderr, end='', file=sys.stderr)\n"
                "sys.exit(result.returncode)\n"
            )
            return self.run(wrapper_code, on_stdout=on_stdout, on_stderr=on_stderr)

        # ─── Blocking POST (default) ───────────────────────────
        body = self._request("POST", f"/v1/cells/{self.cell_id}/cmd", {
            "command": cmd,
        })
        self.executions += 1

        receipt = None
        if "receipt" in body:
            r = body["receipt"]
            receipt = CellReceipt(
                execution_id=r["execution_id"],
                code_hash=r["code_hash"],
                result_hash=r["result_hash"],
                template=r["template"],
                timestamp=r["timestamp"],
                receipt_hash=r.get("receipt_hash", ""),
            )

        return CellResult(
            stdout=body.get("stdout", ""),
            stderr=body.get("stderr", ""),
            exit_code=body.get("exit_code", 0),
            latency_ms=body.get("latency_ms", 0.0),
            receipt=receipt,
        )

    def run_stream(self, code: str):
        """Execute code and stream output line-by-line via SSE.

        Returns an iterator of dicts with type 'stdout', 'stderr', or 'result'.

        Example:
            >>> for event in cell.run_stream("for i in range(5): print(i)"):
            ...     if event['type'] == 'stdout':
            ...         print(event['text'])
        """
        url = f"{self.api_url}/v1/cells/{self.cell_id}/exec/stream"
        data = json.dumps({"code": code}).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=self.request_timeout)
            for line in resp:
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        yield event
                        if event.get("type") == "result":
                            self.executions += 1
                            return
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as e:
            raise CellError(f"Stream failed: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise CellError(f"Stream connection failed: {e.reason}") from e

    def fetch(
        self,
        url: str,
        method: str = "GET",
        body: Optional[str] = None,
        save_to: Optional[str] = None,
        timeout_secs: int = 30,
    ) -> FetchResult:
        """Fetch a URL via the host's network stack.

        The response is automatically saved to the cell's filesystem
        at /data/__fetch_response__.json and can be read in subsequent
        run() calls.

        Args:
            url: URL to fetch
            method: HTTP method (GET, POST, PUT, DELETE)
            body: Request body (for POST/PUT)
            save_to: Save response to this file path in /data/
            timeout_secs: Request timeout in seconds

        Returns:
            FetchResult with status, body, latency

        Example:
            >>> resp = cell.fetch("https://api.example.com/data", save_to="api.json")
            >>> cell.run("import json; data = json.load(open('/data/api.json'))")
        """
        payload: Dict[str, Any] = {
            "url": url,
            "method": method.upper(),
            "timeout_secs": timeout_secs,
        }
        if body:
            payload["body"] = body
        if save_to:
            payload["save_to"] = save_to

        result = self._request("POST", f"/v1/cells/{self.cell_id}/fetch", payload)

        return FetchResult(
            status=result.get("status", 0),
            body=result.get("body", ""),
            content_type=result.get("content_type", ""),
            body_size=result.get("body_size", 0),
            latency_ms=result.get("latency_ms", 0.0),
            error=result.get("error"),
        )

    def _local_data_path(self, path: str = "") -> str:
        """Return the host filesystem path for a file inside the 
        cell's /data/ dir. Only valid in local (PyO3) mode.
        
        Enforces two defenses against sandbox escape:
          1. Lexical: any `..` path segment is rejected up front.
          2. Realpath: post-resolution, target must live under 
             the cell's data root (defeats symlink escapes).
        """
        import os as _os
        cells_root = _os.environ.get(
            "CELL_DATA_DIR", "/tmp/synapse_cells/"
        )
        clean = path.lstrip("/")
        # Lexical defense: segment-aware `..` rejection. This is 
        # intentionally segment-aware (not substring) so filenames 
        # like "my..file.txt" stay legal — only `..` as a path 
        # component is dangerous.
        segments = clean.replace("\\", "/").split("/")
        if ".." in segments:
            raise ValueError(
                f"Path traversal not allowed: {path!r}"
            )
        base = _os.path.join(cells_root, self.cell_id, "data")
        target = _os.path.join(base, clean) if clean else base
        # Realpath defense-in-depth: catches symlink escapes.
        base_real = _os.path.realpath(base)
        target_real = _os.path.realpath(target)
        if (target_real != base_real 
                and not target_real.startswith(base_real + _os.sep)):
            raise ValueError(
                f"Path escapes sandbox root: {path!r}"
            )
        return target

    def write_file(self, path: str, content: str) -> None:
        """Write a file to the cell's /data/ directory.

        Args:
            path: File path relative to /data/
            content: File content (text)
        """
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            target = self._local_data_path(path)
            _os.makedirs(_os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w") as f:
                f.write(content)
            return
        self._request("POST", f"/v1/cells/{self.cell_id}/files", {
            "path": path,
            "content": content,
        })

    def write_files(self, files: Dict[str, str]) -> dict:
        """Write multiple files in a single request.

        More efficient than calling write_file() in a loop — sends one
        HTTP request instead of N.

        Args:
            files: Dict mapping path → content.
                   Example: {"main.py": "print(1)", "data.json": "{}"}

        Returns:
            Dict with 'written' count, 'total' count, and 'errors' list.

        Example:
            >>> cell.write_files({
            ...     "src/main.py": "print('hello')",
            ...     "src/utils.py": "def add(a,b): return a+b",
            ...     "config.json": '{"debug": true}',
            ... })
            {'written': 3, 'total': 3, 'errors': []}
        """
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            written = 0
            errors: List[str] = []
            for path, content in files.items():
                try:
                    target = self._local_data_path(path)
                    _os.makedirs(_os.path.dirname(target) or ".", exist_ok=True)
                    with open(target, "w") as f:
                        f.write(content)
                    written += 1
                except Exception as e:
                    errors.append(f"{path}: {e}")
            return {"written": written, "total": len(files), "errors": errors}

        file_list = [{"path": p, "content": c} for p, c in files.items()]
        return self._request("POST", f"/v1/cells/{self.cell_id}/files/batch", {
            "files": file_list,
        })

    def read_file(self, path: str) -> str:
        """Read a file from the cell's /data/ directory.

        Args:
            path: File path relative to /data/

        Returns:
            File content as string
        """
        if getattr(self, "_native_cell", None) is not None:
            target = self._local_data_path(path)
            with open(target, "r") as f:
                return f.read()
        from urllib.parse import quote
        result = self._request("GET", f"/v1/cells/{self.cell_id}/files?path={quote(path)}")
        return result.get("content", "")

    def list_files(self, path: str = "") -> List["EntryInfo"]:
        """List files in the cell's /data/ directory.

        Returns rich EntryInfo objects (E2B-compatible).

        Args:
            path: Directory path relative to /data/ (default: root)

        Returns:
            List of EntryInfo objects with name, type, path, size, etc.
        """
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            import stat as _stat_mod
            clean = path.lstrip("/")
            base = _os.path.join("/tmp/synapse_cells/", self.cell_id, "data")
            dir_path = _os.path.join(base, clean) if clean else base
            entries: List[EntryInfo] = []
            for name in _os.listdir(dir_path):
                full = _os.path.join(dir_path, name)
                try:
                    st = _os.stat(full)
                    entries.append(EntryInfo(
                        name=name,
                        type="dir" if _stat_mod.S_ISDIR(st.st_mode) else "file",
                        path=_os.path.relpath(full, base),
                        size=st.st_size,
                        mode=st.st_mode,
                        permissions=_stat_mod.filemode(st.st_mode)[1:],
                        modified_time=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
                    ))
                except OSError:
                    entries.append(EntryInfo(name=name))
            return entries

        from urllib.parse import quote
        result = self._request("GET", f"/v1/cells/{self.cell_id}/files/list?path={quote(path)}")
        # Handle both old (list of strings) and new (list of objects) response shapes
        if isinstance(result, dict):
            files_raw = result.get("files", [])
        elif isinstance(result, list):
            files_raw = result
        else:
            files_raw = []

        if files_raw and isinstance(files_raw[0], str):
            # Old gateway response -- wrap as minimal EntryInfo
            return [EntryInfo(name=f, path=f) for f in files_raw]
        return [EntryInfo.from_json(f) if isinstance(f, dict) else EntryInfo(name=str(f)) for f in files_raw]

    def file_exists(self, path: str) -> bool:
        """Check if a file or directory exists in the cell's /data/ directory."""
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            clean = path.lstrip("/")
            return _os.path.exists(_os.path.join("/tmp/synapse_cells/", self.cell_id, "data", clean))
        from urllib.parse import quote
        result = self._request("GET", f"/v1/cells/{self.cell_id}/files/exists?path={quote(path)}")
        return result.get("exists", False)

    def file_info(self, path: str) -> "EntryInfo":
        """Get metadata about a file or directory."""
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            import stat as _stat_mod
            clean = path.lstrip("/")
            full = _os.path.join("/tmp/synapse_cells/", self.cell_id, "data", clean)
            st = _os.stat(full)
            return EntryInfo(
                name=_os.path.basename(full),
                type="dir" if _stat_mod.S_ISDIR(st.st_mode) else "file",
                path=clean,
                size=st.st_size,
                mode=st.st_mode,
                permissions=_stat_mod.filemode(st.st_mode)[1:],
                modified_time=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            )
        from urllib.parse import quote
        result = self._request("GET", f"/v1/cells/{self.cell_id}/files/info?path={quote(path)}")
        return EntryInfo.from_json(result)

    def remove_file(self, path: str) -> None:
        """Remove a file or directory from the cell."""
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            import shutil as _shutil
            clean = path.lstrip("/")
            full = _os.path.join("/tmp/synapse_cells/", self.cell_id, "data", clean)
            if _os.path.isdir(full):
                _shutil.rmtree(full)
            else:
                _os.remove(full)
            return
        from urllib.parse import quote
        self._request("DELETE", f"/v1/cells/{self.cell_id}/files?path={quote(path)}")

    def make_dir(self, path: str) -> None:
        """Create a directory (and parents) in the cell."""
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            clean = path.lstrip("/")
            _os.makedirs(_os.path.join("/tmp/synapse_cells/", self.cell_id, "data", clean), exist_ok=True)
            return
        self._request("POST", f"/v1/cells/{self.cell_id}/files/mkdir", {"path": path})

    def rename_file(self, old_path: str, new_path: str) -> "EntryInfo":
        """Rename/move a file or directory within the cell."""
        if getattr(self, "_native_cell", None) is not None:
            import os as _os
            clean_old = old_path.lstrip("/")
            clean_new = new_path.lstrip("/")
            base = _os.path.join("/tmp/synapse_cells/", self.cell_id, "data")
            _os.renames(_os.path.join(base, clean_old), _os.path.join(base, clean_new))
            return self.file_info(new_path)
        result = self._request("POST", f"/v1/cells/{self.cell_id}/files/rename", {
            "old_path": old_path,
            "new_path": new_path,
        })
        return EntryInfo.from_json(result)

    # ─── Sprint A Batch 1: Lifecycle + metadata + envs ─────────

    def set_timeout(self, timeout_secs: int) -> None:
        """Update the cell's inactivity timeout (1-86400 seconds)."""
        if getattr(self, "_native_cell", None) is not None:
            return  # Local mode has no reaper
        self._request("PUT", f"/v1/cells/{self.cell_id}/timeout",
                      {"timeout": timeout_secs})

    def refresh(self) -> None:
        """Reset the inactivity timer, extending the cell's lifetime."""
        if getattr(self, "_native_cell", None) is not None:
            return  # Local mode has no reaper
        self._request("POST", f"/v1/cells/{self.cell_id}/refresh")

    def is_running(self) -> bool:
        """Check whether the cell is currently running (lightweight heartbeat)."""
        if getattr(self, "_native_cell", None) is not None:
            return self._native_cell is not None
        try:
            result = self._request("GET", f"/v1/cells/{self.cell_id}/is_running")
            return result.get("running", False)
        except CellError:
            return False

    def patch_metadata(self, patch: dict) -> dict:
        """Merge key/value pairs into the cell's metadata. Returns updated map."""
        if getattr(self, "_native_cell", None) is not None:
            return {}  # Local mode doesn't track metadata server-side
        result = self._request("PATCH", f"/v1/cells/{self.cell_id}/metadata", patch)
        return result.get("metadata", {})

    def get_envs(self) -> dict:
        """Return the cell's current environment variable map."""
        if getattr(self, "_native_cell", None) is not None:
            return {}
        return self._request("GET", f"/v1/cells/{self.cell_id}/envs")

    def patch_envs(self, patch: dict) -> dict:
        """Merge key/value pairs into the cell's environment variables.

        Note: newly added envs take effect for new code runs on persistent
        cells; the running Wasm harness won't see them mid-session.
        Returns the updated env map.
        """
        if getattr(self, "_native_cell", None) is not None:
            return {}
        return self._request("PATCH", f"/v1/cells/{self.cell_id}/envs", patch)

    def get_metrics(self) -> dict:
        """Get per-cell usage metrics.

        Returns a dict with execution count, uptime, idle time, etc.

        Example:
            >>> m = cell.get_metrics()
            >>> print(f"Executions: {m['executions']}, Uptime: {m['uptime_ms']}ms")
        """
        if getattr(self, "_native_cell", None) is not None:
            return {"executions": self.executions, "uptime_ms": 0}
        return self._request("GET", f"/v1/cells/{self.cell_id}/metrics")

    # ─── Sprint A Batch 2: Process management ─────────────────

    def start_process(self, command: str) -> "ProcessHandle":
        """Start a real background subprocess and return a ProcessHandle.

        Spawns a native OS process (not Wasm-translated) via the gateway.
        Returns immediately with a handle for polling/stdin/kill.
        """
        body = self._request("POST", f"/v1/cells/{self.cell_id}/cmd", {
            "command": command,
            "background": True,
        })
        return ProcessHandle(body.get("command_id", ""), self)

    def list_processes(self) -> list:
        """List all background processes (running + completed) for this cell."""
        result = self._request("GET", f"/v1/cells/{self.cell_id}/processes")
        if isinstance(result, list):
            return [ProcessHandle(p.get("command_id", ""), self) for p in result]
        return []

    def kill_process(self, command_id: str) -> None:
        """Kill a background process by command_id."""
        self._request(
            "POST",
            f"/v1/cells/{self.cell_id}/processes/{command_id}/kill")

    def send_process_stdin(self, command_id: str, data: str) -> None:
        """Send data to a background process's stdin."""
        self._request(
            "POST",
            f"/v1/cells/{self.cell_id}/processes/{command_id}/stdin",
            {"data": data})

    # ─── Sprint A Batch 5: Pause / Resume / Snapshots ──────────

    def pause(self) -> str:
        """Pause the cell and take a filesystem snapshot.

        Updates cell status to 'paused'. Returns the snapshot_id
        which can be used to restore state later.

        Example::

            snap_id = cell.pause()
            print(f"Paused with snapshot {snap_id}")
            # ... later ...
            cell.resume()

        Returns:
            Snapshot ID string.
        """
        if getattr(self, "_native_cell", None) is not None:
            return ""  # Local mode doesn't support pause
        result = self._request("POST", f"/v1/cells/{self.cell_id}/pause")
        return result.get("snapshot_id", "")

    def resume(self) -> None:
        """Resume a paused cell.

        Updates the cell status back to 'running' and resets the
        inactivity timer. Cell state is restored from the most
        recent snapshot.

        Example::

            cell.resume()
            result = cell.run("print('back online!')")
        """
        if getattr(self, "_native_cell", None) is not None:
            return
        self._request("POST", f"/v1/cells/{self.cell_id}/resume")

    def create_snapshot(self) -> str:
        """Create a named snapshot of the cell's current filesystem state.

        Unlike ``pause()`` which also changes cell status, this captures
        a snapshot while the cell keeps running.

        Example::

            snap_id = cell.create_snapshot()
            print(f"Snapshot: {snap_id}")

        Returns:
            Snapshot ID string.
        """
        if getattr(self, "_native_cell", None) is not None:
            return ""
        result = self._request("POST", f"/v1/cells/{self.cell_id}/snapshot")
        return result.get("snapshot_id", "")

    def list_snapshots(self) -> list:
        """List all snapshots for this cell.

        Example::

            snaps = cell.list_snapshots()
            for s in snaps:
                print(s)

        Returns:
            List of snapshot metadata dicts.
        """
        if getattr(self, "_native_cell", None) is not None:
            return []
        result = self._request("GET", f"/v1/cells/{self.cell_id}/snapshots")
        return result if isinstance(result, list) else []

    # ─── Phase A2: Code Contexts (Jupyter-style namespaces) ──────

    def create_code_context(self, name: str = "default") -> "CodeContextHandle":
        """Create a named code context with its own variable namespace.

        Code contexts provide Jupyter-style isolated namespaces within a
        persistent cell. Variables defined in one context are invisible to
        other contexts, enabling multi-tenant or multi-notebook workflows.

        Requires a persistent cell.

        Args:
            name: Human-readable name for the context.

        Returns:
            A CodeContextHandle that can run code in this namespace.

        Example:
            >>> ctx = cell.create_code_context("analysis")
            >>> ctx.run("x = 42")
            >>> ctx.run("print(x)")  # prints 42
            >>> ctx2 = cell.create_code_context("sandbox")
            >>> ctx2.run("print(x)")  # NameError — x not in this context
        """
        if not self.persistent:
            raise CellError("Code contexts require a persistent cell")
        result = self._request("POST", f"/v1/cells/{self.cell_id}/contexts", {
            "name": name,
        })
        return CodeContextHandle(
            context_id=result["context_id"],
            name=result.get("name", name),
            cell=self,
        )

    def list_code_contexts(self) -> list:
        """List all code contexts in this cell.

        Returns:
            List of dicts with 'context_id' and 'name' keys.
        """
        result = self._request("GET", f"/v1/cells/{self.cell_id}/contexts")
        if isinstance(result, list):
            return result
        return []

    def delete_code_context(self, context_id: str) -> None:
        """Delete a code context and free its namespace.

        Args:
            context_id: The context ID returned by create_code_context().
        """
        self._request("DELETE", f"/v1/cells/{self.cell_id}/contexts/{context_id}")

    # ─── Phase B: Additional gap closures ────────────────────────

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot by ID.

        Args:
            snapshot_id: The snapshot ID returned by create_snapshot().
        """
        self._request("DELETE", f"/v1/cells/{self.cell_id}/snapshots/{snapshot_id}")

    def get_logs(self) -> list:
        """Get execution logs for this cell.

        Returns a list of log entry dicts from the cell's exec history.
        Each entry contains the code, output, and timestamp of a previous
        execution.

        Returns:
            List of log entry dicts.
        """
        result = self._request("GET", f"/v1/cells/{self.cell_id}/logs")
        if isinstance(result, list):
            return result
        return []

    def close_process_stdin(self, command_id: str) -> None:
        """Close the stdin pipe of a running process, sending EOF.

        After calling this, the process will receive EOF on its stdin and
        can no longer receive input via send_process_stdin().

        Args:
            command_id: The process/command ID.
        """
        self._request("POST", f"/v1/cells/{self.cell_id}/processes/{command_id}/close-stdin")

    # ─── Phase C: Quick + Medium gap closures ────────────────────

    def send_signal(self, command_id: str, signal: int = 15) -> dict:
        """Send a signal to a running process.

        Args:
            command_id: The process/command ID.
            signal: Signal number (9=SIGKILL, 15=SIGTERM). Default: SIGTERM.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/processes/{command_id}/signal",
            {"signal": signal})

    def get_host(self) -> dict:
        """Get the sandbox host info for port forwarding.

        Returns:
            Dict with host, port, and cell_id.
        """
        return self._request("GET", f"/v1/cells/{self.cell_id}/host")

    def update_network(self, config: dict) -> dict:
        """Update the sandbox network configuration.

        Args:
            config: Network config dict (e.g., {"deny_out": ["10.0.0.0/8"]}).
        """
        return self._request("PUT", f"/v1/cells/{self.cell_id}/network", config)

    def get_mcp_token(self) -> dict:
        """Get an MCP access token for this cell.

        Returns:
            Dict with token and cell_id.
        """
        return self._request("GET", f"/v1/cells/{self.cell_id}/mcp/token")

    def get_mcp_url(self) -> dict:
        """Get the MCP server URL for this cell.

        Returns:
            Dict with url and cell_id.
        """
        return self._request("GET", f"/v1/cells/{self.cell_id}/mcp/url")

    @classmethod
    def mcp_catalog(cls, api_url: str = "http://localhost:8002") -> list:
        """Get the available MCP server catalog.

        Returns:
            List of available MCP server dicts.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/mcp/catalog"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def start_mcp_server(self, name: str, command: str) -> dict:
        """Start a custom MCP server inside this cell.

        Args:
            name: Name for the MCP server.
            command: Shell command to start the server.

        Returns:
            Dict with server_name, command_id, cell_id, status.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/mcp/servers",
            {"name": name, "command": command})

    def configure_github(self, github_token: str) -> dict:
        """Configure GitHub integration (git credentials) inside this cell.

        Args:
            github_token: GitHub Personal Access Token or App token.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/mcp/github",
            {"github_token": github_token})

    @classmethod
    def get_build_status(cls, template_name: str,
                         api_url: str = "http://localhost:8002") -> dict:
        """Get the build status of a template.

        Args:
            template_name: Template name.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/templates/{template_name}/build"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def rebuild_template(cls, template_name: str,
                         api_url: str = "http://localhost:8002") -> dict:
        """Trigger a rebuild of a template.

        Args:
            template_name: Template name to rebuild.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/templates/{template_name}/rebuild"
        req = urllib.request.Request(url, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def set_template_registry_auth(cls, token: str,
                                    registry_url: str = "https://registry.synapse.run",
                                    api_url: str = "http://localhost:8002") -> dict:
        """Configure private template registry authentication.

        Args:
            token: Registry access token.
            registry_url: Registry URL (default: Synapse registry).
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/templates/registry/auth"
        data = json.dumps({"token": token, "registry_url": registry_url}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def patch_volume_metadata(self, volume_id: str, metadata: dict) -> dict:
        """Update metadata/tags on a volume.

        Args:
            volume_id: Volume ID.
            metadata: Key-value pairs to merge into volume metadata.
        """
        return self._request("PATCH", f"/v1/volumes/{volume_id}/metadata", metadata)

    def get_volume_metadata(self, volume_id: str) -> dict:
        """Get metadata/tags for a volume."""
        return self._request("GET", f"/v1/volumes/{volume_id}/metadata")

    def watch_directory(self, path: str = "") -> dict:
        """Start watching a directory for changes.

        Args:
            path: Directory path to watch (relative to /data/).

        Returns:
            Dict with watch_id and path.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/files/watch",
            {"path": path})

    def get_watch_events(self, watch_id: str) -> list:
        """Poll for file change events on a watched directory.

        Args:
            watch_id: Watch ID returned by watch_directory().

        Returns:
            List of file entry dicts (name, type, size).
        """
        result = self._request("GET",
            f"/v1/cells/{self.cell_id}/files/watch/{watch_id}")
        return result if isinstance(result, list) else []

    def concat_files(self, sources: list, destination: str) -> dict:
        """Concatenate multiple files into one.

        Args:
            sources: List of source file paths (relative to /data/).
            destination: Destination file path.

        Returns:
            Dict with status, destination, and size.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/files/concat",
            {"sources": sources, "destination": destination})

    def get_access_token(self) -> dict:
        """Generate a secured access token for this cell.

        Returns:
            Dict with access_token, cell_id, and expires_in.
        """
        return self._request("POST", f"/v1/cells/{self.cell_id}/access-token")

    def set_proxy(self, proxy_url: str) -> dict:
        """Set proxy configuration for outbound requests.

        Args:
            proxy_url: Proxy URL (e.g., "http://proxy.company.com:3128").
        """
        return self._request("PUT",
            f"/v1/cells/{self.cell_id}/proxy",
            {"proxy_url": proxy_url})

    def connect_storage(self, bucket: str, provider: str = "s3") -> dict:
        """Connect an external storage bucket (S3/GCS) to this cell.

        Args:
            bucket: Bucket name.
            provider: Storage provider ("s3" or "gcs").
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/storage",
            {"bucket": bucket, "provider": provider})

    @classmethod
    def list_teams(cls, api_url: str = "http://localhost:8002") -> list:
        """List teams."""
        import urllib.request
        import json
        url = f"{api_url}/v1/teams"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def get_team_metrics(cls, team_id: str = "default",
                         api_url: str = "http://localhost:8002") -> dict:
        """Get team-level metrics."""
        import urllib.request
        import json
        url = f"{api_url}/v1/teams/{team_id}/metrics"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    # ─── Phase D: Hard feature implementations ──────────────────

    def get_lifecycle_events(self) -> list:
        """Get lifecycle events for this cell (create, pause, resume, kill, etc.).

        Returns:
            List of event dicts with event type, detail, and timestamp.
        """
        result = self._request("GET", f"/v1/cells/{self.cell_id}/events")
        return result if isinstance(result, list) else []

    @classmethod
    def get_global_events(cls, api_url: str = "http://localhost:8002") -> list:
        """Get global lifecycle events across all cells (last 100).

        Returns:
            List of event dicts.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/events"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def register_webhook(cls, url: str, events: list = None,
                         api_url: str = "http://localhost:8002") -> dict:
        """Register a webhook to receive lifecycle event notifications.

        Args:
            url: HTTP endpoint to receive POST callbacks.
            events: List of event types to subscribe to (default: ["*"] for all).

        Returns:
            Dict with webhook_id, url, events, created_at.
        """
        import urllib.request
        import json as json_mod
        payload = {"url": url, "events": events or ["*"]}
        data = json_mod.dumps(payload).encode()
        req = urllib.request.Request(
            f"{api_url}/v1/webhooks", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            return json_mod.loads(resp.read())

    @classmethod
    def list_webhooks(cls, api_url: str = "http://localhost:8002") -> list:
        """List all registered webhooks."""
        import urllib.request
        import json
        req = urllib.request.Request(f"{api_url}/v1/webhooks")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def delete_webhook(cls, webhook_id: str,
                       api_url: str = "http://localhost:8002") -> dict:
        """Delete a webhook by ID."""
        import urllib.request
        import json
        req = urllib.request.Request(
            f"{api_url}/v1/webhooks/{webhook_id}", method="DELETE")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def get_upload_url(self, path: str) -> dict:
        """Generate a signed upload URL for a file.

        Args:
            path: Destination file path in the sandbox.

        Returns:
            Dict with upload_url, token, expires_in, path.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/files/upload-url",
            {"path": path})

    def get_download_url(self, path: str) -> dict:
        """Generate a signed download URL for a file.

        Args:
            path: Source file path in the sandbox.

        Returns:
            Dict with download_url, token, expires_in, path.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/files/download-url",
            {"path": path})

    def stream_stdin(self, command_id: str, chunks: list) -> dict:
        """Stream multiple stdin chunks to a running process.

        Args:
            command_id: Process/command ID.
            chunks: List of string chunks to send sequentially.

        Returns:
            Dict with status and chunks_sent count.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/processes/{command_id}/stdin/stream",
            {"chunks": chunks})

    def update_process_env(self, command_id: str, env: dict) -> dict:
        """Update environment variables for a running process.

        Args:
            command_id: Process/command ID.
            env: Dict of environment variable key-value pairs to set.

        Returns:
            Dict with status and updated env.
        """
        return self._request("PATCH",
            f"/v1/cells/{self.cell_id}/processes/{command_id}/env",
            env)

    def exec_capture(self, code: str) -> dict:
        """Execute code and capture chart/image output.

        Automatically detects matplotlib figures and returns them as
        base64-encoded PNG images alongside the standard output.

        Args:
            code: Python code to execute (may produce matplotlib plots).

        Returns:
            Dict with stdout, stderr, exit_code, images (list of base64), latency_ms.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/exec/capture",
            {"code": code})

    @classmethod
    def get_template_library(cls, api_url: str = "http://localhost:8002") -> list:
        """Get the official template library catalog.

        Returns:
            List of template dicts with name, description, runtime, packages, category.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/templates/library"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def install_template(cls, name: str,
                         api_url: str = "http://localhost:8002") -> dict:
        """Install a template from the official library.

        Args:
            name: Template name from the library catalog.
        """
        import urllib.request
        import json
        data = json.dumps({"name": name}).encode()
        req = urllib.request.Request(
            f"{api_url}/v1/templates/library/install",
            data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def get_ssh_info(self) -> dict:
        """Get SSH connection info for this cell.

        Returns:
            Dict with ssh_host, ssh_port, ssh_user, connection_string.
        """
        return self._request("GET", f"/v1/cells/{self.cell_id}/ssh")

    def set_custom_domain(self, domain: str) -> dict:
        """Configure a custom domain for this cell's hosted services.

        Args:
            domain: Custom domain name (e.g., "app.example.com").

        Returns:
            Dict with status, domain, cname_target, ssl config.
        """
        return self._request("POST",
            f"/v1/cells/{self.cell_id}/domains",
            {"domain": domain})

    @classmethod
    def get_deploy_info(cls, api_url: str = "http://localhost:8002") -> dict:
        """Get BYOC (Bring Your Own Cloud) deployment information.

        Returns:
            Dict with supported providers, requirements, deployment methods.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/deploy/info"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    @classmethod
    def get_grpc_proto(cls, api_url: str = "http://localhost:8002") -> dict:
        """Get the gRPC protocol buffer definition for the Cell service.

        Returns:
            Dict with proto (string) and version.
        """
        import urllib.request
        import json
        url = f"{api_url}/v1/grpc/proto"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    # ─── Kill / Info / List ──────────────────────────────────────

    def kill(self) -> None:
        """Kill (destroy) the cell and clean up resources."""
        if getattr(self, "_native_cell", None) is not None:
            self._native_cell = None
            return

        try:
            self._request("DELETE", f"/v1/cells/{self.cell_id}")
        except CellError:
            pass  # Already dead
        finally:
            self._reset_conn()

    def get_info(self) -> SandboxInfo:
        """Get typed E2B-compatible SandboxInfo for this Cell.

        HTTP mode: GET /v1/cells/{cell_id} and parse.
        PyO3 local mode: read from NativeCell.get_info() when available, else
            fall back to building a SandboxInfo from the in-memory template + id.

        Raises CellError if the cell has been killed or the server returns 404.
        """
        if getattr(self, "_native_cell", None) is not None:
            try:
                raw = self._native_cell.get_info()   # pyO3-added in Track C, may not exist yet
                return SandboxInfo.from_gateway_json(raw)
            except AttributeError:
                # PyO3 NativeCell.get_info() lands in Track C Task 9.
                # For now build a minimal SandboxInfo from in-process state.
                now = datetime.now(timezone.utc)
                return SandboxInfo(
                    sandbox_id=self.cell_id,
                    template_id=self.template,
                    metadata={},
                    started_at=now,
                    end_at=now,   # no accurate end time available without the backing struct
                    state=SandboxState.RUNNING,
                )

        data = self._request("GET", f"/v1/cells/{self.cell_id}")
        if data.get("status") == "killed" or data.get("state") == "killed":
            raise CellError(f"Cell {self.cell_id} has been killed")
        return SandboxInfo.from_gateway_json(data)

    # ─── Sprint C Phase C1: Template management ────────────────

    @classmethod
    def list_templates(
        cls,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
    ) -> list:
        """List all registered templates on the gateway.

        Returns a list of template info dicts.
        """
        import http.client
        import json as _json
        from urllib.parse import urlparse

        key = api_key or os.environ.get("SYNAPSE_API_KEY", "")
        parsed = urlparse(api_url.rstrip("/"))
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if parsed.scheme == "https":
            import ssl
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=30)

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        conn.request("GET", "/v1/templates", headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        return _json.loads(body) if body else []

    @classmethod
    def get_template(
        cls,
        name: str,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
    ) -> Optional[dict]:
        """Get info for a specific template by name.

        Returns template info dict or None if not found.
        """
        import http.client
        import json as _json
        from urllib.parse import urlparse

        key = api_key or os.environ.get("SYNAPSE_API_KEY", "")
        parsed = urlparse(api_url.rstrip("/"))
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if parsed.scheme == "https":
            import ssl
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=30)

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        conn.request("GET", f"/v1/templates/{name}", headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        if resp.status == 404:
            return None
        return _json.loads(body) if body else None

    @classmethod
    def build_template(
        cls,
        dockerfile_path: str,
        name: Optional[str] = None,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Transpile a Dockerfile to a Cell template and register it.

        Sprint C Phase C4 — the E2B replacement bridge. Accepts an existing
        Dockerfile, converts it to a Cell TemplateInfo via the Wasm-native
        directive mapping, and POSTs it to ``/v1/templates``.

        Args:
            dockerfile_path: Path to the Dockerfile.
            name: Override the template name (default: derived from parent dir).
            api_url: Gateway URL.
            api_key: API key (default: env SYNAPSE_API_KEY).
            dry_run: If True, don't upload; return the spec dict.

        Returns:
            Registered template info dict (or the spec if dry_run).

        Raises:
            TranspileError: on unsupported Dockerfile directives.
            CellError: on gateway upload failure.
        """
        from synapse.dockerfile_transpiler import transpile_dockerfile_file

        spec, warnings = transpile_dockerfile_file(dockerfile_path)
        if name:
            spec["name"] = name
        if not spec.get("name"):
            spec["name"] = "cell-template"

        if dry_run:
            return spec

        import http.client
        import json as _json
        from urllib.parse import urlparse

        key = api_key or os.environ.get("SYNAPSE_API_KEY", "")
        parsed = urlparse(api_url.rstrip("/"))
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if parsed.scheme == "https":
            import ssl
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=30)

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        conn.request("POST", "/v1/templates", _json.dumps(spec), headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()

        if resp.status != 200:
            raise CellError(f"Template upload failed ({resp.status}): {body}")
        return _json.loads(body) if body else spec

    @classmethod
    def list(
        cls,
        query: Optional["SandboxQuery"] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
    ) -> "SandboxPaginator":
        """List sandboxes with pagination + optional filters.

        Matches E2B's Sandbox.list(query, limit, next_token) shape.
        Not supported in PyO3 local mode.

        Returns a paginator. Call ``.next_items()`` until ``.has_next`` is False.
        """
        if api_url == "local":
            raise CellError("Cell.list() is not supported in local (PyO3) mode")
        return SandboxPaginator(
            query=query,
            limit=limit,
            next_token=next_token,
            api_url=api_url,
            api_key=api_key,
        )

    @property
    def info(self) -> dict:
        """Get current cell info from the server.

        Deprecated: use ``get_info()`` for a typed SandboxInfo. This property
        remains for back-compat and returns the raw dict from the gateway.
        """
        return self._request("GET", f"/v1/cells/{self.cell_id}")

    # ─── Context Manager ─────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.kill()

    def __repr__(self) -> str:
        status = "persistent" if self.persistent else "ephemeral"
        return f"Cell({self.cell_id[:8]}... {status} {self.template} execs={self.executions})"


# ─── Command Handle ─────────────────────────────────────────────


class CommandHandle:
    """Handle for a background command. Returned by Cell.command(background=True).

    Properties poll the gateway on access and cache terminal states
    (``completed`` or ``failed``). Use :meth:`wait` to block until the
    command finishes.

    Example:
        >>> handle = cell.command("sleep 2 && echo done", background=True)
        >>> handle.wait()
        >>> print(handle.stdout)   # "done\\n"
        >>> print(handle.exit_code)  # 0
    """

    def __init__(self, command_id: str, cell: "Cell"):
        self.command_id = command_id
        self._cell = cell
        self._data: Optional[dict] = None

    def _fetch(self) -> dict:
        """Poll the gateway for command status."""
        if self._data and self._data.get("status") in ("completed", "failed"):
            return self._data  # cached -- terminal state
        self._data = self._cell._request(
            "GET", f"/v1/cells/{self._cell.cell_id}/commands/{self.command_id}")
        return self._data

    @property
    def is_running(self) -> bool:
        """True while the command is still executing."""
        return self._fetch().get("status") == "running"

    @property
    def stdout(self) -> str:
        """Standard output captured so far (or final, if completed)."""
        return self._fetch().get("stdout", "")

    @property
    def stderr(self) -> str:
        """Standard error captured so far (or final, if completed)."""
        return self._fetch().get("stderr", "")

    @property
    def exit_code(self) -> Optional[int]:
        """Process exit code. None while still running."""
        return self._fetch().get("exit_code")

    def wait(self, timeout_ms: int = 30000) -> "CommandHandle":
        """Block until the command completes (polls every 100 ms).

        Args:
            timeout_ms: Maximum time to wait in milliseconds (default 30 s).

        Returns:
            self, for chaining (e.g. ``handle.wait().stdout``).
        """
        import time
        deadline = time.time() + timeout_ms / 1000.0
        while self.is_running and time.time() < deadline:
            time.sleep(0.1)
        return self

    def kill(self) -> None:
        """Kill the background command."""
        try:
            self._cell._request(
                "DELETE",
                f"/v1/cells/{self._cell.cell_id}/commands/{self.command_id}")
        except CellError:
            pass

    def __repr__(self) -> str:
        status = self._data.get("status", "unknown") if self._data else "pending"
        return f"CommandHandle({self.command_id[:8]}... {status})"


# ─── Process Handle (Sprint A Batch 2) ──────────────────────────


class ProcessHandle:
    """Handle for a real OS subprocess. Returned by Cell.start_process().

    Polls the gateway's ``/processes/{cmd_id}`` endpoint on property access.
    Terminal states (``completed`` or ``failed``) are cached.

    Example:
        >>> handle = cell.start_process("sleep 2 && echo done")
        >>> handle.wait(timeout=10)
        >>> print(handle.stdout)   # "done"
        >>> print(handle.exit_code)  # 0
    """

    def __init__(self, command_id: str, cell: "Cell"):
        self.command_id = command_id
        self._cell = cell
        self._data: Optional[dict] = None

    def _fetch(self, force: bool = False) -> dict:
        """Poll the gateway for process status."""
        if not force and self._data and self._data.get("status") in ("completed", "failed"):
            return self._data
        self._data = self._cell._request(
            "GET",
            f"/v1/cells/{self._cell.cell_id}/processes/{self.command_id}")
        return self._data

    @property
    def pid(self) -> Optional[int]:
        """OS process ID."""
        return self._fetch().get("pid")

    @property
    def is_running(self) -> bool:
        """True while the process is alive."""
        return self._fetch(force=True).get("status") == "running"

    @property
    def stdout(self) -> str:
        """Standard output captured so far."""
        return self._fetch().get("stdout", "")

    @property
    def stderr(self) -> str:
        """Standard error captured so far."""
        return self._fetch().get("stderr", "")

    @property
    def exit_code(self) -> Optional[int]:
        """Process exit code. None while still running."""
        return self._fetch().get("exit_code")

    def send_stdin(self, data: str) -> None:
        """Send data to the process's stdin."""
        self._cell._request(
            "POST",
            f"/v1/cells/{self._cell.cell_id}/processes/{self.command_id}/stdin",
            {"data": data})

    def kill(self) -> None:
        """Kill the subprocess (SIGKILL)."""
        try:
            self._cell._request(
                "POST",
                f"/v1/cells/{self._cell.cell_id}/processes/{self.command_id}/kill")
        except CellError:
            pass

    def wait(self, timeout: float = 30.0) -> "ProcessHandle":
        """Block until process completes or timeout (seconds) elapses.

        Returns self for chaining.
        Raises CellError on timeout.
        """
        import time as _time
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            if not self.is_running:
                return self
            _time.sleep(0.1)
        raise CellError(
            f"Process {self.command_id} did not complete within {timeout}s")

    def __repr__(self) -> str:
        status = self._data.get("status", "unknown") if self._data else "pending"
        return f"ProcessHandle({self.command_id[:8]}... {status})"

# ─── Code Context Handle (Phase A2) ─────────────────────────────


class CodeContextHandle:
    """Handle for a named code context within a persistent Cell.

    Each context maintains an isolated Python namespace. Variables set
    in one context are invisible to others. Created via
    ``cell.create_code_context(name)``.

    Example:
        >>> ctx = cell.create_code_context("analysis")
        >>> result = ctx.run("x = 42; print(x)")
        >>> print(result.stdout)  # "42"
        >>> ctx.run("print(x)")   # still 42 — same namespace
        >>> ctx.delete()
    """

    def __init__(self, context_id: str, name: str, cell: "Cell"):
        self.context_id = context_id
        self.name = name
        self._cell = cell

    def run(self, code: str) -> "CellResult":
        """Execute code in this context's namespace.

        Args:
            code: Python source code to execute.

        Returns:
            CellResult with stdout, stderr, latency, and receipt.
        """
        result = self._cell._request(
            "POST",
            f"/v1/cells/{self._cell.cell_id}/contexts/{self.context_id}/exec",
            {"code": code},
        )

        receipt = None
        if "receipt" in result:
            r = result["receipt"]
            receipt = CellReceipt(
                execution_id=r.get("execution_id", ""),
                code_hash=r.get("code_hash", ""),
                result_hash=r.get("result_hash", ""),
                template=r.get("template", ""),
                timestamp=r.get("timestamp", 0),
                receipt_hash=r.get("receipt_hash", ""),
            )

        return CellResult(
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=result.get("exit_code", 0),
            latency_ms=result.get("latency_ms", 0.0),
            receipt=receipt,
        )

    def delete(self) -> None:
        """Delete this context and free its namespace."""
        self._cell.delete_code_context(self.context_id)

    def __repr__(self) -> str:
        return f"CodeContextHandle({self.context_id[:12]}... name='{self.name}')"


# ─── PTY Handle + Namespace (Sprint A Batch 3) ─────────────────


class PtyHandle:
    """Handle for an active PTY session via WebSocket.

    Binary WS frames = raw shell I/O bytes.
    JSON WS frames = control messages (resize, kill).

    Requires: ``pip install websocket-client``
    """

    def __init__(
        self,
        cell: "Cell",
        on_data: Optional[Callable] = None,
    ):
        self._cell = cell
        self._on_data = on_data
        self._ws = None
        self._running = False
        self._stop_flag = [False]
        self._reader_thread = None
        self.pid: Optional[int] = None

    def _connect(self, cols: int = 80, rows: int = 24) -> None:
        import threading
        try:
            import websocket as _ws_lib
        except ImportError:
            raise CellError(
                "PTY requires websocket-client. Install: pip install websocket-client"
            )

        ws_url = (
            self._cell.api_url
            .replace("http://", "ws://")
            .replace("https://", "wss://")
        )
        ws_url = f"{ws_url}/v1/cells/{self._cell.cell_id}/pty"

        headers = {}
        if self._cell.api_key:
            headers["Authorization"] = f"Bearer {self._cell.api_key}"

        self._ws = _ws_lib.WebSocket()
        self._ws.settimeout(5)
        self._ws.connect(ws_url, header=headers)
        self._running = True
        self._stop_flag = [False]

        # Read the initial {"type":"connected","pid":N} message
        try:
            initial = self._ws.recv()
            import json as _json
            info = _json.loads(initial)
            if info.get("type") == "connected":
                self.pid = info.get("pid")
        except Exception:
            pass

        on_data = self._on_data
        ws_ref = self._ws
        stop_flag = self._stop_flag

        def _reader():
            import websocket as _ws_lib
            while not stop_flag[0]:
                try:
                    opcode, data = ws_ref.recv_data(control_frame=False)
                    if opcode == _ws_lib.ABNF.OPCODE_BINARY:
                        if on_data:
                            on_data(bytes(data))
                    elif opcode == _ws_lib.ABNF.OPCODE_TEXT:
                        # JSON control response or text output
                        if on_data:
                            on_data(data if isinstance(data, bytes) else data.encode())
                    elif opcode == _ws_lib.ABNF.OPCODE_CLOSE:
                        stop_flag[0] = True
                        break
                except Exception:
                    stop_flag[0] = True
                    break

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def send_stdin(self, data: bytes) -> None:
        """Send raw bytes to the PTY (keyboard input)."""
        if self._ws and not self._stop_flag[0]:
            self._ws.send_binary(data)

    def resize(self, cols: int, rows: int) -> None:
        """Request a terminal resize."""
        if self._ws and not self._stop_flag[0]:
            import json as _json
            self._ws.send(_json.dumps({"type": "resize", "cols": cols, "rows": rows}))

    def kill(self) -> None:
        """Kill the shell process and close the WebSocket."""
        if self._ws:
            import json as _json
            try:
                self._ws.send(_json.dumps({"type": "kill"}))
            except Exception:
                pass
            try:
                self._ws.close()
            except Exception:
                pass
        self._running = False
        self._stop_flag[0] = True

    @property
    def is_running(self) -> bool:
        return self._running and not self._stop_flag[0]

    def __repr__(self) -> str:
        state = "running" if self.is_running else "stopped"
        pid_str = f" pid={self.pid}" if self.pid else ""
        return f"PtyHandle({state}{pid_str})"


class PtyNamespace:
    """PTY management namespace. Access via ``cell.pty``."""

    def __init__(self, cell: "Cell"):
        self._cell = cell

    def create(
        self,
        cols: int = 80,
        rows: int = 24,
        on_data: Optional[Callable] = None,
    ) -> PtyHandle:
        """Create and connect a PTY session.

        Args:
            cols: Initial terminal width (default 80).
            rows: Initial terminal height (default 24).
            on_data: Callback fired with raw bytes as shell output arrives.

        Returns:
            Connected PtyHandle.

        Requires:
            ``pip install websocket-client``
        """
        handle = PtyHandle(self._cell, on_data=on_data)
        handle._connect(cols=cols, rows=rows)
        return handle


# ─── Sandbox Paginator ───────────────────────────────────────────


class SandboxPaginator:
    """E2B-compatible paginator for Cell.list()."""

    def __init__(
        self,
        query: Optional[SandboxQuery] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
        request_timeout: int = 60,
    ):
        self.query = query
        self.limit = limit
        self._next_token = next_token
        self._has_next = True   # True until the first fetch clears it
        self._first_fetch_done = False
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key or os.environ.get("SYNAPSE_API_KEY")
        self._request_timeout = request_timeout

    @property
    def has_next(self) -> bool:
        """Whether there are more items to fetch."""
        return self._has_next

    def next_items(self) -> List[SandboxInfo]:
        """Fetch the next page of sandboxes.

        Returns an empty list when exhausted. Caller should check
        ``has_next`` before calling.
        """
        if not self._has_next:
            return []

        from urllib.parse import quote

        # Build query string
        params: List[str] = []
        if self.limit is not None:
            params.append(f"limit={self.limit}")
        if self._next_token is not None:
            params.append(f"next_token={quote(self._next_token)}")
        if self.query is not None:
            if self.query.metadata:
                pairs = ",".join(
                    f"{quote(k)}={quote(v)}" for k, v in self.query.metadata.items()
                )
                params.append(f"metadata={quote(pairs)}")
            if self.query.state:
                state_str = ",".join(
                    s.value if hasattr(s, "value") else str(s) for s in self.query.state
                )
                params.append(f"state={state_str}")

        path = "/v1/cells"
        if params:
            path += "?" + "&".join(params)

        # HTTP request (lightweight client; no Cell instance to borrow from)
        import http.client
        import ssl
        from urllib.parse import urlparse

        parsed = urlparse(self._api_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        scheme = parsed.scheme
        if scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=self._request_timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self._request_timeout)

        headers: Dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")

        # Extract X-Next-Token BEFORE parsing
        next_tok = resp.getheader("X-Next-Token") or resp.getheader("x-next-token")

        if resp.status >= 400:
            try:
                err = json.loads(body)
                raise CellError(err.get("error", f"HTTP {resp.status}"))
            except json.JSONDecodeError:
                raise CellError(f"HTTP {resp.status}: {resp.reason}")

        items_json = json.loads(body)
        items = [SandboxInfo.from_gateway_json(c) for c in items_json]

        self._next_token = next_tok
        self._has_next = bool(next_tok)
        self._first_fetch_done = True
        return items


# ─── Convenience Function ────────────────────────────────────────

def run(code: str, api_key: Optional[str] = None, **kwargs) -> CellResult:
    """One-shot code execution (ephemeral cell).

    For quick, stateless execution without managing a cell lifecycle.

    Args:
        code: Python source code to execute
        api_key: API key for authenticated access

    Returns:
        CellResult with stdout, stderr, latency, and receipt

    Example:
        >>> from synapse.cell import run
        >>> result = run("print(2 + 2)", api_key="cell_sk_live_...")
        >>> print(result.stdout)  # "4"
    """
    api_key = api_key or os.environ.get("SYNAPSE_API_KEY")
    with Cell(api_key=api_key, persistent=False, **kwargs) as cell:
        return cell.run(code)
