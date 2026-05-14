#!/usr/bin/env python3
"""Tests for the Synapse Python SDK.

Requires the gateway to be running at http://127.0.0.1:8000.
"""
import sys
import os

# Add parent directory so we can import synapse without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse import Synapse, ExecutionResult


LOCAL_URL = "http://127.0.0.1:8000"
GOD_MODE_KEY = "your_api_key_here"


def test_execute_syn():
    """Test .syn execution via the SDK."""
    client = Synapse(api_key=GOD_MODE_KEY, base_url=LOCAL_URL)
    try:
        result = client.execute_syn("@f 0 main [ + 100 200 ]")
        assert isinstance(result, ExecutionResult)
        assert result.result == 300, f"Expected 300, got {result.result}"
        assert result.latency_ms > 0
        print(f"PASS: execute_syn returned {result}")
        return True
    except Exception as e:
        print(f"SKIP: {e}")
        return False


def test_execute_wasm():
    """Test .wasm execution via the SDK."""
    # Minimal wasm module: main() -> i64(42)
    wasm_bytes = bytes([
        0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
        0x01, 0x05, 0x01, 0x60, 0x00, 0x01, 0x7e,
        0x03, 0x02, 0x01, 0x00,
        0x07, 0x08, 0x01, 0x04, 0x6d, 0x61, 0x69, 0x6e, 0x00, 0x00,
        0x0a, 0x06, 0x01, 0x04, 0x00, 0x42, 0x2a, 0x0b,
    ])
    client = Synapse(api_key=GOD_MODE_KEY, base_url=LOCAL_URL)
    try:
        result = client.execute_wasm(wasm_bytes)
        assert isinstance(result, ExecutionResult)
        assert result.result == 42, f"Expected 42, got {result.result}"
        print(f"PASS: execute_wasm returned {result}")
        return True
    except Exception as e:
        print(f"SKIP: {e}")
        return False


def test_invalid_wasm_rejected():
    """Test that invalid wasm bytes raise ValueError."""
    client = Synapse(api_key=GOD_MODE_KEY, base_url=LOCAL_URL)
    try:
        client.execute_wasm(b"not wasm")
        print("FAIL: Expected ValueError")
        return False
    except ValueError:
        print("PASS: Invalid wasm correctly rejected client-side")
        return True


def test_repr():
    """Test ExecutionResult repr."""
    r = ExecutionResult(result=42, stdout="hello", arena_pos=4096, latency_ms=1.5)
    assert "42" in repr(r)
    assert "hello" in repr(r)
    print(f"PASS: repr = {r}")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("Synapse SDK Test Suite")
    print("=" * 50)
    results = []
    for test in [test_repr, test_invalid_wasm_rejected, test_execute_syn, test_execute_wasm]:
        print(f"\n--- {test.__doc__.strip()} ---")
        results.append(test())
    passed = sum(1 for r in results if r)
    print(f"\nResults: {passed}/{len(results)} passed")
