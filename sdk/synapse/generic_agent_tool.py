"""Generic function-calling integration for Synapse Cell.

Works with any AI agent that supports the standard OpenAI
function-calling / tool_use protocol — including Amp, OpenCode,
OpenClaw, and similar frameworks.

This module provides framework-agnostic tool definitions that can
be adapted to any function-calling interface.

Usage:
    from synapse.generic_agent_tool import SYNAPSE_TOOL_DEFS, dispatch

    # Pass SYNAPSE_TOOL_DEFS to your agent's tool registration
    # Call dispatch(cell, tool_name, args) to handle tool invocations
"""
from __future__ import annotations

from typing import Any, Dict, List

# Framework-agnostic tool definitions
SYNAPSE_TOOL_DEFS: List[Dict[str, Any]] = [
    {
        "name": "synapse_run_code",
        "description": (
            "Execute Python code in a secure Synapse Cell sandbox. "
            "200x faster than E2B. Persistent state across calls."
        ),
        "parameters": {
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
        "description": "Run a shell command in the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "synapse_write_file",
        "description": "Write a file to the sandbox filesystem.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to /data/."},
                "content": {"type": "string", "description": "File content."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "synapse_read_file",
        "description": "Read a file from the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to /data/."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "synapse_list_files",
        "description": "List files in a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path.", "default": ""},
            },
        },
    },
]


def dispatch(cell, tool_name: str, args: Dict[str, Any]) -> str:
    """Dispatch a tool call to the Cell SDK.

    Args:
        cell: Active Synapse Cell instance.
        tool_name: Tool name from the agent's response.
        args: Tool arguments dict.

    Returns:
        String result.
    """
    if tool_name == "synapse_run_code":
        result = cell.run(args["code"])
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"stderr: {result.stderr}")
        return "\n".join(parts) or f"(exit {result.exit_code})"

    elif tool_name == "synapse_run_command":
        result = cell.command(args["command"])
        return str(getattr(result, "stdout", result)) or "(no output)"

    elif tool_name == "synapse_write_file":
        cell.write_file(args["path"], args["content"])
        return f"Written: {args['path']}"

    elif tool_name == "synapse_read_file":
        return cell.read_file(args["path"])

    elif tool_name == "synapse_list_files":
        entries = cell.list_files(args.get("path", ""))
        return "\n".join(e.name for e in entries) or "(empty)"

    raise ValueError(f"Unknown tool: {tool_name}")
