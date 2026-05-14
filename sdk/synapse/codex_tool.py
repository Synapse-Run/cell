"""OpenAI Codex integration for Synapse Cell.

Codex uses the same OpenAI function-calling protocol as the
standard Agents SDK. This module re-exports the Agents SDK tools
for Codex-specific usage.

Usage:
    from synapse.codex_tool import synapse_codex_tools

    # Register tools with Codex via the function-calling API
"""
from __future__ import annotations

from synapse.claude_code_tool import get_synapse_tools, handle_tool_call

# Codex uses the same function-calling format as Claude/OpenAI
synapse_codex_tools = get_synapse_tools()
synapse_codex_handle = handle_tool_call
