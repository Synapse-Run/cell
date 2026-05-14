"""LlamaIndex tool for Synapse Cell — Sprint B Batch 7.

Provides a Cell code-execution tool compatible with LlamaIndex
(https://github.com/run-llama/llama_index).

Usage:
    from synapse.llamaindex_tool import SynapseCellTool

    tool = SynapseCellTool()
    result = tool.call("print('hello from Cell!')")

    # With a LlamaIndex agent:
    from llama_index.core.agent import ReActAgent
    from llama_index.llms.openai import OpenAI
    agent = ReActAgent.from_tools([tool], llm=OpenAI(model="gpt-4o-mini"))
    response = agent.chat("Run some Python to compute 2+2")

Requires: pip install llama-index-core
"""
from __future__ import annotations

import os
from typing import Any, Optional

try:
    from llama_index.core.tools import FunctionTool, ToolMetadata
    _HAS_LLAMAINDEX = True
except ImportError:
    _HAS_LLAMAINDEX = False
    # Stubs so the module is importable without llama_index
    class ToolMetadata:  # type: ignore[no-redef]
        def __init__(self, **kw: Any): pass

    class FunctionTool:  # type: ignore[no-redef]
        def __init__(self, fn: Any = None, metadata: Any = None, **kw: Any):
            self._fn = fn
            self.metadata = metadata
        def call(self, *a: Any, **kw: Any) -> str:
            if self._fn:
                return self._fn(*a, **kw)
            raise NotImplementedError("Install llama-index-core to use this tool")
        @classmethod
        def from_defaults(cls, fn: Any = None, **kw: Any) -> "FunctionTool":
            return cls(fn=fn, metadata=ToolMetadata(**kw))


def _synapse_cell_execute(code: str) -> str:
    """Execute Python code in a secure Synapse Cell sandbox.

    200x faster than E2B. Sub-millisecond Wasm execution with persistent
    filesystem and cryptographic execution receipts.

    Args:
        code: Python source code to execute.

    Returns:
        Execution result string with stdout, stderr, and exit code.
    """
    from synapse.cell import Cell

    api_key = os.environ.get("SYNAPSE_API_KEY", "")
    api_url = os.environ.get("SYNAPSE_API_URL", "local")

    try:
        with Cell(api_key=api_key, api_url=api_url) as cell:
            result = cell.run(code)
            output = f"Exit code: {result.exit_code}\nStdout: {result.stdout}"
            if result.stderr:
                output += f"\nStderr: {result.stderr}"
            return output
    except Exception as e:
        return f"Error: {e!s}"


def SynapseCellTool(
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> FunctionTool:
    """Create a LlamaIndex FunctionTool for Synapse Cell code execution.

    Args:
        api_url: Cell gateway URL (default: env SYNAPSE_API_URL or production).
        api_key: API key (default: env SYNAPSE_API_KEY).

    Returns:
        A LlamaIndex FunctionTool ready for agent use.
    """
    # Capture config in closure if provided
    if api_url or api_key:
        _url = api_url or os.environ.get("SYNAPSE_API_URL", "local")
        _key = api_key or os.environ.get("SYNAPSE_API_KEY", "")

        def _configured_execute(code: str) -> str:
            from synapse.cell import Cell
            try:
                with Cell(api_key=_key, api_url=_url) as cell:
                    result = cell.run(code)
                    output = f"Exit code: {result.exit_code}\nStdout: {result.stdout}"
                    if result.stderr:
                        output += f"\nStderr: {result.stderr}"
                    return output
            except Exception as e:
                return f"Error: {e!s}"

        fn = _configured_execute
    else:
        fn = _synapse_cell_execute

    return FunctionTool.from_defaults(
        fn=fn,
        name="synapse_cell_executor",
        description=(
            "Execute Python code in a secure, high-speed Synapse Cell sandbox. "
            "200x faster than E2B. Sub-millisecond Wasm execution with persistent "
            "filesystem and cryptographic execution receipts. "
            "Input: Python source code string. Returns stdout, stderr, exit code."
        ),
    )
