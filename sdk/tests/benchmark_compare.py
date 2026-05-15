#!/usr/bin/env python3
"""
Synapse Cell vs E2B — Head-to-Head Benchmark Suite

Runs identical workloads on both platforms and compares:
- Cold start latency
- Simple exec latency
- Multi-step persistent session
- HTTP fetch
- File I/O
- Math-heavy computation
- Concurrent execution

Usage:
    pip install e2b-code-interpreter
    export E2B_API_KEY="e2b_..."
    python3 benchmark_compare.py
"""
import os
import json
import time
import statistics
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Configuration ─────────────────────────────────────────────
E2B_API_KEY = os.environ.get("E2B_API_KEY", "")
CELL_API_KEY = os.environ.get("CELL_API_KEY", "test_key")
CELL_API_URL = os.environ.get("CELL_API_URL", "http://localhost:8002")
ITERATIONS = 5  # Per test
CONCURRENT_COUNT = 10

# ─── Load Synapse Cell SDK ─────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    'cell',
    os.path.join(os.path.dirname(__file__), '..', 'synapse', 'cell.py')
)
cell_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cell_mod)
Cell = cell_mod.Cell

# ─── E2B SDK (optional) ───────────────────────────────────────
try:
    from e2b_code_interpreter import Sandbox
    HAS_E2B = True
except ImportError:
    HAS_E2B = False
    print("⚠ e2b-code-interpreter not installed. Run: pip install e2b-code-interpreter")

# ─── Benchmark Functions ──────────────────────────────────────

def bench_cell_cold_start():
    """Measure cold start: create cell + first exec."""
    start = time.time()
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    cell.run("print('cold')")
    elapsed = (time.time() - start) * 1000
    cell.kill()
    return elapsed

def bench_e2b_cold_start():
    """Measure cold start: create sandbox + first exec."""
    start = time.time()
    sandbox = Sandbox.create()
    sandbox.run_code("print('cold')")
    elapsed = (time.time() - start) * 1000
    sandbox.kill()
    return elapsed

def bench_cell_simple_exec():
    """Simple exec: print(42)."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    result = cell.run("print(42)")
    cell.kill()
    return result.latency_ms

def bench_e2b_simple_exec():
    """Simple exec: print(42)."""
    sandbox = Sandbox.create()
    start = time.time()
    sandbox.run_code("print(42)")
    elapsed = (time.time() - start) * 1000
    sandbox.kill()
    return elapsed

def bench_cell_multistep():
    """5-step persistent session."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL)
    start = time.time()
    cell.run("import math")
    cell.run("class Vec:\n    def __init__(self, x, y): self.x, self.y = x, y\n    def mag(self): return math.sqrt(self.x**2 + self.y**2)")
    cell.run("v = Vec(3, 4)")
    cell.run("v.x = v.x * 2")
    cell.run("print(f'{v.mag():.4f}')")
    elapsed = (time.time() - start) * 1000
    cell.kill()
    return elapsed

def bench_e2b_multistep():
    """5-step persistent session."""
    sandbox = Sandbox.create()
    start = time.time()
    sandbox.run_code("import math")
    sandbox.run_code("class Vec:\n    def __init__(self, x, y): self.x, self.y = x, y\n    def mag(self): return math.sqrt(self.x**2 + self.y**2)")
    sandbox.run_code("v = Vec(3, 4)")
    sandbox.run_code("v.x = v.x * 2")
    sandbox.run_code("print(f'{v.mag():.4f}')")
    elapsed = (time.time() - start) * 1000
    sandbox.kill()
    return elapsed

def bench_cell_math():
    """Math-heavy: fibonacci + sorting."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    code = """
import random
def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

f = fib(1000)
data = [random.randint(0, 1000000) for _ in range(50000)]
data.sort()
print(f'fib(1000) digits: {len(str(f))}, sorted: {len(data)}')
"""
    result = cell.run(code)
    cell.kill()
    return result.latency_ms

def bench_e2b_math():
    """Math-heavy: fibonacci + sorting."""
    sandbox = Sandbox.create()
    code = """
import random
def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

f = fib(1000)
data = [random.randint(0, 1000000) for _ in range(50000)]
data.sort()
print(f'fib(1000) digits: {len(str(f))}, sorted: {len(data)}')
"""
    start = time.time()
    sandbox.run_code(code)
    elapsed = (time.time() - start) * 1000
    sandbox.kill()
    return elapsed

def bench_cell_file_io():
    """Write 100KB file, read back, hash."""
    cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
    code = """
import hashlib
data = 'x' * 100000
with open('/data/test.txt', 'w') as f:
    f.write(data)
with open('/data/test.txt', 'r') as f:
    content = f.read()
h = hashlib.sha256(content.encode()).hexdigest()[:16]
print(f'size={len(content)}, hash={h}')
"""
    result = cell.run(code)
    cell.kill()
    return result.latency_ms

def bench_e2b_file_io():
    """Write 100KB file, read back, hash."""
    sandbox = Sandbox.create()
    code = """
import hashlib
data = 'x' * 100000
with open('/tmp/test.txt', 'w') as f:
    f.write(data)
with open('/tmp/test.txt', 'r') as f:
    content = f.read()
