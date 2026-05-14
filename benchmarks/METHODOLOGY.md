# Synapse Cell — Benchmark Methodology

This document defines exactly what the canonical Cell-vs-E2B benchmark measures, how it measures it, and what's explicitly in and out of scope. Every public performance claim about Cell traces back to a `cell/benchmarks/RESULTS.jsonl` row produced by `cell/benchmarks/e2b_apples_to_apples.py` running under this methodology.

If the harness ever produces numbers that don't match this methodology, **fix the harness** — never silently update the document to match.

---

## What we measure

Three scenarios, each chosen to isolate a specific performance dimension:

### Scenario 1: `simple_eval`

**Code executed:** `print(2 + 2)`

**What it measures:** end-to-end latency of the simplest possible AI agent code call. Dominated by cold-start (first call) or warm-cache state (subsequent calls). This is the headline number that maps directly to "how slow does the agent feel between reasoning steps."

**Expected baseline (E2B Pro):** ~216 ms p50 (per E2B's own published benchmarks and Journal 166 measurements).

**Expected Cell number:** depends on hardware and cache state. On AX102 production: ~0.6 ms p50 (per Journal 166). On Mac release-profile: ~1.5 ms warm-cache (per JC-002).

### Scenario 2: `math_heavy`

**Code executed:**
```python
result = sum(i * i for i in range(10000))
print(result)
```

**What it measures:** CPU-bound execution time inside the sandbox. Tests whether the Wasm runtime introduces excessive overhead per arithmetic operation. Should mostly reflect the underlying CPU clock + Wasm JIT quality.

**Expected baseline:** ~288 ms (E2B per Journal 166).

**Expected Cell number:** ~40 ms (AX102, per Journal 166). Depends heavily on how well wasmtime JIT compiles the inner loop.

### Scenario 3: `file_io`

**Code executed:**
```python
data = b"x" * 100_000      # 100 KB
with open("/tmp/bench.bin", "wb") as f:
    f.write(data)
with open("/tmp/bench.bin", "rb") as f:
    n = len(f.read())
print(n)
```

**What it measures:** FFI cost for sandboxed filesystem operations. Cell uses `host_fs_write` / `host_fs_read` which canonicalize paths against `/sandbox_volumes/`. E2B uses their built-in filesystem mounted into the Firecracker VM.

**Note:** Cell needs Pro-tier license (Apache 2.0 EdgeCell doesn't include `host_fs_*`). EdgeCell will skip this scenario; Pro/Scale/Hub run it.

**Expected baseline:** ~251 ms (E2B per Journal 166).

**Expected Cell number:** ~42 ms (AX102 Pro tier, per Journal 166).

---

## How we measure

### Sample size

- **N = 100 runs per scenario** (default; configurable via `--runs`)
- **K = 10 warmup runs discarded** (default; configurable via `--warmup`)

Rationale: 100 runs gives stable p50 and p95 estimates without taking too long. 10 warmup runs amortize: wasmtime JIT compilation, sandbox volume directory creation, Python import cost, network connection establishment (for E2B), and any first-call overhead.

### Timing primitive

`time.perf_counter_ns()` — monotonic, nanosecond resolution. We measure wall-clock time from the moment the SDK call is invoked to the moment it returns. This includes:

- For Cell local PyO3 mode: just in-process execution time (no IPC)
- For Cell HTTP mode: TCP round-trip + serialization + execution
- For E2B: their full pipeline (TCP to AWS region + Firecracker boot + execution)

We do NOT subtract any "fair" overhead — the latency the user feels IS the latency we measure. Apples-to-apples per the harness name.

### Cache states reported

Each scenario is reported in two cache states:

| State | What it means |
|-------|---------------|
| **cold** | First call after process start; nothing primed |
| **warm** | Median of runs after the warmup phase; all caches hot |

We report both because both matter: warm-cache is what production agents see for the 99% case, cold-cache is what a customer sees on first deploy / first request after a deploy.

### Statistics computed

For each scenario × cache state:

- **p50** (median) — primary headline metric
- **p95** — worst-case for the bulk of users
- **p99** — tail latency (slow outliers)
- **mean ± stddev** — context for distribution shape
- **min / max** — sanity check (catastrophic outliers)
- **vs_e2b_factor** — Cell p50 / E2B p50 (as a multiplier — higher = Cell is faster)
- **mann_whitney_u_p_value** — Mann-Whitney U test for whether Cell and E2B distributions are significantly different

### Mann-Whitney U over Welch's t-test

Latency distributions are heavy-tailed and non-Gaussian (occasional GC pauses, cache misses, network blips). Welch's t-test assumes normality; Mann-Whitney U is non-parametric and robust to that violation. We report the U statistic + p-value, not Cohen's d.

---

## What's IN scope

- ✅ End-to-end SDK-call wall-clock latency
- ✅ Cell with PyO3 local mode (`api_url="local"`)
- ✅ Cell with HTTP mode (`api_url="https://cell.synapserun.dev"`) when running on a different host than the gateway
- ✅ E2B Pro tier via `e2b-code-interpreter` SDK
- ✅ Reproducibility on documented hardware (`HARDWARE_PROFILE.md`)
- ✅ Per-run hardware + git SHA + profile + cache state in RESULTS.jsonl

---

## What's OUT of scope

- ❌ **Modal, CodeSandbox, Daytona, Cloudflare Sandboxes, Northflank** — secondary competitors. Future expansion (separate `*_apples_to_apples.py` per competitor).
- ❌ **GPU workloads** — Cell Hub tier supports WGSL GPU FFIs, but the harness measures CPU-only scenarios. GPU benchmarks are a separate, future, harness.
- ❌ **Sustained throughput / concurrent sandboxes** — that's `cell/sdk/tests/stress_test.py`, not this harness.
- ❌ **Network bandwidth tests** — irrelevant for the per-call latency claim.
- ❌ **Memory profiling** — the 1 MB / 133 MB density claim is measured separately (by RAM accounting, not via this harness).
- ❌ **Power / energy efficiency** — the 7-9× vs Docker claim was RAPL-measured (Journal 04), not via this harness.
- ❌ **Custom Python packages** — the scenarios use stdlib only. Real-world AI agents use packages; that's a different benchmark we'll build later.

---

## Methodology integrity rules

1. **Don't mix profiles.** A run is either debug-profile or release-profile. Don't average across.
2. **Don't mix hardware.** Each RESULTS.jsonl row is tagged with hardware fingerprint. Don't aggregate across hosts unless the canonical reporting explicitly notes "median across hosts."
3. **Don't cherry-pick.** The canonical published number is the **median across the last 5 runs** (per the `cell:benchmark-e2b` skill). Don't quote the best single run.
4. **Don't reorder iterations.** Each iteration is independent; don't drop "outliers" by hand. Report what the harness produced.
5. **Don't skip the warmup discard.** The warmup amortizes cache priming; without it, p50 looks artificially worse.
6. **Don't hide failures.** If a run had errors (compile fail, network blip, sandbox crash), the JSONL row records the error count. A run with >5% errors is flagged invalid in the canonical reporting.
7. **Don't overstate against E2B.** Report Cell numbers honestly even when they're worse on a particular scenario or cache state.

---

## Scenario expansion protocol

When adding a new scenario:

1. Define it in this document FIRST (code, what it measures, expected baseline)
2. Add it to `e2b_apples_to_apples.py` as a new function with the same `_run_cell()` / `_run_e2b()` signature
3. Run it 100× on each side; verify result correctness on every iteration
4. Add at least one entry to `RESULTS.jsonl` with the new scenario before publishing any number
5. Open a JC-NNN entry covering the rationale + first-run results

---

## Pointers

- [`README.md`](README.md) — quick start
- [`HARDWARE_PROFILE.md`](HARDWARE_PROFILE.md) — pinned hardware specs
- [`e2b_apples_to_apples.py`](e2b_apples_to_apples.py) — the harness implementation
- [`RESULTS.jsonl`](RESULTS.jsonl) — append-only run history
- [`../CLAUDE.md`](../CLAUDE.md) — canonical numbers table (this methodology's output)
