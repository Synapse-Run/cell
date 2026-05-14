"""OpenAI Agents SDK tool for Synapse Cell — Sprint B Batch 7.

Provides a Cell code-execution tool compatible with the OpenAI Agents SDK
(https://github.com/openai/openai-agents-python).

Usage:
    from synapse.openai_agents_tool import synapse_cell_tool

    # Register with an OpenAI agent:
    from agents import Agent
    agent = Agent(
        name="coder",
        tools=[synapse_cell_tool],
    )

Requires: pip install openai-agents
"""
from __future__ import annotations

import os
from typing import Any

try:
    from agents import function_tool
    _HAS_AGENTS = True
except ImportError:
    _HAS_AGENTS = False
    # Stub so the module is importable without the SDK
    def function_tool(fn: Any = None, **kw: Any) -> Any:  # type: ignore
        def _wrap(f: Any) -> Any:
            return f
        return _wrap(fn) if fn else _wrap


@function_tool
def synapse_cell_execute(code: str) -> str:
    """Execute Python code in a secure, high-speed Synapse Cell sandbox.

    200x faster than E2B. Sub-millisecond Wasm execution with persistent
    filesystem and cryptographic execution receipts.

    Args:
        code: Python source code to execute in the sandbox.

    Returns:
        Execution result including stdout, stderr, and exit code.
    """
    from synapse.cell import Cell

    api_key = os.environ.get("SYNAPSE_API_KEY", "")
    # Default to local (zero-config PyO3 mode). Customers override with
    # SYNAPSE_API_URL=https://... when they have a gateway.
    api_url = os.environ.get("SYNAPSE_API_URL", "local")

    cell = None
    try:
        cell = Cell(api_key=api_key, api_url=api_url)
        result = cell.run(code)
        output = f"Exit code: {result.exit_code}\nStdout: {result.stdout}"
        if result.stderr:
            output += f"\nStderr: {result.stderr}"
        return output
    except Exception as e:
        return f"Error: {e!s}"
    finally:
        if cell is not None:
            try:
                cell.kill()
            except Exception:
                pass


# Convenience alias matching the naming pattern from langchain_tool.py
synapse_cell_tool = synapse_cell_execute


@function_tool
def synapse_cell_command(command: str) -> str:
    """Run a shell command in a secure Synapse Cell sandbox.

    Supports: git, pip, ls, cat, grep, find, curl, and more.

    Args:
        command: Shell command to execute.

    Returns:
        Command output (stdout + stderr).
    """
    from synapse.cell import Cell

    api_key = os.environ.get("SYNAPSE_API_KEY", "")
    api_url = os.environ.get("SYNAPSE_API_URL", "local")

    cell = None
    try:
        cell = Cell(api_key=api_key, api_url=api_url)
        result = cell.command(command)
        if hasattr(result, "stdout"):
            return result.stdout or "(no output)"
        return str(result)
    except Exception as e:
        return f"Error: {e!s}"
    finally:
        if cell is not None:
            try:
                cell.kill()
            except Exception:
                pass


@function_tool
def synapse_cell_write_file(path: str, content: str) -> str:
    """Write a file to the Synapse Cell sandbox filesystem.

    Args:
        path: File path relative to /data/.
        content: File content (text).

    Returns:
        Confirmation message.
    """
    from synapse.cell import Cell

    api_key = os.environ.get("SYNAPSE_API_KEY", "")
    api_url = os.environ.get("SYNAPSE_API_URL", "local")

    cell = None
    try:
        cell = Cell(api_key=api_key, api_url=api_url)
        cell.write_file(path, content)
        return f"File written: {path}"
    except Exception as e:
        return f"Error: {e!s}"
    finally:
        if cell is not None:
            try:
                cell.kill()
            except Exception:
                pass


# All Synapse tools for convenient agent registration:
#   agent = Agent(name="coder", tools=synapse_tools)
synapse_tools = [synapse_cell_execute, synapse_cell_command, synapse_cell_write_file]
