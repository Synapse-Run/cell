"""Vercel AI SDK integration for Synapse Cell.

Provides tool definitions compatible with the Vercel AI SDK's
`@ai-sdk/core` tool interface, enabling AI models to execute code
in Synapse Cell sandboxes.

Usage (Next.js API route):
    from synapse.vercel_ai_tool import synapse_tools_vercel

    # In your API route:
    tools = synapse_tools_vercel
"""
from __future__ import annotations

from typing import Any, Dict, List


def get_vercel_tool_definitions() -> List[Dict[str, Any]]:
    """Return tool definitions in Vercel AI SDK format.

    Compatible with the `tools` parameter of `generateText()` and
    `streamText()` in `@ai-sdk/core`.

    Returns:
        List of tool definition dicts with name, description, parameters.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "synapse_execute",
                "description": (
                    "Execute Python code in a secure, high-performance "
                    "Synapse Cell sandbox. 200x faster than alternatives. "
                    "Supports persistent state between calls."
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
        },
        {
            "type": "function",
            "function": {
                "name": "synapse_command",
                "description": "Run a shell command in the sandbox.",
                "parameters": {
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
        },
        {
            "type": "function",
            "function": {
                "name": "synapse_write_file",
                "description": "Write a file to the sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path."},
                        "content": {"type": "string", "description": "File content."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "synapse_read_file",
                "description": "Read a file from the sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path."},
                    },
                    "required": ["path"],
                },
            },
        },
    ]


def handle_vercel_tool_call(cell, tool_name: str, args: Dict[str, Any]) -> str:
    """Dispatch a Vercel AI SDK tool call to the Cell.

    Args:
        cell: Active Synapse Cell instance.
        tool_name: Tool name from the AI response.
        args: Tool arguments dict.

    Returns:
        String result for tool_result.
    """
    if tool_name == "synapse_execute":
        result = cell.run(args["code"])
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"stderr: {result.stderr}")
        return "\n".join(parts) or f"(exit {result.exit_code})"

    elif tool_name == "synapse_command":
        result = cell.command(args["command"])
        return str(getattr(result, "stdout", result)) or "(no output)"

    elif tool_name == "synapse_write_file":
        cell.write_file(args["path"], args["content"])
        return f"Written: {args['path']}"

    elif tool_name == "synapse_read_file":
        return cell.read_file(args["path"])

    raise ValueError(f"Unknown tool: {tool_name}")


# Convenience export
synapse_tools_vercel = get_vercel_tool_definitions()
