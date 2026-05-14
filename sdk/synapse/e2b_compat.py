"""
E2B Compatibility Adapter — Drop-in replacement for e2b_code_interpreter.Sandbox.

Switch from E2B to Synapse with a ONE-LINE import change:

    # Before (E2B)
    from e2b_code_interpreter import Sandbox

    # After (Synapse — 100× cheaper, sub-ms execution)
    from synapse.e2b_compat import Sandbox

Everything else stays the same. Your existing E2B code just works.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any, List
from synapse.cell import (
    Cell,
    CellResult,
    CellError,
    EntryInfo,
    SandboxInfo,
    SandboxState,
    SandboxQuery,
    SandboxPaginator,
)


# ─── E2B-Compatible Result Types ────────────────────────────────────

@dataclass
class ExecutionResult:
    """E2B-compatible execution result.

    Maps to e2b_code_interpreter.Execution with the same field names.
    Synapse-specific fields (exit_code, latency_ms, receipt) are exposed
    alongside the E2B-compatible surface.
    """
    text: str = ""
    stdout: str = ""
    stderr: str = ""
    error: Optional["ExecutionError"] = None
    results: List[Any] = field(default_factory=list)
    # Synapse-specific additions (safe to ignore if migrating from E2B):
    exit_code: int = 0
    latency_ms: float = 0.0
    receipt: Optional[Any] = None

    @property
    def logs(self) -> "ExecutionLogs":
        return ExecutionLogs(stdout=self.stdout, stderr=self.stderr)


@dataclass
class ExecutionError:
    name: str = ""
    value: str = ""
    traceback: str = ""


@dataclass
class ExecutionLogs:
    stdout: str = ""
    stderr: str = ""


# ─── Filesystem Adapter ─────────────────────────────────────────────

class FilesystemAdapter:
    """E2B-compatible filesystem interface.

    E2B uses sandbox.files.write() / sandbox.files.read().
    This adapter maps those to Synapse Cell's file operations.
    """

    def __init__(self, cell: Cell):
        self._cell = cell

    def write(self, path: str, data: str) -> None:
        """Write a file to the sandbox filesystem.

        E2B API: sandbox.files.write('/path/to/file', 'content')
        """
        # E2B uses /home/user/ paths; Synapse uses /data/
        clean_path = self._normalize_path(path)
        self._cell.write_file(clean_path, data)

    def read(self, path: str) -> str:
        """Read a file from the sandbox filesystem.

        E2B API: content = sandbox.files.read('/path/to/file')
        """
        clean_path = self._normalize_path(path)
        return self._cell.read_file(clean_path)

    def list(self, path: str = "") -> List["EntryInfo"]:
        """List files in a directory.

        E2B API: files = sandbox.files.list('/path')
        """
        clean_path = self._normalize_path(path)
        return self._cell.list_files(clean_path)

    def exists(self, path: str) -> bool:
        """Check if a file or directory exists.

        E2B API: sandbox.files.exists('/path')
        """
        clean_path = self._normalize_path(path)
        return self._cell.file_exists(clean_path)

    def get_info(self, path: str) -> "EntryInfo":
        """Get file/directory metadata.

        E2B API: sandbox.files.get_info('/path')
        """
        clean_path = self._normalize_path(path)
        return self._cell.file_info(clean_path)

    def remove(self, path: str) -> None:
        """Remove a file or directory.

        E2B API: sandbox.files.remove('/path')
        """
        clean_path = self._normalize_path(path)
        self._cell.remove_file(clean_path)

    def make_dir(self, path: str) -> bool:
        """Create a directory.

        E2B API: sandbox.files.make_dir('/path')
        """
        clean_path = self._normalize_path(path)
        self._cell.make_dir(clean_path)
        return True

    def rename(self, old_path: str, new_path: str) -> "EntryInfo":
        """Rename/move a file or directory.

        E2B API: sandbox.files.rename('/old', '/new')
        """
        clean_old = self._normalize_path(old_path)
        clean_new = self._normalize_path(new_path)
        return self._cell.rename_file(clean_old, clean_new)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Convert E2B-style paths to Synapse paths.

        E2B uses /home/user/ as the base; Synapse uses /data/.
        This strips common prefixes for compatibility.
        """
        for prefix in ["/home/user/", "/home/", "/tmp/"]:
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        return path.lstrip("/")


