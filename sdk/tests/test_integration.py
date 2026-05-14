#!/usr/bin/env python3
"""Integration tests for the current Synapse SDK preview surface."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from synapse import Synapse, ExecutionResult, SynapseError, AssertionError
from synapse.langchain_tool import SynapseExecuteTool, SynapseValidateTool


# ─── Mock Gateway ───

class MockGatewayHandler(BaseHTTPRequestHandler):
    request_count = 0

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length).decode())
        MockGatewayHandler.request_count += 1

        if self.path == "/v1/execute":
            if "code" in body:
                code = body["code"]
                if "error" in code:
                    self._json(400, {"status": "error", "error": "parse_error", "error_type": "parse_error"})
                elif "@assert" in code:
                    self._json(200, {
                        "status": "success", "result": 42, "stdout": "", "arena_pos": 4096,
                        "latency_ms": 1.0,
                        "assertions": [{"pass": True, "expected": 42, "got": 42}],
                    })
                else:
                    self._json(200, {"status": "success", "result": 2500, "stdout": "", "arena_pos": 4096, "latency_ms": 0.8})
            elif "wasm" in body:
                self._json(200, {"status": "success", "result": 42, "stdout": "", "arena_pos": 4096, "latency_ms": 0.5})
            else:
                self._json(400, {"status": "error", "error": "missing_code"})
        elif self.path == "/v1/execute/python":
            self._json(200, {"status": "success", "result": 42, "stdout": "", "arena_pos": 4096, "latency_ms": 1.1})
        else:
            self._json(404, {"error": "not_found"})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress log output


_mock_server = None
_mock_port = 18999


def setup_mock():
    global _mock_server
    if _mock_server:
        return
    _mock_server = HTTPServer(("127.0.0.1", _mock_port), MockGatewayHandler)
    t = threading.Thread(target=_mock_server.serve_forever, daemon=True)
    t.start()


def get_client():
    return Synapse(api_key="sk_live_test", base_url=f"http://127.0.0.1:{_mock_port}", max_retries=0)


# ─── Tests ───

def test_execute_syn():
    """Test execute_syn returns correct result."""
    client = get_client()
    result = client.execute_syn("@f 0 main [ * 50 50 ]")
    assert isinstance(result, ExecutionResult)
    assert result.result == 2500
    assert result.latency_ms > 0
    print("PASS: execute_syn")


def test_execute_wasm():
    """Test execute_wasm returns correct result."""
    wasm = bytes([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
                  0x01, 0x05, 0x01, 0x60, 0x00, 0x01, 0x7e,
                  0x03, 0x02, 0x01, 0x00,
                  0x07, 0x08, 0x01, 0x04, 0x6d, 0x61, 0x69, 0x6e, 0x00, 0x00,
                  0x0a, 0x06, 0x01, 0x04, 0x00, 0x42, 0x2a, 0x0b])
    client = get_client()
    result = client.execute_wasm(wasm)
    assert result.result == 42
    print("PASS: execute_wasm")


def test_structured_error():
    """Test structured error handling."""
    client = get_client()
    try:
        client.execute_syn("@f 0 main [ error_trigger ]")
        assert False, "Should have raised"
    except SynapseError as e:
        assert e.status_code == 400
        assert "parse_error" in e.error
        print(f"PASS: structured error: {e.error}")


def test_assertions():
    """Test execute_syn_with_assert."""
    client = get_client()
    result = client.execute_syn_with_assert("@f 0 main [ 42 ]\n@assert == result 42")
    assert result.assertions is not None
    assert result.assertions[0]["pass"] is True
    print("PASS: assertions")


def test_execute_python():
    """Test restricted Python execution endpoint."""
    client = get_client()
    result = client.execute_python("result = 21 + 21")
    assert result.result == 42
    print("PASS: execute_python")


def test_validate_unavailable():
    """Test validate compatibility shim is explicitly unavailable."""
    client = get_client()
    try:
        client.validate("@f 0 main [ 42 ]")
        assert False, "validate() should be unavailable on the current preview surface"
    except NotImplementedError:
        print("PASS: validate unavailable")


def test_langchain_tool():
    """Test LangChain tool wrapper."""
    os.environ["SYNAPSE_API_KEY"] = "sk_live_test"
    os.environ["SYNAPSE_BASE_URL"] = f"http://127.0.0.1:{_mock_port}"
    tool = SynapseExecuteTool()
    result = tool.run("@f 0 main [ * 50 50 ]")
    assert "2500" in result
    print(f"PASS: LangChain tool: {result}")


def test_langchain_validate_tool():
    """Test LangChain validate shim returns a clear message."""
    tool = SynapseValidateTool()
    result = tool.run("@f 0 main [ 42 ]")
    assert "not part of the current Synapse preview surface" in result
    print(f"PASS: LangChain validate shim: {result}")


def test_invalid_wasm():
    """Test invalid wasm rejection."""
    client = get_client()
    try:
        client.execute_wasm(b"not wasm")
        assert False
    except ValueError:
        print("PASS: invalid wasm rejected")


def test_repr():
    """Test ExecutionResult repr."""
    r = ExecutionResult(42, "hello", 4096, 1.5)
    assert "42" in repr(r)
    print(f"PASS: repr")


if __name__ == "__main__":
    setup_mock()
    print("=" * 50)
    print("Synapse SDK Integration Tests")
    print("=" * 50)
    tests = [
        test_execute_syn, test_execute_wasm, test_structured_error,
        test_assertions, test_execute_python, test_validate_unavailable, test_langchain_tool,
        test_langchain_validate_tool, test_invalid_wasm, test_repr,
    ]
    passed = 0
    for t in tests:
        print(f"\n--- {t.__doc__.strip()} ---")
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL: {e}")
    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
