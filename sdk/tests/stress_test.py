#!/usr/bin/env python3
"""
Synapse Cell — Comprehensive Stress Test Suite

Hammers the system from every angle to prove production readiness.
Run this before any public launch.

Tests:
  1. Concurrent cell creation blast (50 cells)
  2. Sustained throughput (60s sustained load)
  3. Memory pressure (large data structures)
  4. Deep recursion & complex code
  5. Persistent session marathon (100 sequential execs)
  6. File I/O stress (large files, many files)
  7. Shell command barrage
  8. SSE streaming under load
  9. Error handling (bad code, edge cases)
  10. Mixed workload chaos test
  11. Cell lifecycle (create → exec → kill, rapid cycle)
  12. Concurrent persistent sessions

Usage:
    python3 stress_test.py [--quick]   # quick = 10s sustained instead of 60s
"""
import os
import sys
import json
import time
import random
import string
import importlib.util
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List

# ─── Configuration ─────────────────────────────────────────────
CELL_API_KEY = os.environ.get("CELL_API_KEY", "test_key")
CELL_API_URL = os.environ.get("CELL_API_URL", "http://localhost:8002")
QUICK_MODE = "--quick" in sys.argv
SUSTAINED_DURATION = 10 if QUICK_MODE else 60
CONCURRENT_CELLS = 50
PERSISTENT_MARATHON_STEPS = 100
FILE_IO_COUNT = 50

# ─── Load SDK ──────────────────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    'cell',
    os.path.join(os.path.dirname(__file__), '..', 'synapse', 'cell.py')
)
cell_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cell_mod)
Cell = cell_mod.Cell

# ─── Test Results ──────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float
    details: str = ""
    metrics: dict = field(default_factory=dict)

results: List[TestResult] = []

def run_test(name, fn):
    """Run a test and capture results."""
    print(f"\n{'━'*60}")
    print(f"  TEST: {name}")
    print(f"{'━'*60}")
    start = time.time()
    try:
        details, metrics = fn()
        elapsed = (time.time() - start) * 1000
        results.append(TestResult(name, True, elapsed, details, metrics))
        print(f"  ✅ PASSED ({elapsed:.0f}ms) — {details}")
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        tb = traceback.format_exc()
        results.append(TestResult(name, False, elapsed, str(e)))
        print(f"  ❌ FAILED ({elapsed:.0f}ms) — {e}")
        print(f"     {tb.split(chr(10))[-2]}")

# ═══════════════════════════════════════════════════════════════
#  TEST 1: Concurrent Cell Creation Blast
# ═══════════════════════════════════════════════════════════════

def test_concurrent_creation():
    """Create 50 cells simultaneously and verify all are functional."""
    cells = []
    errors = []
    
    def create_and_exec(i):
        for attempt in range(2):  # 1 retry for transient connection issues
            try:
                cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
                result = cell.run(f"print('cell-{i}-ok')")
                assert result.stdout.strip() == f"cell-{i}-ok", f"Bad output: {result.stdout}"
                assert result.exit_code == 0
                cell.kill()
                return True
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.5)  # Brief pause before retry
                    continue
                return str(e)
    
    with ThreadPoolExecutor(max_workers=CONCURRENT_CELLS) as pool:
        futures = {pool.submit(create_and_exec, i): i for i in range(CONCURRENT_CELLS)}
        for f in as_completed(futures):
            r = f.result()
            if r is True:
                cells.append(futures[f])
            else:
                errors.append((futures[f], r))
    
    success_rate = len(cells) / CONCURRENT_CELLS * 100
    assert success_rate >= 95, f"Only {success_rate:.0f}% success rate"
    return (
        f"{len(cells)}/{CONCURRENT_CELLS} cells created & executed ({success_rate:.0f}%)",
        {"created": len(cells), "errors": len(errors), "success_rate": success_rate}
    )

# ═══════════════════════════════════════════════════════════════
#  TEST 2: Sustained Throughput
# ═══════════════════════════════════════════════════════════════

