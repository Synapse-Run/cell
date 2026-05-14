#!/usr/bin/env python3
"""
Cell raw execution benchmark — measures Wasm exec latency WITHOUT HTTP/network overhead.

This script connects to a running gateway and measures ONLY the exec round-trip,
NOT cell creation/teardown. Use this for accurate p50/p95/p99 production numbers.

Usage:
    # Against local gateway:
    python3 bench_raw_exec.py --api http://localhost:8002 --runs 200 --warmup 20

    # Against AX102 production:
    python3 bench_raw_exec.py --api http://ax102:8002 --runs 200 --warmup 20

    # Pure exec (reuses single cell, no create/kill per iteration):
    python3 bench_raw_exec.py --api http://localhost:8002 --runs 500 --warmup 50 --reuse-cell
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
import platform


def create_cell(api_url):
    """Create a sandbox cell, return cell_id."""
    data = json.dumps({"template": "python3"}).encode()
    req = urllib.request.Request(
        f"{api_url}/v1/cells",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())["cell_id"]


def exec_code(api_url, cell_id, code):
    """Execute code in a cell, return (result_dict, elapsed_ms)."""
    data = json.dumps({"code": code, "language": "python"}).encode()
    t0 = time.perf_counter_ns()
    req = urllib.request.Request(
        f"{api_url}/v1/cells/{cell_id}/exec",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000
    result = json.loads(resp.read())
    return result, elapsed_ms


def kill_cell(api_url, cell_id):
    """Kill a sandbox cell."""
    req = urllib.request.Request(f"{api_url}/v1/cells/{cell_id}", method="DELETE")
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def compute_stats(timings):
    """Compute p50/p95/p99/mean/stdev/min/max."""
    timings.sort()
    n = len(timings)
    return {
        "n": n,
        "p50_ms": round(timings[n // 2], 3),
        "p95_ms": round(timings[int(n * 0.95)], 3),
        "p99_ms": round(timings[int(n * 0.99)], 3),
        "mean_ms": round(statistics.mean(timings), 3),
        "stdev_ms": round(statistics.stdev(timings) if n > 1 else 0, 3),
        "min_ms": round(min(timings), 3),
        "max_ms": round(max(timings), 3),
    }


SCENARIOS = {
    "simple_eval": {
        "code": "print(2 + 2)",
        "expected": "4",
        "description": "Minimum AI-agent tool call — headline latency number",
    },
    "math_heavy": {
        "code": "total = 0\nfor i in range(10000):\n    total = total + i * i\nprint(total)",
        "expected": "333283335000",
        "description": "CPU-bound loop — tests Wasm JIT quality",
    },
    "string_ops": {
        "code": 's = "hello" * 1000\nprint(len(s))',
        "expected": "5000",
        "description": "String allocation — tests memory management",
    },
}


def main():
    parser = argparse.ArgumentParser(description="Cell raw execution benchmark")
    parser.add_argument("--api", default="http://localhost:8002", help="Gateway URL")
    parser.add_argument("--runs", type=int, default=200, help="Measured iterations")
    parser.add_argument("--warmup", type=int, default=20, help="Warmup iterations")
    parser.add_argument("--reuse-cell", action="store_true", help="Reuse one cell (no per-run create/kill)")
    parser.add_argument("--output", default=None, help="JSONL output path")
    args = parser.parse_args()

    # Health check
    try:
        resp = urllib.request.urlopen(f"{args.api}/v1/health")
        health = json.loads(resp.read())
        print(f"Gateway: {health}")
    except Exception as e:
        print(f"Cannot reach {args.api}: {e}", file=sys.stderr)
        return 1

    print(f"\n{'=' * 60}")
    print(f" Cell Raw Execution Benchmark")
    print(f"{'=' * 60}")
    print(f" API:     {args.api}")
    print(f" Runs:    {args.runs} ({args.warmup} warmup)")
    print(f" Mode:    {'reuse-cell' if args.reuse_cell else 'fresh-cell-per-run'}")
    print(f" Host:    {platform.node()} ({platform.machine()})")
    print(f"{'=' * 60}\n")

    results = {}

    for name, scenario in SCENARIOS.items():
        print(f"--- {name}: {scenario['description']} ---")

        if args.reuse_cell:
            cell_id = create_cell(args.api)

        timings = []
        errors = 0

        for i in range(args.warmup + args.runs):
            try:
                if not args.reuse_cell:
                    cell_id = create_cell(args.api)

                result, elapsed_ms = exec_code(args.api, cell_id, scenario["code"])

                if not args.reuse_cell:
                    kill_cell(args.api, cell_id)

                if i >= args.warmup:
                    timings.append(elapsed_ms)
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  error #{i}: {e}", file=sys.stderr)

        if args.reuse_cell:
            kill_cell(args.api, cell_id)

        if timings:
            stats = compute_stats(timings)
            stats["errors"] = errors
            results[name] = stats
            print(f"  p50={stats['p50_ms']}ms  p95={stats['p95_ms']}ms  p99={stats['p99_ms']}ms  mean={stats['mean_ms']}ms  (errors={errors})")
        else:
            print(f"  ALL RUNS FAILED ({errors} errors)")
        print()

    # Output
    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api": args.api,
        "host": platform.node(),
        "arch": platform.machine(),
        "mode": "reuse-cell" if args.reuse_cell else "fresh-cell",
        "runs": args.runs,
        "warmup": args.warmup,
        "scenarios": results,
    }

    if args.output:
        with open(args.output, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"Appended to {args.output}")

    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