# ─── Main Sandbox Class ─────────────────────────────────────────────


class Volume:
    """E2B-compatible Volume class."""
    
    def __init__(self, volume_id: str):
        self.volume_id = volume_id

    @property
    def id(self) -> str:
        return self.volume_id
        
    @property
    def name(self) -> str:
        return self.volume_id

    @classmethod
    def create(cls, *args, **kwargs) -> "Volume":
        """Create a new persistent volume on the gateway."""
        from synapse.cell import Cell
        resp = Cell.create_volume()
        return cls(volume_id=resp.get("volume_id", ""))


class Sandbox:
    """E2B-compatible Sandbox — drop-in replacement powered by Synapse Cell.

    Usage is identical to E2B:

        from synapse.e2b_compat import Sandbox

        sandbox = Sandbox()
        execution = sandbox.run_code("x = 42; print(x)")
        print(execution.text)  # "42"

        sandbox.files.write("/home/user/data.txt", "hello")
        content = sandbox.files.read("/home/user/data.txt")

        sandbox.kill()

    Also works as a context manager:

        with Sandbox() as sandbox:
            sandbox.run_code("print('hello')")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        template: str = "python3",
        timeout: int = 300,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        allow_internet_access: bool = True,
        network: Optional[Dict[str, Any]] = None,
        volume_mounts: Optional[Dict[str, Any]] = None,
        lifecycle: Optional[Dict[str, Any]] = None,
        secure: bool = True,
        # Synapse-specific extensions (not in E2B)
        api_url: str = "https://api.synapserun.dev",
        volume_id: Optional[str] = None,
    ):
        """Create a new sandbox.

        Args:
            api_key: API key for authentication (E2B: E2B_API_KEY)
            template: Language runtime (default: "python3")
            timeout: Session timeout in seconds (E2B default: 300)
            metadata: Optional metadata dict
            envs: Optional environment variables
            allow_internet_access: Allow outbound network (default True, E2B compat)
            network: Network config dict (SandboxNetworkOpts shape)
            volume_mounts: Dict mapping mount path -> volume ID or name
                           E2B shape: {"/data/foo": "vol_abc"} or {"/data/foo": Volume(...)}
                           Transformed to gateway shape: [{"path": "/data/foo", "name": "vol_abc"}]
            lifecycle: Lifecycle config dict (e.g. {"on_timeout": "pause"})
            secure: Enable enhanced security (default True)
            api_url: Synapse gateway URL (Synapse extension)
            volume_id: Persistent volume ID (Synapse extension)
        """
        # Transform E2B volume_mounts dict to gateway list-of-objects shape
        transformed_mounts = None
        if volume_mounts is not None:
            transformed_mounts = []
            for mount_path, vol_ref in volume_mounts.items():
                # vol_ref can be a string (volume ID/name) or an object with .id/.name
                vol_name = str(vol_ref) if isinstance(vol_ref, str) else str(
                    getattr(vol_ref, 'id', getattr(vol_ref, 'name', str(vol_ref)))
                )
                transformed_mounts.append({"path": mount_path, "name": vol_name})

        self._cell = Cell(
            api_url=api_url,
            api_key=api_key,
            template=template,
            persistent=True,
            volume_id=volume_id,
            timeout_ms=timeout * 1000,
            metadata=metadata,
            envs=envs,
            allow_internet_access=allow_internet_access,
            network=network,
            volume_mounts=transformed_mounts,
            lifecycle=lifecycle,
            secure=secure,
        )
        self.sandbox_id = self._cell.cell_id
        self.files = FilesystemAdapter(self._cell)

    @classmethod
    def create(
        cls,
        api_key: Optional[str] = None,
        template: str = "python3",
        timeout: int = 300,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        allow_internet_access: bool = True,
        network: Optional[Dict[str, Any]] = None,
        volume_mounts: Optional[Dict[str, Any]] = None,
        lifecycle: Optional[Dict[str, Any]] = None,
        secure: bool = True,
        **kwargs: Any,
    ) -> "Sandbox":
        """Create a new sandbox (E2B factory method).

        E2B API: sandbox = Sandbox.create()

        Accepts the same parameters as __init__. See Sandbox.__init__
        docstring for details on each parameter.
        """
        return cls(
            api_key=api_key,
            template=template,
            timeout=timeout,
            metadata=metadata,
            envs=envs,
            allow_internet_access=allow_internet_access,
            network=network,
            volume_mounts=volume_mounts,
            lifecycle=lifecycle,
            secure=secure,
            **kwargs,
        )

    def run_code(
        self,
        code: str,
        language: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        on_result: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """Execute code in the sandbox.

        This is the core E2B API method. Maintains state between calls.

        When streaming callbacks (``on_stdout``, ``on_stderr``, etc.) are
        provided, they are passed through to ``Cell.run()`` which delegates
        to the SSE streaming endpoint in HTTP mode. Callbacks fire in
        real-time as output arrives from the gateway -- not post-hoc after
        execution completes.

        Args:
            code: Code string to execute
            language: Language override (default: sandbox template)
            on_stdout: Callback for stdout lines (real-time via SSE)
            on_stderr: Callback for stderr lines (real-time via SSE)
            on_result: Callback for result event dict (real-time via SSE)
            on_error: Callback for error message string (real-time via SSE)
            timeout: Execution timeout in seconds
            envs: Environment variables for this execution

        Returns:
            ExecutionResult with text, stdout, stderr, and error fields
        """
        try:
            result: CellResult = self._cell.run(
                code,
                language=language,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
                on_result=on_result,
                on_error=on_error,
            )
        except CellError as e:
            return ExecutionResult(
                text="",
                stdout="",
                stderr=str(e),
                error=ExecutionError(
                    name="CellError",
                    value=str(e),
                    traceback="",
                ),
            )

        # Callbacks already fired by Cell.run() during SSE streaming --
        # no need to fire them again here.

        error = None
        if result.exit_code != 0 and result.stderr:
            error = ExecutionError(
                name="ExecutionError",
                value=result.stderr.strip().split("\n")[-1] if result.stderr.strip() else "",
                traceback=result.stderr,
            )

        return ExecutionResult(
            text=result.stdout.strip(),
            stdout=result.stdout,
            stderr=result.stderr,
            error=error,
            exit_code=result.exit_code,
            latency_ms=getattr(result, "latency_ms", 0.0),
            receipt=getattr(result, "receipt", None),
        )

    def kill(self) -> None:
        """Kill the sandbox and free resources.

        E2B API: sandbox.kill()
        """
        self._cell.kill()

    def close(self) -> None:
        """Alias for kill() — E2B uses both."""
        self.kill()

    def keep_alive(self, duration: int = 60) -> None:
        """Keep the sandbox alive (no-op in Synapse, sessions auto-manage)."""
        pass

    # ─── Sprint A Batch 1: Lifecycle + metadata + envs ─────────

    def set_timeout(self, timeout_secs: int) -> None:
        """Update the sandbox's inactivity timeout (E2B parity)."""
        self._cell.set_timeout(timeout_secs)

    def refresh(self) -> None:
        """Reset the inactivity timer, extending the sandbox's lifetime."""
        self._cell.refresh()

    def is_running(self) -> bool:
        """Check whether the sandbox is currently running."""
        return self._cell.is_running()

    def patch_metadata(self, patch: dict) -> dict:
        """Merge key/value pairs into the sandbox's metadata."""
        return self._cell.patch_metadata(patch)

    def get_envs(self) -> dict:
        """Return the sandbox's current environment variable map."""
        return self._cell.get_envs()

    def patch_envs(self, patch: dict) -> dict:
        """Merge key/value pairs into the sandbox's environment variables."""
        return self._cell.patch_envs(patch)

    # ─── Sprint A Batch 2: Process management ─────────────────

    def start_process(self, command: str):
        """Start a background subprocess. Returns a ProcessHandle."""
        return self._cell.start_process(command)

    def list_processes(self) -> list:
        """List all background processes for this sandbox."""
        return self._cell.list_processes()

    def kill_process(self, command_id: str) -> None:
        """Kill a background process by command_id."""
        self._cell.kill_process(command_id)

    def send_process_stdin(self, command_id: str, data: str) -> None:
        """Send data to a background process's stdin."""
        self._cell.send_process_stdin(command_id, data)

    # ─── Sprint A Batch 5: Pause / Resume / Snapshots ─────────

    def pause(self) -> str:
        """Pause the sandbox and take a filesystem snapshot."""
        return self._cell.pause()

    def resume(self) -> None:
        """Resume a paused sandbox."""
        self._cell.resume()

    def list_snapshots(self) -> list:
        """List snapshot manifests for this sandbox."""
        return self._cell.list_snapshots()

    # ─── Sprint A Batch 3: PTY ────────────────────────────────

    @property
    def pty(self):
        """PTY namespace (E2B sandbox.pty parity)."""
        return self._cell.pty

    # ─── Sprint A Batch 6: Git client ─────────────────────────

    @property
    def git(self):
        """Git client namespace (E2B sandbox.git parity)."""
        return self._cell.git

    @property
    def id(self) -> str:
        """Sandbox ID (E2B compat)."""
        return self.sandbox_id

    def get_info(self) -> "SandboxInfo":
        """Get typed E2B-compatible SandboxInfo for this sandbox."""
        return self._cell.get_info()

    @classmethod
    def get_info_for(
        cls,
        sandbox_id: str,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
    ) -> "SandboxInfo":
        """E2B-compatible static get_info: Sandbox.get_info_for(sandbox_id).

        Note: E2B names this Sandbox.get_info(sandbox_id). Python lacks true
        overloads; Cell exposes both the instance get_info() and this static
        get_info_for() to make the dispatch explicit.

        Delegates to Cell.connect(sandbox_id) -> get_info() so the gateway
        verifies the sandbox exists before returning info.
        """
        cell = Cell.connect(sandbox_id, api_url=api_url, api_key=api_key)
        return cell.get_info()

    @classmethod
    def connect(
        cls,
        sandbox_id: str,
        timeout: Optional[int] = None,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
    ) -> "Sandbox":
        """E2B-compatible: attach to an existing sandbox by ID.

        Cell's paused-sandbox auto-resume is deferred to milestone 2.12;
        for 1.11 this simply verifies the sandbox is Running and returns a
        working Sandbox bound to that ID. Raises if the sandbox is killed
        or does not exist.
        """
        timeout_ms = (timeout * 1000) if timeout else 3_600_000
        cell = Cell.connect(
            sandbox_id,
            api_url=api_url,
            api_key=api_key,
            timeout_ms=timeout_ms,
        )
        # Build a Sandbox bound to the existing Cell without re-creating
        inst = cls.__new__(cls)
        inst._cell = cell
        inst.sandbox_id = cell.cell_id
        inst.files = FilesystemAdapter(cell)
        return inst

    @classmethod
    def list(
        cls,
        query: Optional[SandboxQuery] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        api_url: str = "http://localhost:8002",
        api_key: Optional[str] = None,
    ) -> SandboxPaginator:
        """List sandboxes with pagination + optional filters.

        E2B-compatible: ``Sandbox.list(query, limit, next_token)``.
        Returns a ``SandboxPaginator``; call ``.next_items()`` until
        ``.has_next`` is False.

        Delegates directly to ``Cell.list()``.
        """
        return Cell.list(
            query=query,
            limit=limit,
            next_token=next_token,
            api_url=api_url,
            api_key=api_key,
        )

    def reconnect(self, timeout: Optional[int] = None) -> "Sandbox":
        """E2B-compatible instance variant of connect().

        In Cell today this is effectively a no-op: the sandbox is already
        connected; we just validate that it is still Running and return self.
        Kept for E2B drop-in API matching.

        Note: E2B exposes this as an instance ``connect()`` method via a
        custom ``class_method_variant`` decorator that overloads the same
        name for both static and instance calls. Python cannot cleanly do
        this, so the instance variant lives under ``reconnect()`` and the
        common drop-in signature (``Sandbox.connect(sandbox_id)``) is the
        static classmethod above.

        Milestone 2.12 will add pause/resume and this method will resume
        a paused sandbox; for 1.11 it only validates that the cell is alive.
        """
        info = self._cell.get_info()
        if info.state == SandboxState.KILLED:
            raise CellError(f"Sandbox {self.sandbox_id} has been killed")
        return self

    # ─── Context Manager ─────────────────────────────────────────

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *args: Any) -> None:
        self.kill()

    def __repr__(self) -> str:
        return f"Sandbox(id={self.sandbox_id[:12]}... synapse-powered)"
