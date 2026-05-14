"""Async wrapper for Synapse Cell — Sprint B Batch 8.

Wraps the synchronous Cell class via asyncio.to_thread() so every method
can be awaited. This is the standard pattern used by httpx, SQLAlchemy,
and other Python libraries that need async compatibility without rewriting
the entire I/O layer.

Usage:
    import asyncio
    from synapse.async_cell import AsyncCell

    async def main():
        async with AsyncCell(api_url="http://localhost:8002") as cell:
            result = await cell.run("print('hello async!')")
            print(result.stdout)

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from synapse.cell import (
    Cell,
    CellResult,
    CellError,
    SandboxInfo,
    ProcessHandle,
)


class AsyncCell:
    """Async wrapper for Cell. Every I/O method delegates to asyncio.to_thread().

    Supports async context manager (``async with AsyncCell(...) as cell:``).
    Constructor args are identical to :class:`Cell`.
    """

    def __init__(self, **kwargs: Any):
        self._cell = Cell(**kwargs)
        self.cell_id = self._cell.cell_id
        self.template = self._cell.template
        self.persistent = self._cell.persistent

    # ─── Context manager ─────────────────────────────────────────

    async def __aenter__(self) -> "AsyncCell":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.kill()

    # ─── Class methods ───────────────────────────────────────────

    @classmethod
    async def connect(cls, cell_id: str, **kwargs: Any) -> "AsyncCell":
        """Attach to an existing running Cell (async version of Cell.connect)."""
        cell = await asyncio.to_thread(Cell.connect, cell_id, **kwargs)
        inst = cls.__new__(cls)
        inst._cell = cell
        inst.cell_id = cell.cell_id
        inst.template = cell.template
        inst.persistent = cell.persistent
        return inst

    # ─── Code execution ──────────────────────────────────────────

    async def run(
        self,
        code: str,
        language: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        on_result: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> CellResult:
        """Execute code in the sandbox (async)."""
        return await asyncio.to_thread(
            self._cell.run, code,
            language=language,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            on_result=on_result,
            on_error=on_error,
        )

    async def command(
        self,
        cmd: str,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        background: bool = False,
    ) -> Any:
        """Run a shell command (async)."""
        return await asyncio.to_thread(
            self._cell.command, cmd,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            background=background,
        )

    async def start_process(self, command: str) -> ProcessHandle:
        """Start a background process (async)."""
        return await asyncio.to_thread(self._cell.start_process, command)

    async def list_processes(self) -> list:
        """List background processes (async)."""
        return await asyncio.to_thread(self._cell.list_processes)

    # ─── Filesystem ──────────────────────────────────────────────

    async def write_file(self, path: str, content: str) -> None:
        """Write a file (async)."""
        await asyncio.to_thread(self._cell.write_file, path, content)

    async def write_files(self, files: dict) -> dict:
        """Write multiple files in a single request (async)."""
        return await asyncio.to_thread(self._cell.write_files, files)

    async def read_file(self, path: str) -> str:
        """Read a file (async)."""
        return await asyncio.to_thread(self._cell.read_file, path)

    async def list_files(self, path: str = "/") -> list:
        """List directory contents (async)."""
        return await asyncio.to_thread(self._cell.list_files, path)

    async def file_exists(self, path: str) -> bool:
        """Check if path exists (async)."""
        return await asyncio.to_thread(self._cell.file_exists, path)

    async def file_info(self, path: str) -> Any:
        """Get file metadata (async)."""
        return await asyncio.to_thread(self._cell.file_info, path)

    async def remove_file(self, path: str) -> None:
        """Remove file or directory (async)."""
        await asyncio.to_thread(self._cell.remove_file, path)

    async def make_dir(self, path: str) -> None:
        """Create directory (async)."""
        await asyncio.to_thread(self._cell.make_dir, path)

    async def rename_file(self, old_path: str, new_path: str) -> Any:
        """Rename/move file (async)."""
        return await asyncio.to_thread(self._cell.rename_file, old_path, new_path)

    # ─── Lifecycle ───────────────────────────────────────────────

    async def kill(self) -> None:
        """Kill the cell (async)."""
        await asyncio.to_thread(self._cell.kill)

    async def get_info(self) -> SandboxInfo:
        """Get cell info (async)."""
        return await asyncio.to_thread(self._cell.get_info)

    async def is_running(self) -> bool:
        """Check if cell is running (async)."""
        return await asyncio.to_thread(self._cell.is_running)

    async def set_timeout(self, timeout_secs: int) -> None:
        """Update inactivity timeout (async)."""
        await asyncio.to_thread(self._cell.set_timeout, timeout_secs)

    async def refresh(self) -> None:
        """Reset inactivity timer (async)."""
        await asyncio.to_thread(self._cell.refresh)

    async def pause(self) -> str:
        """Pause cell + snapshot (async)."""
        return await asyncio.to_thread(self._cell.pause)

    async def resume(self) -> None:
        """Resume paused cell (async)."""
        await asyncio.to_thread(self._cell.resume)

    async def create_snapshot(self) -> str:
        """Create a snapshot while cell keeps running (async)."""
        return await asyncio.to_thread(self._cell.create_snapshot)

    async def list_snapshots(self) -> list:
        """List all snapshots for this cell (async)."""
        return await asyncio.to_thread(self._cell.list_snapshots)

    # ─── Metadata + envs ────────────────────────────────────────

    async def patch_metadata(self, patch: dict) -> dict:
        """Merge metadata (async)."""
        return await asyncio.to_thread(self._cell.patch_metadata, patch)

    async def get_envs(self) -> dict:
        """Get environment variables (async)."""
        return await asyncio.to_thread(self._cell.get_envs)

    async def patch_envs(self, patch: dict) -> dict:
        """Merge environment variables (async)."""
        return await asyncio.to_thread(self._cell.patch_envs, patch)

    # ─── Fetch ───────────────────────────────────────────────────

    async def fetch(self, url: str, **kwargs: Any) -> Any:
        """HTTP fetch via sandbox (async)."""
        return await asyncio.to_thread(self._cell.fetch, url, **kwargs)

    # ─── Volumes ─────────────────────────────────────────────────

    @property
    def volumes(self) -> "AsyncVolumesAdapter":
        """Async volumes adapter."""
        if not hasattr(self, '_async_volumes'):
            self._async_volumes = AsyncVolumesAdapter(self._cell.volumes)
        return self._async_volumes

    # ─── Namespaces (passthrough — PTY/git have their own async needs) ─

    @property
    def pty(self):
        """PTY namespace (sync — PTY is WebSocket-native, not thread-wrapped)."""
        return self._cell.pty

    @property
    def git(self):
        """Git namespace (sync methods — wrap individually if needed)."""
        return self._cell.git

    def __repr__(self) -> str:
        return f"AsyncCell({self.cell_id[:8]}...)"


class AsyncVolumesAdapter:
    """Async wrapper for VolumesAdapter — Sprint D."""

    def __init__(self, sync_adapter: Any):
        self._sync = sync_adapter

    async def read(self, volume_id: str, path: str) -> str:
        """Read a file from a volume (async)."""
        return await asyncio.to_thread(self._sync.read, volume_id, path)

    async def write(self, volume_id: str, path: str, content: str) -> None:
        """Write a file to a volume (async)."""
        await asyncio.to_thread(self._sync.write, volume_id, path, content)

    async def delete(self, volume_id: str) -> dict:
        """Delete a volume (async)."""
        return await asyncio.to_thread(self._sync.delete, volume_id)

    async def list_all(self) -> list:
        """List all volumes (async)."""
        return await asyncio.to_thread(self._sync.list_all)