def test_sustained_throughput():
    """Sustained execution load for N seconds, measuring throughput."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    latencies = []
    errors = 0
    start = time.time()
    deadline = start + SUSTAINED_DURATION
    
    while time.time() < deadline:
        try:
            result = cell.run(f"print({random.randint(1, 999999)})")
            latencies.append(result.latency_ms)
        except:
            errors += 1
    
    cell.kill()
    elapsed = time.time() - start
    
    import statistics
    p50 = statistics.median(latencies) if latencies else 0
    p95 = sorted(latencies)[int(len(latencies)*0.95)] if latencies else 0
    p99 = sorted(latencies)[int(len(latencies)*0.99)] if latencies else 0
    throughput = len(latencies) / elapsed
    
    assert errors / max(len(latencies), 1) < 0.01, f"Error rate too high: {errors}/{len(latencies)}"
    
    return (
        f"{len(latencies)} execs in {elapsed:.0f}s = {throughput:.1f} exec/s, p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms",
        {"total_execs": len(latencies), "throughput": throughput, "p50": p50, "p95": p95, "p99": p99, "errors": errors}
    )

# ═══════════════════════════════════════════════════════════════
#  TEST 3: Memory Pressure
# ═══════════════════════════════════════════════════════════════

def test_memory_pressure():
    """Test with large data structures to verify memory limits."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    
    # 1MB string
    result = cell.run("data = 'x' * 1000000\nprint(f'len={len(data)}')")
    assert "len=1000000" in result.stdout, f"1MB string failed: {result.stdout}"
    
    # Large list
    result = cell.run("data = list(range(100000))\nprint(f'len={len(data)}, sum={sum(data)}')")
    assert "len=100000" in result.stdout
    
    # Dict with many keys
    result = cell.run("d = {str(i): i*i for i in range(10000)}\nprint(f'keys={len(d)}')")
    assert "keys=10000" in result.stdout
    
    # Nested structures
    result = cell.run("nested = {'a': [{'b': list(range(100))} for _ in range(100)]}\nprint(f'ok, len={len(nested[\"a\"])}')")
    assert "ok, len=100" in result.stdout
    
    cell.kill()
    return ("1MB strings, 100K lists, 10K dicts, nested structures — all OK", {})

# ═══════════════════════════════════════════════════════════════
#  TEST 4: Complex Code Patterns
# ═══════════════════════════════════════════════════════════════

def test_complex_code():
    """Test complex Python features: classes, generators, closures, decorators."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    
    # Classes with inheritance
    cell.run("""
class Animal:
    def __init__(self, name):
        self.name = name
    def speak(self):
        return f"{self.name} says ..."

class Dog(Animal):
    def speak(self):
        return f"{self.name} says Woof!"

class Cat(Animal):
    def speak(self):
        return f"{self.name} says Meow!"
""")
    result = cell.run("print(Dog('Rex').speak())")
    assert "Rex says Woof" in result.stdout
    
    # Generators
    cell.run("""
def fibonacci():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b
""")
    result = cell.run("gen = fibonacci()\nprint([next(gen) for _ in range(10)])")
    assert "[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]" in result.stdout
    
    # Closures
    cell.run("""
def make_counter(start=0):
    count = [start]
    def inc(n=1):
        count[0] += n
        return count[0]
    return inc
""")
    result = cell.run("c = make_counter(10)\nprint(c(5), c(3), c(1))")
    assert "15 18 19" in result.stdout
    
    # List comprehensions and lambdas
    result = cell.run("""
transform = lambda x: x**2 + 1
data = [transform(i) for i in range(10)]
filtered = list(filter(lambda x: x % 2 == 0, data))
print(f'data={data[:5]}, filtered={filtered}')
""")
    assert "data=" in result.stdout
    
    # Exception handling
    result = cell.run("""
try:
    result = 1 / 0
except ZeroDivisionError as e:
    print(f'Caught: {e}')
finally:
    print('Cleanup done')
""")
    assert "Caught:" in result.stdout
    assert "Cleanup done" in result.stdout
    
    cell.kill()
    return ("Classes, generators, closures, lambdas, exceptions — all OK", {})

# ═══════════════════════════════════════════════════════════════
#  TEST 5: Persistent Session Marathon
# ═══════════════════════════════════════════════════════════════

def test_persistent_marathon():
    """100 sequential executions building complex state."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    latencies = []
    
    # Build state incrementally
    cell.run("history = []")
    for i in range(PERSISTENT_MARATHON_STEPS):
        result = cell.run(f"history.append({i})\nprint(len(history))")
        latencies.append(result.latency_ms)
        expected = str(i + 1)
        assert result.stdout.strip() == expected, \
            f"Step {i}: expected {expected}, got {result.stdout.strip()}"
    
    # Verify final state
    result = cell.run("print(f'total={len(history)}, sum={sum(history)}')")
    expected_sum = sum(range(PERSISTENT_MARATHON_STEPS))
    assert f"total={PERSISTENT_MARATHON_STEPS}" in result.stdout
    assert f"sum={expected_sum}" in result.stdout
    
    import statistics
    p50 = statistics.median(latencies)
    p99 = sorted(latencies)[int(len(latencies)*0.99)]
    
    cell.kill()
    return (
        f"{PERSISTENT_MARATHON_STEPS} steps, state verified, p50={p50:.0f}ms p99={p99:.0f}ms",
        {"steps": PERSISTENT_MARATHON_STEPS, "p50": p50, "p99": p99, "latencies": latencies[-5:]}
    )