h = hashlib.sha256(content.encode()).hexdigest()[:16]
print(f'size={len(content)}, hash={h}')
"""
    start = time.time()
    sandbox.run_code(code)
    elapsed = (time.time() - start) * 1000
    sandbox.kill()
    return elapsed

def bench_cell_concurrent():
    """10 concurrent executions."""
    start = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENT_COUNT) as pool:
        futures = []
        for i in range(CONCURRENT_COUNT):
            def task(idx=i):
                cell = Cell(api_key=CELL_API_KEY, api_url=CELL_API_URL, persistent=False)
                r = cell.run(f"print({idx} * {idx})")
                cell.kill()
                return r.latency_ms
            futures.append(pool.submit(task))
        [f.result() for f in as_completed(futures)]
    elapsed = (time.time() - start) * 1000
    return elapsed

def bench_e2b_concurrent():
    """10 concurrent executions."""
    start = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENT_COUNT) as pool:
        futures = []
        for i in range(CONCURRENT_COUNT):
            def task(idx=i):
                sandbox = Sandbox.create()
                sandbox.run_code(f"print({idx} * {idx})")
                sandbox.kill()
            futures.append(pool.submit(task))
        [f.result() for f in as_completed(futures)]
    elapsed = (time.time() - start) * 1000
    return elapsed

# ─── Runner ───────────────────────────────────────────────────

def run_benchmark(name, cell_fn, e2b_fn, iterations=ITERATIONS):
    """Run a benchmark N times and collect stats."""
    print(f"\n{'─'*60}")
    print(f"  {name}")
    print(f"{'─'*60}")
    
    # Cell benchmarks
    cell_times = []
    for i in range(iterations):
        try:
            t = cell_fn()
            cell_times.append(t)
            print(f"  Cell  [{i+1}/{iterations}]: {t:.0f}ms")
        except Exception as e:
            print(f"  Cell  [{i+1}/{iterations}]: ERROR - {e}")
    
    # E2B benchmarks
    e2b_times = []
    if HAS_E2B and E2B_API_KEY:
        for i in range(iterations):
            try:
                t = e2b_fn()
                e2b_times.append(t)
                print(f"  E2B   [{i+1}/{iterations}]: {t:.0f}ms")
            except Exception as e:
                print(f"  E2B   [{i+1}/{iterations}]: ERROR - {e}")
    else:
        print("  E2B   [skipped — no API key]")
    
    return {
        "name": name,
        "cell": {
            "p50": statistics.median(cell_times) if cell_times else None,
            "p95": sorted(cell_times)[int(len(cell_times)*0.95)] if cell_times else None,
            "min": min(cell_times) if cell_times else None,
            "max": max(cell_times) if cell_times else None,
            "avg": statistics.mean(cell_times) if cell_times else None,
            "raw": cell_times,
        },
        "e2b": {
            "p50": statistics.median(e2b_times) if e2b_times else None,
            "p95": sorted(e2b_times)[int(len(e2b_times)*0.95)] if e2b_times else None,
            "min": min(e2b_times) if e2b_times else None,
            "max": max(e2b_times) if e2b_times else None,
            "avg": statistics.mean(e2b_times) if e2b_times else None,
            "raw": e2b_times,
        },
    }

def main():
    print("=" * 60)
    print("  Synapse Cell vs E2B — Head-to-Head Benchmark")
    print(f"  Iterations: {ITERATIONS}")
    print(f"  Cell URL: {CELL_API_URL}")
    print(f"  E2B: {'enabled' if HAS_E2B and E2B_API_KEY else 'disabled'}")
    print("=" * 60)
    
    benchmarks = [
        ("Cold Start (create + first exec)", bench_cell_cold_start, bench_e2b_cold_start),
        ("Simple Exec (print(42))", bench_cell_simple_exec, bench_e2b_simple_exec),
        ("5-Step Persistent Session", bench_cell_multistep, bench_e2b_multistep),
        ("Math Heavy (fib + sort 50K)", bench_cell_math, bench_e2b_math),
        ("File I/O (100KB write/read/hash)", bench_cell_file_io, bench_e2b_file_io),
        (f"Concurrent ({CONCURRENT_COUNT} parallel)", bench_cell_concurrent, bench_e2b_concurrent),
    ]
    
    results = []
    for name, cell_fn, e2b_fn in benchmarks:
        r = run_benchmark(name, cell_fn, e2b_fn)
        results.append(r)
    
    # Print comparison table
    print(f"\n{'='*70}")
    print("  RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"{'Test':<35} {'Cell p50':>10} {'E2B p50':>10} {'Speedup':>10}")
    print(f"{'─'*35} {'─'*10} {'─'*10} {'─'*10}")
    
    for r in results:
        cell_p50 = f"{r['cell']['p50']:.0f}ms" if r['cell']['p50'] else "N/A"
        e2b_p50 = f"{r['e2b']['p50']:.0f}ms" if r['e2b']['p50'] else "N/A"
        if r['cell']['p50'] and r['e2b']['p50']:
            speedup = r['e2b']['p50'] / r['cell']['p50']
            speedup_str = f"{speedup:.1f}×"
        else:
            speedup_str = "—"
        print(f"{r['name']:<35} {cell_p50:>10} {e2b_p50:>10} {speedup_str:>10}")
    
    # Save results
    output_path = os.path.join(os.path.dirname(__file__), 'benchmark_results.json')
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")

if __name__ == "__main__":
    main()
