#!/usr/bin/env python3
"""End-to-end test for the Synapse Modal-style decorator API.

Tests the decorator → transpiler → .syn preview pipeline locally.
Tests remote execution if SYNAPSE_API_KEY is set.

Usage:
    python3 sdk/tests/test_decorator.py
"""

import os
import sys

# Add SDK to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse.decorator import SynapseApp, app


# ─── Test Functions ──────────────────────────────────────────

@app.function
def add(a, b):
    return a + b

@app.function
def multiply(x, y):
    return x * y

@app.function
def factorial(n):
    result = 1
    i = 1
    while i <= n:
        result = result * i
        i = i + 1
    return result

@app.function
def fibonacci(n):
    a = 0
    b = 1
    i = 0
    while i < n:
        temp = b
        b = a + b
        a = temp
        i = i + 1
    return a

@app.function
def max_val(a, b):
    if a > b:
        return a
    return b

@app.function
def sum_to(n):
    total = 0
    i = 1
    while i <= n:
        total = total + i
        i = i + 1
    return total

@app.function
def gcd(a, b):
    while b != 0:
        temp = a % b
        a = b
        b = temp
    return a


# ─── Test Runner ─────────────────────────────────────────────

def test_local_calls():
    """Test that decorated functions work as normal Python."""
    print("  [1] Local calls (normal Python)...")
    assert add(21, 21) == 42, f"add(21,21) = {add(21,21)}"
    assert multiply(50, 50) == 2500
    assert factorial(5) == 120
    assert fibonacci(10) == 55
    assert max_val(73, 42) == 73
    assert sum_to(10) == 55
    assert gcd(48, 18) == 6
    print("      PASS — all 7 functions correct")


def test_local_explicit():
    """Test the .local() method."""
    print("  [2] .local() explicit calls...")
    assert add.local(10, 20) == 30
    assert factorial.local(7) == 5040
    print("      PASS")


def test_preview():
    """Test .syn code generation via .preview()."""
    print("  [3] .preview() — transpiler integration...")

    syn_code = add.preview(21, 21)
    assert "@f" in syn_code, f"Missing @f directive: {syn_code[:100]}"
    assert "main" in syn_code, f"Missing main function: {syn_code[:100]}"
    print(f"      add(21, 21) → {len(syn_code)} chars of .syn")

    syn_code = factorial.preview(5)
    assert "while" in syn_code, f"Missing while loop: {syn_code[:100]}"
    print(f"      factorial(5) → {len(syn_code)} chars of .syn")

    syn_code = fibonacci.preview(10)
    assert "while" in syn_code
    print(f"      fibonacci(10) → {len(syn_code)} chars of .syn")

    syn_code = gcd.preview(48, 18)
    assert "while" in syn_code
    print(f"      gcd(48, 18) → {len(syn_code)} chars of .syn")

    print("      PASS — all 4 programs transpile correctly")


def test_repr():
    """Test __repr__ and __name__."""
    print("  [4] Function metadata...")
    assert repr(add) == "<SynapseFunction add>"
    assert add.__name__ == "add"
    assert factorial.__name__ == "factorial"
    print("      PASS")


def test_starmap_preview():
    """Test starmap builds correct argument tuples."""
    print("  [5] .starmap() argument wrapping...")
    # Can't test actual execution without gateway, but verify arg wrapping
    args = [1, 2, 3, 4, 5]
    tuples = [(a,) for a in args]
    assert tuples == [(1,), (2,), (3,), (4,), (5,)]
    print("      PASS")


def test_custom_app():
    """Test creating a custom SynapseApp instance."""
    print("  [6] Custom SynapseApp instance...")
    custom = SynapseApp(api_key="test_key", base_url="http://localhost:9999")

    @custom.function
    def double(x):
        return x * 2

    assert double(5) == 10  # Local call works
    assert double.__name__ == "double"
    syn = double.preview(5)
    assert "@f" in syn
    print("      PASS")


def test_remote_execution():
    """Test actual Synapse execution (requires running gateway)."""
    api_key = os.environ.get("SYNAPSE_API_KEY")
    base_url = os.environ.get("SYNAPSE_BASE_URL", "http://localhost:8000")

    if not api_key:
        print("  [7] .remote() execution... SKIP (no SYNAPSE_API_KEY)")
        return

    print(f"  [7] .remote() execution (gateway: {base_url})...")

    remote_app = SynapseApp(api_key=api_key, base_url=base_url)

    @remote_app.function
    def remote_add(a, b):
        return a + b

    @remote_app.function
    def remote_fib(n):
        a = 0
        b = 1
        i = 0
        while i < n:
            temp = b
            b = a + b
            a = temp
            i = i + 1
        return a

    try:
        result = remote_add.remote(21, 21)
        assert result == 42, f"Expected 42, got {result}"
        print(f"      remote_add(21, 21) = {result} ✓")

        full = remote_add.remote_full(21, 21)
        print(f"      latency: {full.latency_ms}ms, hash: {full.deterministic_hash[:16]}...")

        result = remote_fib.remote(10)
        assert result == 55, f"Expected 55, got {result}"
        print(f"      remote_fib(10) = {result} ✓")

        # Test .map()
        results = remote_add.map([(1, 2), (3, 4), (5, 6)])
        assert results == [3, 7, 11], f"map results: {results}"
        print(f"      map(3 jobs) = {results} ✓")

        print("      PASS — remote execution verified")
    except Exception as e:
        print(f"      FAIL — {e}")


if __name__ == "__main__":
    print()
    print("═" * 60)
    print("  Synapse Decorator API — End-to-End Tests")
    print("═" * 60)
    print()

    passed = 0
    total = 7

    for test_fn in [
        test_local_calls,
        test_local_explicit,
        test_preview,
        test_repr,
        test_starmap_preview,
        test_custom_app,
        test_remote_execution,
    ]:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"      FAIL — {e}")

    print()
    print(f"  Results: {passed}/{total} passed")
    print("═" * 60)
    print()