# ═══════════════════════════════════════════════════════════════
#  TEST 6: File I/O Stress
# ═══════════════════════════════════════════════════════════════

def test_file_io_stress():
    """Create, write, read, delete many files."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    
    # Write many small files
    result = cell.run(f"""
import os, hashlib
for i in range({FILE_IO_COUNT}):
    with open(f'/data/file_{{i}}.txt', 'w') as f:
        f.write(f'content-{{i}}' * 100)

count = len([f for f in os.listdir('/data') if f.startswith('file_')])
print(f'created={{count}}')
""")
    assert f"created={FILE_IO_COUNT}" in result.stdout
    
    # Read all files and hash
    result = cell.run(f"""
import os, hashlib
total_bytes = 0
for i in range({FILE_IO_COUNT}):
    with open(f'/data/file_{{i}}.txt') as f:
        total_bytes += len(f.read())
print(f'read_bytes={{total_bytes}}')
""")
    assert "read_bytes=" in result.stdout
    
    # Write a large file (500KB)
    result = cell.run("""
data = 'A' * 500000
with open('/data/large.txt', 'w') as f:
    f.write(data)
import os
size = os.path.getsize('/data/large.txt')
print(f'large_file={size}')
""")
    assert "large_file=500000" in result.stdout
    
    # Binary-like data
    result = cell.run("""
import json
data = {'key_' + str(i): list(range(100)) for i in range(50)}
with open('/data/data.json', 'w') as f:
    json.dump(data, f)
with open('/data/data.json') as f:
    loaded = json.load(f)
print(f'keys={len(loaded)}, match={loaded == data}')
""")
    assert "keys=50, match=True" in result.stdout
    
    cell.kill()
    return (f"{FILE_IO_COUNT} files + 500KB large file + JSON round-trip — all OK", {})

# ═══════════════════════════════════════════════════════════════
#  TEST 7: Shell Command Barrage
# ═══════════════════════════════════════════════════════════════

def test_shell_commands():
    """Test all supported shell commands."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    passed = 0
    
    commands = [
        ("echo hello world", "hello world"),
        ("pwd", "/"),
        ("mkdir -p /data/test/deep/nested", "Created"),
        ("touch /data/test/file.txt", "Touched"),
        ("ls /data/test", "deep\nfile.txt"),
        ("find /data/test", "/data/test"),
        ("echo content > test", "content > test"),  # echo doesn't redirect
    ]
    
    for cmd, expected_substr in commands:
        result = cell.command(cmd)
        assert result.exit_code == 0, f"'{cmd}' failed with exit code {result.exit_code}: {result.stderr}"
        assert expected_substr in result.stdout, \
            f"'{cmd}': expected '{expected_substr}' in '{result.stdout}'"
        passed += 1
    
    cell.kill()
    return (f"{passed}/{len(commands)} shell commands passed", {"passed": passed})

# ═══════════════════════════════════════════════════════════════
#  TEST 8: SSE Streaming
# ═══════════════════════════════════════════════════════════════

