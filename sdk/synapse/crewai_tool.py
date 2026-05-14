"""CrewAI tools for Synapse Cell — secure code execution for AI agents.

Two tools:
  SynapseCellCrewTool  — Execute Python in a Cell sandbox (200x faster than E2B)
  SynapseCrewTool      — Execute .syn code on the preview gateway

Synapse Cell provides:
  - Sub-millisecond Wasm execution (200x faster than E2B)
  - Real-time streaming via on_stdout/on_stderr callbacks
  - Persistent filesystem (read/write/list/mkdir)
  - Cryptographic execution receipts (SHA-256 chain)
  - Drop-in E2B replacement (same API surface)

Usage:
    from synapse.crewai_tool import SynapseCellCrewTool

    tool = SynapseCellCrewTool(api_url="http://localhost:8002")
    result = tool._run(code="print('Hello from Cell!')")

    # In a CrewAI crew:
    from crewai import Agent
    agent = Agent(
        role="Code Executor",
        tools=[SynapseCellCrewTool()],
        llm=llm,
    )

Requires: pip install crewai
"""
import os
from typing import Any

try:
    from crewai.tools import BaseTool as CrewBaseTool
except ImportError:
    # Stub so the module can be imported without crewai installed.
    # Accepts kwargs so `SynapseCellCrewTool(api_url="local")` works as-is.
    class CrewBaseTool:  # type: ignore[no-redef]
        name: str = ""
        description: str = ""
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)
        def _run(self, *a: Any, **kw: Any) -> str:  # pragma: no cover - overridden
            raise NotImplementedError
        def run(self, *a: Any, **kw: Any) -> str:
            return self._run(*a, **kw)


# ─── Cell Tool (primary — commercial sandbox) ──────────────────────────

class SynapseCellCrewTool(CrewBaseTool):
    """Execute Python code in a secure, persistent Synapse Cell sandbox.

    200x faster than E2B. Sub-millisecond Wasm execution with real-time
    streaming, persistent filesystem, and cryptographic execution receipts.
    """

    name: str = "synapse_cell_executor"
    description: str = (
        "Execute Python code within a persistent, high-speed Wasm sandbox "
        "(200x faster than E2B). Supports real-time streaming, filesystem "
        "operations, and cryptographic execution receipts. "
        "Input: raw Python code. Returns stdout, stderr, and execution metadata."
    )
    api_url: str = ""
    api_key: str = ""

    # Pydantic v2 model_config (CrewAI v0.80+ uses Pydantic v2)
    model_config = {"arbitrary_types_allowed": True}

    def _run(self, code: str = "", **kwargs: Any) -> str:
        """Execute Python code and return the result.

        CrewAI passes tool input as keyword arguments.
        """
        from synapse.cell import Cell

        key = self.api_key or os.environ.get("SYNAPSE_API_KEY", "")
        cell_kwargs: dict[str, Any] = {}
        if self.api_url:
            cell_kwargs["api_url"] = self.api_url

        try:
            with Cell(api_key=key, **cell_kwargs) as cell:
                result = cell.run(code)
                output = f"Exit code: {result.exit_code}\nStdout: {result.stdout}"
                if result.stderr:
                    output += f"\nStderr: {result.stderr}"
                if result.receipt:
                    output += f"\nReceipt: {result.receipt.execution_id}"
                return output
        except Exception as e:
            return f"Error executing cell: {e!s}"


# ─── .syn preview tool (research gateway) ──────────────────────────────

class SynapseCrewTool(CrewBaseTool):
    """Execute .syn code on native Wasm silicon via Synapse."""

    name: str = "synapse_execute"
    description: str = (
        "Execute .syn code on the Synapse native Wasm engine. "
        "Input: .syn source code (prefix notation). "
        "Output: execution result with latency <2ms."
    )

    model_config = {"arbitrary_types_allowed": True}

    def _run(self, code: str = "", **kwargs: Any) -> str:
        """Execute .syn code and return the result."""
        from synapse.client import Synapse, SynapseError

        api_key = os.environ.get("SYNAPSE_API_KEY", "")
        base_url = os.environ.get("SYNAPSE_BASE_URL", "https://api.synapserun.dev")
        client = Synapse(api_key=api_key, base_url=base_url)
        try:
            result = client.execute_syn(code)
            return f"Result: {result.result}, Arena: {result.arena_pos}, Latency: {result.latency_ms}ms"
        except SynapseError as e:
            return f"Error: {e.error}"
