#!/usr/bin/env python3
"""
synapse_eval — In-process .syn execution via wasmtime (no HTTP overhead).

Two execution modes:
  execute()      — Safe mode. Fresh Store per eval. ~0.2ms/eval.
  execute_fast() — Turbo mode. Reuses cached instances. ~0.007ms/eval.

Usage:
    from sdk.synapse_eval import SynapseEvaluator

    evaluator = SynapseEvaluator()

    # Safe mode (isolated per eval):
    result = evaluator.execute("@f 0 main [ + 21 21 ]")

    # Turbo mode (152K evals/sec):
    result = evaluator.execute_fast("@f 0 main [ + 21 21 ]")
"""

import hashlib
import sys
import os
import time

# Add project root to path for imports
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class SynapseEvaluator:
    """In-process .syn evaluator using wasmtime-py.

    Eliminates HTTP overhead by running the full compile+execute pipeline
    in the same Python process. Two modes:

    - execute():      Safe. Fresh Store per eval. ~0.2ms/eval.
    - execute_fast(): Turbo. Reuses instance. ~0.007ms/eval (150K+ evals/sec).
    """

    def __init__(self, fuel_limit=10_000_000):
        import wasmtime

        config = wasmtime.Config()
        config.consume_fuel = True
        self.engine = wasmtime.Engine(config)

        # Caches
        self._module_cache = {}           # wasm_hash → Module
        self._instance_cache = {}         # source_hash → (store, main_fn)
        self._fuel_limit = fuel_limit
        self._fuel_refill = fuel_limit * 1000  # Enough for 1000 calls per instance

        # Import compiler
        sys.path.insert(0, os.path.join(_project_root, 'tools'))
        from sync import compile_syn_source
        self._compile = compile_syn_source

        # wasmtime module ref (avoid re-importing)
        self._wt = wasmtime

    def _get_module(self, syn_source):
        """Compile .syn → Module with caching. Returns (module, wasm_hash, compile_ms, error)."""
        t0 = time.perf_counter()
        compile_result = self._compile(syn_source)
        compile_ms = (time.perf_counter() - t0) * 1000

        if 'error' in compile_result:
            return None, None, compile_ms, str(compile_result['error'])

        wasm_bytes = bytes(compile_result['wasm'])
        wasm_hash = hashlib.sha256(wasm_bytes).hexdigest()

        if wasm_hash not in self._module_cache:
            try:
                self._module_cache[wasm_hash] = self._wt.Module(self.engine, wasm_bytes)
            except Exception as e:
                return None, None, compile_ms, f'wasm_compile: {e}'

        return self._module_cache[wasm_hash], wasm_hash, compile_ms, None

    def _make_instance(self, module, fuel=None):
        """Create a fresh Store + Linker + Instance for a module."""
        wt = self._wt
        store = wt.Store(self.engine)
        store.set_fuel(fuel or self._fuel_limit)

        linker = wt.Linker(self.engine)
        for imp in module.imports:
            ft = imp.type
            if list(ft.results):
                linker.define(store, imp.module, imp.name,
                              wt.Func(store, ft, lambda *a: -1))
            else:
                linker.define(store, imp.module, imp.name,
                              wt.Func(store, ft, lambda *a: None))

        instance = linker.instantiate(store, module)
        main_fn = instance.exports(store)["main"]
        return store, main_fn

    def execute(self, syn_source):
        """Safe mode: fresh Store per eval. Isolated. ~0.2ms/eval."""
        module, wasm_hash, compile_ms, error = self._get_module(syn_source)
        if error:
            return {'result': None, 'stdout': '', 'exec_ms': 0,
                    'compile_ms': compile_ms, 'error': error}

        t_exec = time.perf_counter()
        try:
            store, main_fn = self._make_instance(module)
            result = main_fn(store)
            exec_ms = (time.perf_counter() - t_exec) * 1000
            return {'result': result, 'stdout': '', 'exec_ms': exec_ms,
                    'compile_ms': compile_ms, 'error': None}
        except Exception as e:
            exec_ms = (time.perf_counter() - t_exec) * 1000
            return {'result': None, 'stdout': '', 'exec_ms': exec_ms,
                    'compile_ms': compile_ms, 'error': str(e)}

    def execute_fast(self, syn_source):
        """Turbo mode: reuses cached instance. ~0.007ms/eval (150K+ evals/sec).

        For pure .syn programs (no side effects), the instance is created once
        and main() is called repeatedly on the same Store. This eliminates
        Store/Linker/Instance creation overhead entirely.

        Safe for GRPO reward eval where programs are deterministic.
        """
        source_hash = hashlib.sha256(syn_source.encode()).hexdigest()

        if source_hash in self._instance_cache:
            store, main_fn = self._instance_cache[source_hash]
            try:
                result = main_fn(store)
                return {'result': result, 'stdout': '', 'exec_ms': 0,
                        'compile_ms': 0, 'error': None, 'cached': True}
            except Exception:
                # Instance exhausted fuel or trapped — recreate
                del self._instance_cache[source_hash]

        # First call or cache miss — compile + create instance
        module, wasm_hash, compile_ms, error = self._get_module(syn_source)
        if error:
            return {'result': None, 'stdout': '', 'exec_ms': 0,
                    'compile_ms': compile_ms, 'error': error}

        t_exec = time.perf_counter()
        try:
            store, main_fn = self._make_instance(module, fuel=self._fuel_refill)
            result = main_fn(store)
            exec_ms = (time.perf_counter() - t_exec) * 1000

            # Cache the instance for future calls
            self._instance_cache[source_hash] = (store, main_fn)

            return {'result': result, 'stdout': '', 'exec_ms': exec_ms,
                    'compile_ms': compile_ms, 'error': None, 'cached': False}
        except Exception as e:
            exec_ms = (time.perf_counter() - t_exec) * 1000
            return {'result': None, 'stdout': '', 'exec_ms': exec_ms,
                    'compile_ms': compile_ms, 'error': str(e)}

    def execute_batch(self, sources):
        """Execute multiple .syn programs using turbo mode. Returns list of results."""
        return [self.execute_fast(s) for s in sources]

    def cache_stats(self):
        return {
            'cached_modules': len(self._module_cache),
            'cached_instances': len(self._instance_cache),
        }

    def clear_instance_cache(self):
        """Clear cached instances (e.g., between training epochs)."""
        self._instance_cache.clear()