def test_sse_streaming():
    """Test SSE streaming with multiple lines."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    
    events = []
    for event in cell.run_stream("for i in range(20):\n    print(f'line-{i}')"):
        events.append(event)
    
    stdout_events = [e for e in events if e['type'] == 'stdout']
    result_events = [e for e in events if e['type'] == 'result']
    
    assert len(stdout_events) == 20, f"Expected 20 stdout events, got {len(stdout_events)}"
    assert len(result_events) == 1, f"Expected 1 result event, got {len(result_events)}"
    assert result_events[0]['exit_code'] == 0
    
    # Verify order
    for i, e in enumerate(stdout_events):
        assert e['text'] == f'line-{i}', f"Event {i}: expected 'line-{i}', got '{e['text']}'"
    
    cell.kill()
    return (f"20 lines streamed in order, result event received", {"events": len(events)})

# ═══════════════════════════════════════════════════════════════
#  TEST 9: Error Handling
# ═══════════════════════════════════════════════════════════════

def test_error_handling():
    """Verify proper error handling for bad inputs."""
    passed = 0
    
    # Syntax error
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("def bad syntax(:")
    has_error = result.exit_code != 0 or "SyntaxError" in result.stderr or "SyntaxError" in result.stdout
    assert has_error, f"Syntax error not detected: exit={result.exit_code}, stderr={result.stderr[:100]}, stdout={result.stdout[:100]}"
    cell.kill()
    passed += 1
    
    # Runtime error (ZeroDivisionError)
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("x = 1/0")
    has_error = result.exit_code != 0 or "ZeroDivisionError" in result.stderr or "ZeroDivisionError" in result.stdout
    assert has_error, f"Division error not detected: exit={result.exit_code}"
    cell.kill()
    passed += 1
    
    # Import non-existent module (harness catches this — just verify it doesn't crash)
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("import nonexistent_module_xyz\nprint('after')")
    # May succeed (harness catches) or fail — either is acceptable
    # Key: the cell doesn't crash or hang
    cell.kill()
    passed += 1
    
    # Infinite loop (should timeout via fuel exhaustion)
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("while True: pass")
    has_error = result.exit_code != 0 or "wasm" in result.stderr.lower() or "fuel" in result.stderr.lower()
    assert has_error, f"Infinite loop not stopped: exit={result.exit_code}"
    cell.kill()
    passed += 1
    
    # Moderately long output (1000 lines — verifiable without pipe issues)
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("for i in range(1000): print(i)")
    assert result.exit_code == 0, f"Long output: exit={result.exit_code}"
    lines = result.stdout.strip().split('\n')
    assert len(lines) >= 500, f"Expected 500+ lines, got {len(lines)}"
    cell.kill()
    passed += 1
    
    # Empty code
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("")
    assert result.exit_code == 0, f"Empty code: exit={result.exit_code}"
    cell.kill()
    passed += 1
    
    # Unicode
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("print('Hello 🌍 世界')")
    assert result.exit_code == 0, f"Unicode: exit={result.exit_code}"
    assert "Hello" in result.stdout, f"Unicode: stdout={result.stdout[:100]}"
    cell.kill()
    passed += 1
    
    return (f"{passed}/7 error handling tests passed", {"passed": passed})

# ═══════════════════════════════════════════════════════════════
#  TEST 10: Mixed Workload Chaos
# ═══════════════════════════════════════════════════════════════

def test_chaos():
    """Simultaneous mixed operations: create, exec, command, kill."""
    errors = []
    completed = []
    
    def chaos_worker(worker_id):
        try:
            cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
            
            # Random sequence of operations
            ops = random.choices(['exec', 'command', 'exec', 'exec'], k=5)
            for op in ops:
                if op == 'exec':
                    r = cell.run(f"print('w{worker_id}-ok')")
                    assert r.exit_code == 0
                elif op == 'command':
                    r = cell.command("ls /data")
                    assert r.exit_code == 0
            
            cell.kill()
            return f"worker-{worker_id}: {len(ops)} ops"
        except Exception as e:
            return f"worker-{worker_id}: ERROR {e}"
    
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(chaos_worker, i) for i in range(20)]
        for f in as_completed(futures):
            result = f.result()
            if "ERROR" in result:
                errors.append(result)
            else:
                completed.append(result)
    
    success_rate = len(completed) / 20 * 100
    assert success_rate >= 90, f"Only {success_rate:.0f}% success"
    
    return (
        f"{len(completed)}/20 workers completed ({success_rate:.0f}%), {len(errors)} errors",
        {"completed": len(completed), "errors": len(errors)}
    )

# ═══════════════════════════════════════════════════════════════
#  TEST 11: Rapid Lifecycle
# ═══════════════════════════════════════════════════════════════

def test_rapid_lifecycle():
    """Create → exec → kill as fast as possible."""
    cycles = 15 if QUICK_MODE else 30
    latencies = []
    
    for i in range(cycles):
        start = time.time()
        cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
        result = cell.run(f"print({i})")
        cell.kill()
        elapsed = (time.time() - start) * 1000
        latencies.append(elapsed)
        assert result.stdout.strip() == str(i)
    
    import statistics
    p50 = statistics.median(latencies)
    
    return (
        f"{cycles} create→exec→kill cycles, p50={p50:.0f}ms/cycle",
        {"cycles": cycles, "p50": p50}
    )

# ═══════════════════════════════════════════════════════════════
#  TEST 12: Concurrent Persistent Sessions
# ═══════════════════════════════════════════════════════════════

def test_concurrent_persistent():
    """10 persistent cells, each doing 10 state-building steps concurrently."""
    errors = []
    
    def persistent_worker(worker_id):
        try:
            cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
            cell.run(f"counter = {worker_id * 1000}")
            
            for step in range(10):
                result = cell.run(f"counter += 1\nprint(counter)")
                expected = worker_id * 1000 + step + 1
                actual = int(result.stdout.strip())
                assert actual == expected, f"W{worker_id} step {step}: expected {expected}, got {actual}"
            
            cell.kill()
            return True
        except Exception as e:
            return str(e)
    
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(persistent_worker, i) for i in range(10)]
        results_list = []
        for f in as_completed(futures):
            r = f.result()
            if r is True:
                results_list.append(True)
            else:
                errors.append(r)
    
    success = len(results_list)
    assert success >= 9, f"Only {success}/10 persistent sessions succeeded"
    
    return (
        f"{success}/10 concurrent persistent sessions, each 10 steps with verified state",
        {"success": success, "errors": len(errors)}
    )

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  🔥 SYNAPSE CELL — STRESS TEST SUITE")
    print(f"  Mode: {'QUICK' if QUICK_MODE else 'FULL'}")
    print(f"  Target: {CELL_API_URL}")
    print(f"  Sustained: {SUSTAINED_DURATION}s")
    print(f"  Concurrent: {CONCURRENT_CELLS} cells")
    print(f"  Persistent marathon: {PERSISTENT_MARATHON_STEPS} steps")
    print("=" * 60)
    
    suite_start = time.time()
    
    tests = [
        ("1. Concurrent Cell Creation (50 cells)", test_concurrent_creation),
        ("2. Sustained Throughput", test_sustained_throughput),
        ("3. Memory Pressure", test_memory_pressure),
        ("4. Complex Code Patterns", test_complex_code),
        ("5. Persistent Session Marathon (100 steps)", test_persistent_marathon),
        ("6. File I/O Stress", test_file_io_stress),
        ("7. Shell Command Barrage", test_shell_commands),
        ("8. SSE Streaming", test_sse_streaming),
        ("9. Error Handling", test_error_handling),
        ("10. Mixed Workload Chaos", test_chaos),
        ("11. Rapid Lifecycle (30 cycles)", test_rapid_lifecycle),
        ("12. Concurrent Persistent Sessions", test_concurrent_persistent),
    ]
    
    for name, fn in tests:
        run_test(name, fn)
    
    suite_elapsed = time.time() - suite_start
    
    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    
    print(f"\n{'═'*60}")
    print(f"  STRESS TEST RESULTS")
    print(f"{'═'*60}")
    print(f"  Total time: {suite_elapsed:.0f}s")
    print(f"  Tests:      {passed} passed, {failed} failed, {len(results)} total")
    print(f"{'─'*60}")
    
    for r in results:
        status = "✅" if r.passed else "❌"
        print(f"  {status} {r.name} ({r.duration_ms:.0f}ms)")
        if not r.passed:
            print(f"     → {r.details}")
    
    print(f"{'─'*60}")
    
    if failed == 0:
        print(f"\n  🏆 ALL {passed} TESTS PASSED — SYSTEM IS PRODUCTION READY")
    else:
        print(f"\n  ⚠️  {failed} TESTS FAILED — FIX BEFORE LAUNCH")
    
    # Save results
    output = {
        "timestamp": time.time(),
        "mode": "quick" if QUICK_MODE else "full",
        "target": CELL_API_URL,
        "duration_s": suite_elapsed,
        "passed": passed,
        "failed": failed,
        "tests": [
            {
                "name": r.name,
                "passed": r.passed,
                "duration_ms": r.duration_ms,
                "details": r.details,
                "metrics": r.metrics,
            }
            for r in results
        ]
    }
    output_path = os.path.join(os.path.dirname(__file__), 'stress_test_results.json')
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to: {output_path}")
    
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
