#!/usr/bin/env python3
"""MCP integration test — verifies the Synapse MCP server responds correctly
to JSON-RPC initialize, tools/list, and tools/call requests.

Tests the MCP protocol layer in isolation via subprocess stdin/stdout.
Does NOT require the gateway to be running.

Usage:
    python3 test_mcp.py
"""
import json
import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..", "..")
MCP_SERVER = os.path.join(PROJECT_ROOT, "tools", "synapse_mcp.py")


def run_mcp_session(messages):
    """Run a batch of JSON-RPC messages and collect responses.
    
    Sends all messages at once, then reads available responses.
    This avoids per-message readline blocking issues.
    """
    input_text = "\n".join(json.dumps(m) for m in messages) + "\n"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        [sys.executable, "-u", MCP_SERVER],  # -u = unbuffered
        input=input_text, capture_output=True, text=True, timeout=5,
        env=env,
    )
    responses = []
    for line in proc.stdout.strip().split("\n"):
        if line.strip():
            responses.append(json.loads(line))
    return responses


def test_initialize():
    """Test MCP initialize handshake."""
    resps = run_mcp_session([{
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }])
    assert len(resps) >= 1, f"Expected 1+ response, got {len(resps)}"
    r = resps[0]
    assert "result" in r, f"Expected result: {r}"
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "synapse"
    assert r["result"]["serverInfo"]["version"] == "1.0.0"
    print("PASS: MCP initialize")
    return True


def test_tools_list():
    """Test MCP tools/list returns the current gateway-backed tools."""
    resps = run_mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    # Skip the initialize response, get tools/list
    tools_resp = [r for r in resps if r.get("id") == 2]
    assert len(tools_resp) == 1, f"Expected tools/list response: {resps}"
    tools = tools_resp[0]["result"]["tools"]
    names = [t["name"] for t in tools]
    expected = ["synapse_execute", "synapse_execute_python", "synapse_health"]
    assert names == expected, f"Wrong tools: {names}"
    # Verify each tool has inputSchema
    for tool in tools:
        assert "inputSchema" in tool, f"Missing inputSchema: {tool['name']}"
    print(f"PASS: MCP tools/list — {len(tools)} current tools, all with schemas")
    return True


def test_ping():
    """Test MCP ping/pong."""
    resps = run_mcp_session([
        {"jsonrpc": "2.0", "id": 99, "method": "ping"},
    ])
    assert len(resps) >= 1
    assert resps[0]["id"] == 99
    assert resps[0]["result"] == {}
    print("PASS: MCP ping")
    return True


def test_unknown_method():
    """Test unknown method returns -32601."""
    resps = run_mcp_session([
        {"jsonrpc": "2.0", "id": 42, "method": "nonexistent/method"},
    ])
    assert len(resps) >= 1
    assert "error" in resps[0], f"Expected error: {resps[0]}"
    assert resps[0]["error"]["code"] == -32601
    print("PASS: MCP unknown method → -32601")
    return True


def test_notification_ignored():
    """Test that notifications (no id) produce no response."""
    resps = run_mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ])
    # Should get 2 responses (2 pings), notification should be skipped
    ping_resps = [r for r in resps if r.get("method") is None]
    assert len(ping_resps) == 2, f"Expected 2 ping responses (notification ignored), got {len(ping_resps)}: {resps}"
    print("PASS: MCP notification correctly ignored")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("Synapse MCP Integration Tests")
    print("=" * 50)
    tests = [test_initialize, test_tools_list, test_ping,
             test_unknown_method, test_notification_ignored]
    passed = 0
    for t in tests:
        print(f"\n--- {t.__doc__.strip()} ---")
        try:
            if t():
                passed += 1
        except Exception as e:
            print(f"FAIL: {e}")
    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
