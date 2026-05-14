#!/usr/bin/env python3
"""
Cell vs E2B — canonical apples-to-apples benchmark harness.

Runs three scenarios (simple_eval, math_heavy, file_io) N times against
Synapse Cell (PyO3 local mode by default) and optionally against E2B
(via the `e2b-code-interpreter` SDK). Computes p50/p95/p99/mean/std/etc.
per scenario per cache state, plus a Mann-Whitney U effect size when both
are present. Appends one JSONL row to cell/benchmarks/RESULTS.jsonl.

Methodology: see cell/benchmarks/METHODOLOGY.md
Hardware profile: see cell/benchmarks/HARDWARE_PROFILE.md
Skill that automates the publish workflow: .claude/skills/cell-benchmark-e2b/

USAGE:
    # Cell-only run (no E2B comparison)
    python3 cell/benchmarks/e2b_apples_to_apples.py --runs 100 --warmup 10 --skip-e2b

    # Full Cell-vs-E2B run (requires E2B_API_KEY in env)
    export E2B_API_KEY=<key>
    python3 cell/benchmarks/e2b_apples_to_apples.py --runs 100 --warmup 10

    # Single scenario (faster iteration)
    python3 cell/benchmarks/e2b_apples_to_apples.py --scenario simple_eval --runs 50

    # Custom output path
    python3 cell/benchmarks/e2b_apples_to_apples.py --output /tmp/cell_bench.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

# Wire up the SDK path (works whether we're in .venv-cell or system Python)
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "cell" / "sdk"))

# ----- Scenario definitions (must match METHODOLOGY.md exactly) ---------------

SCENARIOS: dict[str, dict] = {
    "simple_eval": {
        "code": "print(2 + 2)",
        "expected_stdout_contains": "4",
        "description": "End-to-end latency of the minimum AI-agent code call. "
                       "Headline number — maps to perceived agent reasoning lag.",
    },
    "math_heavy": {
        # Plain for-loop kept inside the .syn safe-subset so we don't trigger
        # the CPython-WASI fallback path (which currently has a sys.path issue
        # for the transpiler subprocess on Mac).  Same arithmetic, lower
        # surface-area for the transpiler.
        "code": (
            "total = 0\n"
            "for i in range(10000):\n"
            "    total = total + i * i\n"
            "print(total)"
        ),
        "expected_stdout_contains": "333283335000",
        "description": "CPU-bound execution inside the sandbox. "
                       "Tests Wasm JIT quality on tight inner loops.",
    },
    # file_io is deferred from the default scenario set: Cell's sandbox preopens
    # /data/ as the writable mount, while E2B uses /tmp/. An apples-to-apples
    # file_io test needs per-runtime path resolution which is non-trivial. Until
    # then, run with --scenario file_io explicitly to opt in (will likely error
    # against Cell unless you adapt the path). Tracked for follow-up in JC-003.
    "file_io": {
        "code": (
            "data = b'x' * 100_000\n"
            "with open('/data/cell_bench.bin', 'wb') as f:\n"
            "    f.write(data)\n"
            "with open('/data/cell_bench.bin', 'rb') as f:\n"
            "    n = len(f.read())\n"
            "print(n)"
        ),
        "expected_stdout_contains": "100000",
        "description": "[experimental] FFI cost for sandboxed filesystem operations. "
                       "Cell uses /data/ (preopened); E2B uses /tmp/. Run with "
                       "--scenario file_io and expect path-mismatch errors until "
                       "the harness gets per-runtime path resolution.",
    },
}


@dataclass
class ScenarioStats:
    runs: int
    warmup_discarded: int
    timings_ms: list[float] = field(default_factory=list)
    errors: int = 0

    def percentile(self, p: float) -> float:
        if not self.timings_ms:
            return float("nan")
        sorted_t = sorted(self.timings_ms)
        idx = int(p * len(sorted_t) / 100)
        return sorted_t[min(idx, len(sorted_t) - 1)]

    def summary(self) -> dict:
        if not self.timings_ms:
            return {"runs": self.runs, "errors": self.errors, "p50_ms": None}
        return {
            "runs": self.runs,
            "errors": self.errors,
            "warmup_discarded": self.warmup_discarded,
            "p50_ms": round(self.percentile(50), 4),
            "p95_ms": round(self.percentile(95), 4),
            "p99_ms": round(self.percentile(99), 4),
            "mean_ms": round(statistics.mean(self.timings_ms), 4),
            "stdev_ms": round(
                statistics.stdev(self.timings_ms) if len(self.timings_ms) > 1 else 0.0, 4
            ),
            "min_ms": round(min(self.timings_ms), 4),
            "max_ms": round(max(self.timings_ms), 4),
        }


# ----- Hardware fingerprint ---------------------------------------------------

def hardware_fingerprint() -> dict:
    """Best-effort cross-platform hardware ID for RESULTS.jsonl rows."""
    fp = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
    }
    try:
        # macOS
        fp["cpu"] = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
    except Exception:
        try:
            # Linux
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        fp["cpu"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            fp["cpu"] = "unknown"
    return fp


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except Exception:
        return "unknown"


def hardware_profile_id(fp: dict) -> str:
    """Map hardware fingerprint to a HARDWARE_PROFILE.md profile ID."""
    hostname = fp.get("hostname", "")
    cpu = fp.get("cpu", "")
    if "Apple" in cpu and "Mac" in hostname:
        return "mike-mac"
    if "AMD Ryzen" in cpu and "7950X3D" in cpu:
        return "ax102-helsinki"  # or ax102-ashburn — both same CPU
    return f"unknown ({hostname})"


# ----- Cell runner (PyO3 local mode) ------------------------------------------

class CellRunner:
    """Cell via the PyO3 native local backend (api_url='local')."""

    def __init__(self):
        try:
            from synapse.cell import Cell  # noqa
            self._Cell = Cell
        except ImportError as e:
            raise RuntimeError(
                f"Cell SDK import failed: {e}\n"
                "Run: cd cell/gateway && maturin develop --release"
            )

    def __enter__(self):
        # Construct a single cell that we reuse across iterations (warm-cache test)
        self._cell = self._Cell(api_url="local", template="python3")
        return self

    def __exit__(self, *_):
        try:
            self._cell.kill()
        except Exception:
            pass

    def run(self, code: str) -> tuple[str, float]:
        """Run code, return (stdout, elapsed_ms)."""
        t0 = time.perf_counter_ns()
        result = self._cell.run(code)
        elapsed_ns = time.perf_counter_ns() - t0
        return result.stdout or "", elapsed_ns / 1_000_000


# ----- E2B runner (optional) --------------------------------------------------

class E2BRunner:
    """E2B via the e2b-code-interpreter SDK. Skipped if no E2B_API_KEY."""

    def __init__(self):
        api_key = os.environ.get("E2B_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("E2B_API_KEY env var is empty; skipping E2B comparison")
        try:
            from e2b_code_interpreter import Sandbox  # type: ignore
            self._Sandbox = Sandbox
        except ImportError as e:
            raise RuntimeError(
                f"e2b-code-interpreter not installed: {e}\n"
                "Run: pip install e2b-code-interpreter"
            )

    def __enter__(self):
        # E2B SDK 2.x requires Sandbox.create() as the factory (pre-2.x allowed
        # a bare Sandbox() constructor). See JC-003 lessons / E2B docs.
        self._sandbox = self._Sandbox.create()
        return self

    def __exit__(self, *_):
        try:
            self._sandbox.kill()
        except Exception:
            pass

    def run(self, code: str) -> tuple[str, float]:
        t0 = time.perf_counter_ns()
        execution = self._sandbox.run_code(code)
        elapsed_ns = time.perf_counter_ns() - t0
        stdout = "".join(execution.logs.stdout) if execution.logs.stdout else ""
        return stdout, elapsed_ns / 1_000_000


# ----- Benchmark loop ---------------------------------------------------------

def run_scenario(
    runner_cm,
    name: str,
    code: str,
    expected: str,
    runs: int,
    warmup: int,
    label: str,
) -> ScenarioStats:
    """Run a scenario N times against a runner context manager."""
    stats = ScenarioStats(runs=runs, warmup_discarded=warmup)
    with runner_cm() as runner:
        # Warmup: discarded from stats
        for i in range(warmup):
            try:
                runner.run(code)
            except Exception as e:
                print(f"  [{label}/{name}] warmup #{i} error: {e}", file=sys.stderr)

        # Measured
        for i in range(runs):
            try:
                stdout, elapsed_ms = runner.run(code)
                if expected and expected not in stdout:
                    print(
                        f"  [{label}/{name}] correctness fail #{i}: "
                        f"expected '{expected}' in stdout, got '{stdout[:100]}'",
                        file=sys.stderr,
                    )
                    stats.errors += 1
                stats.timings_ms.append(elapsed_ms)
            except Exception as e:
                stats.errors += 1
                if i < 3:  # only print first few error messages
                    print(f"  [{label}/{name}] run #{i} error: {e}", file=sys.stderr)
    return stats


def mann_whitney_u(a: list[float], b: list[float]) -> Optional[dict]:
    """
    Lightweight Mann-Whitney U via stdlib only. Returns U + p-value approximation
    (normal approximation, valid for n > 20 in each group).
    """
    if len(a) < 5 or len(b) < 5:
        return None
    combined = sorted([(v, "a") for v in a] + [(v, "b") for v in b])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) - 1 and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1

    rank_sum_a = sum(r for k, r in ranks.items() if combined[k][1] == "a")
    n_a, n_b = len(a), len(b)
    u_a = rank_sum_a - n_a * (n_a + 1) / 2
    u_b = n_a * n_b - u_a
    u = min(u_a, u_b)

    # Normal approximation for p-value
    mean_u = n_a * n_b / 2
    sd_u = (n_a * n_b * (n_a + n_b + 1) / 12) ** 0.5
    if sd_u == 0:
        return {"u": u, "p_value": 1.0}
    z = (u - mean_u) / sd_u
    # Two-tailed p-value via the standard normal CDF
    import math
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / 2 ** 0.5)))
    return {"u": round(u, 2), "z": round(z, 3), "p_value": round(p, 4)}


# ----- Main -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Cell vs E2B benchmark harness")
    parser.add_argument("--runs", type=int, default=100, help="Iterations per scenario (default: 100)")
    parser.add_argument("--warmup", type=int, default=10, help="Warmup iterations to discard (default: 10)")
    parser.add_argument("--skip-e2b", action="store_true", help="Skip E2B comparison (Cell-only run)")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()) + ["all"],
        default="all",
        help="Run a single scenario instead of all three",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "cell" / "benchmarks" / "RESULTS.jsonl"),
        help="Append-only RESULTS.jsonl path",
    )
    parser.add_argument("--print-hardware", action="store_true", help="Print hardware fingerprint and exit")
    args = parser.parse_args()

    fp = hardware_fingerprint()
    profile_id = hardware_profile_id(fp)
    sha = git_sha()

    if args.print_hardware:
        print(json.dumps({"hardware_profile_id": profile_id, **fp, "git_sha": sha}, indent=2))
        return 0

    print("=" * 70)
    print(f" Cell vs E2B — apples-to-apples benchmark")
    print("=" * 70)
    print(f" Hardware profile: {profile_id}")
    print(f" CPU: {fp.get('cpu', 'unknown')}")
    print(f" OS: {fp.get('os', 'unknown')}")
    print(f" Python: {fp.get('python')}")
    print(f" Git SHA: {sha[:12]}")
    print(f" Runs/scenario: {args.runs} ({args.warmup} warmup discarded)")
    print(f" E2B: {'SKIPPED' if args.skip_e2b else 'enabled (E2B_API_KEY set)'}")
    print("=" * 70)
    print()

    # Default "all" excludes file_io until the per-runtime path resolution lands
    # (see SCENARIOS["file_io"] comment).  Pass --scenario file_io to opt in.
    if args.scenario == "all":
        selected_scenarios = [k for k in SCENARIOS.keys() if k != "file_io"]
    else:
        selected_scenarios = [args.scenario]

    row: dict = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_sha": sha,
        "hardware_profile_id": profile_id,
        "hardware_fingerprint": fp,
        "harness_version": "0.1.0",
        "runs_per_scenario": args.runs,
        "warmup_discarded": args.warmup,
        "scenarios": {},
    }

    for name in selected_scenarios:
        scn = SCENARIOS[name]
        print(f"--- {name} ---")
        print(f"  {scn['description']}")

        # Cell run
        print(f"  Running Cell × {args.runs} ...", end=" ", flush=True)
        cell_stats = run_scenario(
            CellRunner, name, scn["code"], scn["expected_stdout_contains"],
            args.runs, args.warmup, "cell",
        )
        cell_summary = cell_stats.summary()
        # Defensive: if every run errored, p95_ms is absent (only p50_ms=None).
        p50 = cell_summary.get('p50_ms')
        p95 = cell_summary.get('p95_ms', '—')
        print(f"p50={p50}ms p95={p95}ms (errors={cell_stats.errors})")

        scenario_record = {
            "description": scn["description"],
            "cell": cell_summary,
        }

        # E2B run (optional)
        if not args.skip_e2b:
            try:
                print(f"  Running E2B  × {args.runs} ...", end=" ", flush=True)
                e2b_stats = run_scenario(
                    E2BRunner, name, scn["code"], scn["expected_stdout_contains"],
                    args.runs, args.warmup, "e2b",
                )
                e2b_summary = e2b_stats.summary()
                e2b_p50 = e2b_summary.get('p50_ms')
                e2b_p95 = e2b_summary.get('p95_ms', '—')
                print(f"p50={e2b_p50}ms p95={e2b_p95}ms (errors={e2b_stats.errors})")
                scenario_record["e2b"] = e2b_summary

                if cell_stats.timings_ms and e2b_stats.timings_ms:
                    cell_p50 = cell_stats.percentile(50)
                    e2b_p50 = e2b_stats.percentile(50)
                    if cell_p50 > 0:
                        factor = e2b_p50 / cell_p50
                        scenario_record["vs_e2b_factor"] = round(factor, 1)
                        print(f"  → Cell is {factor:.0f}× faster than E2B (p50)")
                    mw = mann_whitney_u(cell_stats.timings_ms, e2b_stats.timings_ms)
                    if mw:
                        scenario_record["mann_whitney_u"] = mw
            except RuntimeError as e:
                print(f"E2B skipped: {e}")
                scenario_record["e2b"] = None

        row["scenarios"][name] = scenario_record
        print()

    # Append to RESULTS.jsonl
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"Appended row to {output_path}")
    print()

    # Summary table
    print("=" * 70)
    print(" SUMMARY")
    print("=" * 70)
    print(f"{'Scenario':<20} {'Cell p50':>12} {'E2B p50':>12} {'Speedup':>10}")
    for name in selected_scenarios:
        rec = row["scenarios"][name]
        cell_p50 = rec["cell"]["p50_ms"]
        e2b_p50 = rec.get("e2b", {}).get("p50_ms") if rec.get("e2b") else None
        factor = rec.get("vs_e2b_factor", "—")
        cell_p50_str = f"{cell_p50}ms" if cell_p50 is not None else "—"
        e2b_p50_str = f"{e2b_p50}ms" if e2b_p50 is not None else "—"
        factor_str = f"{factor}×" if factor != "—" else "—"
        print(f"{name:<20} {cell_p50_str:>12} {e2b_p50_str:>12} {factor_str:>10}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
