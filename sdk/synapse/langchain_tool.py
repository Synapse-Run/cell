"""LangChain tools for Synapse Cell — secure code execution for AI agents.

Three tools:
  SynapseCellExecuteTool  — Execute Python in a Cell sandbox (200x faster than E2B)
  SynapseExecuteTool      — Execute .syn code on the preview gateway
  SynapseValidateTool     — Legacy shim (deprecated)

Synapse Cell provides:
  - Sub-millisecond Wasm execution (200x faster than E2B)
  - Real-time streaming via on_stdout/on_stderr callbacks
  - Persistent filesystem (read/write/list/mkdir)
  - Cryptographic execution receipts (SHA-256 chain)
  - Drop-in E2B replacement (same API surface)

Usage:
    from synapse.langchain_tool import SynapseCellExecuteTool

    tool = SynapseCellExecuteTool(api_url="http://localhost:8002")
    result = tool._run("print('Hello from Cell!')")

    # With LangChain agent:
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4o-mini")
    # tool integrates via agent.bind_tools([tool])

Requires: pip install langchain-core  (or langchain>=0.3)
"""
import os
from typing import Optional, Any, Type

try:
    from langchain_core.tools import BaseTool
    from langchain_core.callbacks import CallbackManagerForToolRun
except ImportError:
    try:
        from langchain.tools import BaseTool
        from langchain.callbacks.manager import CallbackManagerForToolRun
    except ImportError:
        # Stub so the module can be imported without langchain installed.
        # Accepts kwargs so `SynapseCellExecuteTool(api_url="local")` works even
        # when langchain isn't installed (e.g., direct usage without agent framework).
        class BaseTool:  # type: ignore[no-redef]
            name: str = ""
            description: str = ""
            def __init__(self, **kwargs: Any) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)
            def _run(self, *a: Any, **kw: Any) -> str:  # pragma: no cover - implemented in subclass
                raise NotImplementedError
            def run(self, *a: Any, **kw: Any) -> str:
                return self._run(*a, **kw)
        CallbackManagerForToolRun = None  # type: ignore[assignment,misc]

# Pydantic v2 compatibility: try importing BaseModel for input schema
try:
    from pydantic import BaseModel, Field
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False


# ─── Cell Tool (primary — commercial sandbox) ──────────────────────────

if _HAS_PYDANTIC:
    class _CellExecuteInput(BaseModel):
        """Input schema for SynapseCellExecuteTool."""
        code: str = Field(description="Python source code to execute in the sandbox")

    class _SynExecuteInput(BaseModel):
        """Input schema for SynapseExecuteTool."""
        code: str = Field(description=".syn source code (prefix notation)")


class SynapseCellExecuteTool(BaseTool):
    """Execute Python code in a secure, persistent Synapse Cell sandbox.

    200x faster than E2B. Sub-millisecond Wasm execution with real-time
    streaming, persistent filesystem, and cryptographic execution receipts.
    Drop-in replacement for E2B code execution tools.
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

    # Pydantic v2 model_config (LangChain v0.3+ requires Pydantic v2)
    model_config = {"arbitrary_types_allowed": True}

    if _HAS_PYDANTIC:
        args_schema: Type[BaseModel] = _CellExecuteInput  # type: ignore[assignment]

    def _run(
        self,
        code: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,  # type: ignore[assignment]
    ) -> str:
        """Execute Python code and return the result.

        When a LangChain callback manager is present, streaming callbacks are
        wired so that stdout/stderr flow through the LangChain callback system
        in real-time.
        """
        from synapse.cell import Cell

        key = self.api_key or os.environ.get("SYNAPSE_API_KEY", "")
        kwargs: dict[str, Any] = {}
        if self.api_url:
            kwargs["api_url"] = self.api_url

        # Wire streaming callbacks through LangChain's callback manager
        on_stdout = None
        on_stderr = None
        if run_manager is not None:
            def on_stdout(line: str) -> None:
                run_manager.on_text(line + "\n", verbose=True)  # type: ignore[union-attr]

            def on_stderr(line: str) -> None:
                run_manager.on_text(f"[stderr] {line}\n", verbose=True)  # type: ignore[union-attr]

        try:
            with Cell(api_key=key, **kwargs) as cell:
                result = cell.run(code, on_stdout=on_stdout, on_stderr=on_stderr)
                output = f"Exit code: {result.exit_code}\nStdout: {result.stdout}"
                if result.stderr:
                    output += f"\nStderr: {result.stderr}"
                if result.receipt:
                    output += f"\nReceipt: {result.receipt.execution_id}"
                return output
        except Exception as e:
            return f"Error executing cell: {e!s}"


# ─── .syn preview tool (research gateway) ──────────────────────────────

class SynapseExecuteTool(BaseTool):
    """Execute .syn code on the Synapse preview gateway."""

    name: str = "synapse_execute"
    description: str = (
        "Execute .syn code on the Synapse native Wasm engine. "
        "Input is .syn source code. Returns the integer result and arena position. "
        "Use prefix notation: '+ 3 4' means 3+4."
    )

    model_config = {"arbitrary_types_allowed": True}

    if _HAS_PYDANTIC:
        args_schema: Type[BaseModel] = _SynExecuteInput  # type: ignore[assignment]

    def _run(
        self,
        code: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,  # type: ignore[assignment]
    ) -> str:
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


class SynapseValidateTool(BaseTool):
    """Legacy compatibility shim for the removed validate endpoint."""

    name: str = "synapse_validate"
    description: str = (
        "Legacy compatibility shim. The /v1/validate endpoint is not part "
        "of the current Synapse preview surface."
    )

    model_config = {"arbitrary_types_allowed": True}

    def _run(
        self,
        code: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,  # type: ignore[assignment]
    ) -> str:
        _ = code
        return (
            "Unavailable: /v1/validate is not part of the current Synapse preview surface. "
            "Use POST /v1/execute on a self-hosted gateway for current end-to-end verification."
        )
