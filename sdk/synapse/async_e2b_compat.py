"""Async E2B-compatible Sandbox wrapper — Sprint B Batch 8.

Wraps AsyncCell in the E2B Sandbox API shape for async frameworks.

Usage:
    import asyncio
    from synapse.async_e2b_compat import AsyncSandbox

    async def main():
        sandbox = await AsyncSandbox.create()
        result = await sandbox.run_code("print(42)")
        print(result.stdout)
        await sandbox.kill()

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from synapse.async_cell import AsyncCell


class AsyncSandbox:
    """Async E2B-compatible Sandbox wrapping AsyncCell."""

    def __init__(self, **kwargs: Any):
        self._cell = AsyncCell(**kwargs)
        self.sandbox_id = self._cell.cell_id

    @classmethod
    async def create(cls, **kwargs: Any) -> "AsyncSandbox":
        """Create a new sandbox (async version of Sandbox())."""
        inst = cls(**kwargs)
        return inst

    @classmethod
    async def connect(cls, sandbox_id: str, **kwargs: Any) -> "AsyncSandbox":
        """Connect to an existing sandbox (async)."""
        cell = await AsyncCell.connect(sandbox_id, **kwargs)
        inst = cls.__new__(cls)
        inst._cell = cell
        inst.sandbox_id = cell.cell_id
        return inst

    async def run_code(self, code: str, **kwargs: Any) -> Any:
        """Execute code (E2B run_code shape, async)."""
        return await self._cell.run(code, **kwargs)

    async def kill(self) -> None:
        """Kill the sandbox (async)."""
        await self._cell.kill()

    async def close(self) -> None:
        """Alias for kill (async)."""
        await self.kill()

    async def get_info(self) -> Any:
        """Get sandbox info (async)."""
        return await self._cell.get_info()

    async def is_running(self) -> bool:
        """Check if running (async)."""
        return await self._cell.is_running()

    async def pause(self) -> str:
        """Pause + snapshot (async)."""
        return await self._cell.pause()

    async def resume(self) -> None:
        """Resume (async)."""
        await self._cell.resume()

    # File operations
    async def write_file(self, path: str, content: str) -> None:
        await self._cell.write_file(path, content)

    async def read_file(self, path: str) -> str:
        return await self._cell.read_file(path)

    async def list_files(self, path: str = "/") -> list:
        return await self._cell.list_files(path)

    @property
    def pty(self):
        return self._cell.pty

    @property
    def git(self):
        return self._cell.git

    @property
    def id(self) -> str:
        return self.sandbox_id

    def __repr__(self) -> str:
        return f"AsyncSandbox({self.sandbox_id[:8]}...)"