# ════════════════════════════════════════════════════════════════════════
#  Comprehensive Self-Test & Benchmark
# ════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import subprocess

    print()
    print("═" * 70)
    print("  Synapse FFI Evaluator — Comprehensive Test & Benchmark")
    print("═" * 70)
    print()

    evaluator = SynapseEvaluator()

    # ── Correctness Tests ──
    print("  ── Correctness Tests ──")
    tests = [
        ("@f 0 main [ + 21 21 ]", 42, "addition"),
        ("@f 0 main [ * 10 10 ]", 100, "multiplication"),
        ("@f 0 main [ - 100 58 ]", 42, "subtraction"),
        ("@f 0 main [ / 144 12 ]", 12, "division"),
        ("@f 0 main [ % 1000 7 ]", 6, "modulo"),
        ("@f 0 main [ let $r 0 let $i 1 while <= $i 10 [ set $r + $r $i set $i + $i 1 ] $r ]", 55, "sum_1_to_10"),
        ("@f 0 main [ let $r 1 let $i 1 while <= $i 10 [ set $r * $r $i set $i + $i 1 ] $r ]", 3628800, "factorial_10"),
        ("@f 0 main [ let $a 0 let $b 1 let $i 0 while < $i 18 [ let $c + $a $b set $a $b set $b $c set $i + $i 1 ] $b ]", 4181, "fibonacci_20"),
        ("@f 0 main [ let $r 0 let $i 1 while <= $i 10 [ set $r + $r * $i $i set $i + $i 1 ] $r ]", 385, "sum_squares"),
        ("@f 0 main [ if > 73 42 [ 73 ] [ 42 ] ]", 73, "max"),
        ("@f 0 main [ if < 73 42 [ 73 ] [ 42 ] ]", 42, "min"),
        ("@f 0 main [ let $r 1 let $i 0 while < $i 10 [ set $r * $r 2 set $i + $i 1 ] $r ]", 1024, "pow_2_10"),
    ]

    all_pass = True
    for code, expected, name in tests:
        # Test both modes
        r_safe = evaluator.execute(code)
        r_fast = evaluator.execute_fast(code)

        safe_ok = r_safe['result'] == expected
        fast_ok = r_fast['result'] == expected

        status = "✅" if (safe_ok and fast_ok) else "❌"
        if not (safe_ok and fast_ok):
            all_pass = False
            print(f"  {status} {name}: expected={expected}, safe={r_safe['result']}, fast={r_fast['result']}")
        else:
            print(f"  {status} {name} = {expected}")

    # Test fast mode cache hit (second call)
    r2 = evaluator.execute_fast("@f 0 main [ + 21 21 ]")
    assert r2['result'] == 42 and r2.get('cached'), f"Cache hit failed: {r2}"
    print(f"  ✅ cache_hit (cached={r2.get('cached')})")

    print()
    if not all_pass:
        print("  ❌ SOME TESTS FAILED")
        sys.exit(1)

    # ── Benchmark: Safe vs Turbo ──
    print("  ── Benchmark: Safe Mode vs Turbo Mode ──")
    N = 10000
    source = "@f 0 main [ + 21 21 ]"

    # Warmup
    evaluator.execute(source)
    evaluator.execute_fast(source)

    # Safe mode
    t0 = time.perf_counter()
    for _ in range(N):
        evaluator.execute(source)
    safe_ms = (time.perf_counter() - t0) * 1000
    safe_per = safe_ms / N

    # Turbo mode
    t0 = time.perf_counter()
    for _ in range(N):
        evaluator.execute_fast(source)
    fast_ms = (time.perf_counter() - t0) * 1000
    fast_per = fast_ms / N

    # Subprocess baseline
    t0 = time.perf_counter()
    for _ in range(min(N, 200)):
        subprocess.run(["python3", "-c", "result = 21 + 21\nprint(result)"],
                       capture_output=True, text=True, timeout=5)
    sub_ms = (time.perf_counter() - t0) * 1000 / min(N, 200)

    print()
    print(f"  {'Method':<30} {'Per Eval':>12} {'Evals/sec':>12} {'vs Sub':>10}")
    print(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*10}")
    print(f"  {'Subprocess (python3 -c)':<30} {sub_ms:.4f}ms   {1000/sub_ms:>10,.0f}   1x")
    print(f"  {'FFI Safe (execute)':<30} {safe_per:.4f}ms   {1000/safe_per:>10,.0f}   {sub_ms/safe_per:,.0f}x")
    print(f"  {'FFI Turbo (execute_fast)':<30} {fast_per:.4f}ms   {1000/fast_per:>10,.0f}   {sub_ms/fast_per:,.0f}x")
    print()

    # ── Benchmark: Multiple distinct programs (turbo) ──
    print("  ── Benchmark: 10 distinct programs × 1000 evals each (Turbo) ──")
    all_sources = [code for code, _, _ in tests]

    # Warmup all
    for s in all_sources:
        evaluator.execute_fast(s)

    t0 = time.perf_counter()
    correct = 0
    total = 0
    for _ in range(1000):
        for i, (code, expected, _) in enumerate(tests):
            r = evaluator.execute_fast(code)
            if r['result'] == expected:
                correct += 1
            total += 1
    multi_ms = (time.perf_counter() - t0) * 1000
    multi_per = multi_ms / total

    print(f"  {total} evals in {multi_ms:.1f}ms ({multi_per:.4f}ms/eval)")
    print(f"  {1000/multi_per:,.0f} evals/sec | {correct}/{total} correct")
    print()

    # ── Production scale extrapolation ──
    print("  ── Production Scale Extrapolation ──")
    scales = [
        ("Our benchmark (192 evals)", 192),
        ("Standard GRPO (1.6M evals)", 1_600_000),
        ("DeepSeek-R1 (6.4M evals)", 6_400_000),
        ("Training Synapse model (64M evals)", 64_000_000),
    ]
    for label, n in scales:
        sub_time = n * sub_ms / 1000
        fast_time = n * fast_per / 1000
        if sub_time > 3600:
            sub_str = f"{sub_time/3600:.1f} hrs"
        elif sub_time > 60:
            sub_str = f"{sub_time/60:.1f} min"
        else:
            sub_str = f"{sub_time:.1f}s"
        if fast_time > 60:
            fast_str = f"{fast_time/60:.1f} min"
        else:
            fast_str = f"{fast_time:.1f}s"
        print(f"  {label:<40} subprocess: {sub_str:>10}  turbo: {fast_str:>10}")

    print()
    print(f"  Cache: {evaluator.cache_stats()}")
    print()
    print("  ═══════════════════════════════════════════════════════")
    print(f"  ✅ ALL TESTS PASSED — Turbo mode: {sub_ms/fast_per:,.0f}× faster than subprocess")
    print("  ═══════════════════════════════════════════════════════")
