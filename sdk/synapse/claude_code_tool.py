"""Claude Code integration for Synapse Cell — Phase A4.

Provides tool definitions compatible with Anthropic's tool_use API,
enabling Claude to execute code, manage files, and run commands in
a Synapse Cell sandbox.

Usage:
    from synapse.cell import Cell
    from synapse.claude_code_tool import get_synapse_tools, handle_tool_call

    cell = Cell(api_url="http://localhost:8002", persistent=True)
    tools = get_synapse_tools()

    # Pass tools to Claude's API, then handle the response:
    result = handle_tool_call(cell, tool_name, tool_input)
"""
from __future__ import annotations

from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from synapse.cell import Cell


def get_synapse_tools() -> List[Dict[str, Any]]:
    """Return Anthropic tool_use-compatible tool definitions.

    These can be passed directly to the `tools` parameter of
    `anthropic.messages.create()`.

    Returns:
        List of tool definition dicts.
    """
    return [
        {
            "name": "synapse_run_code",
            "description": (
                "Execute Python code in an isolated Synapse Cell sandbox. "
                "The sandbox has persistent state — variables and imports "
                "carry over between calls. Use this for data analysis, "
                "computation, file generation, or testing code."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute.",
                    },
                },
                "required": ["code"],
            },
        },
        {
            "name": "synapse_run_command",
            "description": (
                "Execute a shell command in the sandbox (e.g., git, pip, ls). "
                "Returns stdout, stderr, and exit code."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                },
                "required": ["command"],
            },
        },
        {
            "name": "synapse_write_file",
            "description": (
                "Write a file to the sandbox filesystem. Creates parent "
                "directories as needed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to /data/.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content (text).",
                    },
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "synapse_read_file",
            "description": "Read a file from the sandbox filesystem.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to /data/.",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "synapse_list_files",
            "description": "List files in a sandbox directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: root).",
                        "default": "",
                    },
                },
            },
        },
    ]


def handle_tool_call(
    cell: "Cell",
    tool_name: str,
    tool_input: Dict[str, Any],
) -> str:
    """Handle a Claude tool_use call by dispatching to the Cell SDK.

    Args:
        cell: Active Synapse Cell instance.
        tool_name: The tool name from Claude's response.
        tool_input: The input dict from Claude's response.

    Returns:
        String result suitable for tool_result content.

    Raises:
        ValueError: If tool_name is unrecognized.
    """
    if tool_name == "synapse_run_code":
        result = cell.run(tool_input["code"])
        parts = []
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr}")
        if not parts:
            parts.append(f"(exit code {result.exit_code}, no output)")
        return "\n".join(parts)

    elif tool_name == "synapse_run_command":
        result = cell.command(tool_input["command"])
        if hasattr(result, "stdout"):
            parts = []
            if result.stdout:
                parts.append(result.stdout)
            if result.stderr:
                parts.append(f"stderr: {result.stderr}")
            return "\n".join(parts) or "(no output)"
        return str(result)

    elif tool_name == "synapse_write_file":
        cell.write_file(tool_input["path"], tool_input["content"])
        return f"File written: {tool_input['path']}"

    elif tool_name == "synapse_read_file":
        return cell.read_file(tool_input["path"])

    elif tool_name == "synapse_list_files":
        entries = cell.list_files(tool_input.get("path", ""))
        lines = [f"{'[DIR]' if e.type == 'dir' else '     '} {e.name}" for e in entries]
        return "\n".join(lines) or "(empty directory)"

    else:
        raise ValueError(f"Unknown Synapse tool: {tool_name}")
