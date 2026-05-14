# Synapse Cell — Benchmarks

The canonical, reproducible benchmark harness for Cell vs E2B (and other competitors). All public performance claims trace back to runs in this directory. Methodology before numbers; reproducibility before publication.

## Files in this directory

| File | What it is |
|------|------------|
| [`README.md`](README.md) | This file — orientation |
| [`METHODOLOGY.md`](METHODOLOGY.md) | Test definitions, statistical methodology, in/out-of-scope |
| [`HARDWARE_PROFILE.md`](HARDWARE_PROFILE.md) | Pinned hardware specs for every host that's run the harness |
| [`e2b_apples_to_apples.py`](e2b_apples_to_apples.py) | The harness itself — single-file Python, no extra deps |
| [`RESULTS.jsonl`](RESULTS.jsonl) | Append-only history of every run (tamper-evident) |

## Quick start

```bash
# Activate the Cell venv (built by maturin develop --release in cell/gateway/)
cd <your-clone>
source .venv-cell/bin/activate

# Cell-only run (no E2B comparison; useful for local iteration)
python3 cell/benchmarks/e2b_apples_to_apples.py --runs 100 --warmup 10 --skip-e2b

# Full Cell-vs-E2B comparison (requires E2B_API_KEY in env)
export E2B_API_KEY=<your e2b key>
python3 cell/benchmarks/e2b_apples_to_apples.py --runs 100 --warmup 10
```

The harness:
1. Runs each scenario `--runs N` times against Cell (and E2B if available)
2. Discards the first `--warmup K` runs to amortize cache priming
3. Computes p50, p95, p99, mean, std dev per scenario
4. Computes Mann-Whitney U effect size between Cell and E2B
5. Appends one JSONL row to `RESULTS.jsonl` with everything (hardware fingerprint, git SHA, raw timings, derived stats)
6. Prints a summary table to stdout

## Scenarios

Three workloads, defined precisely in `METHODOLOGY.md`:

1. **simple_eval** — `print(2 + 2)`. Measures cold-start + minimal exec.
2. **math_heavy** — sum of squares 1..10000. Measures CPU-bound exec.
3. **file_io** — write 100KB to a sandboxed path, read back. Measures FFI cost (Pro-tier feature in Cell; E2B uses their built-in fs).

## Reading RESULTS.jsonl

```bash
# Show the latest run summary
tail -1 cell/benchmarks/RESULTS.jsonl | python3 -m json.tool

# Median speedup over last 5 runs (the canonical headline)
tail -5 cell/benchmarks/RESULTS.jsonl | python3 -c "
import sys, json, statistics
factors = []
for line in sys.stdin:
    d = json.loads(line)
    if d.get('scenarios', {}).get('simple_eval', {}).get('vs_e2b_factor'):
        factors.append(d['scenarios']['simple_eval']['vs_e2b_factor'])
if factors:
    print(f'Median simple_eval speedup over last {len(factors)} runs: {statistics.median(factors):.0f}×')
"
```

## When to run

- **Weekly** during active development (catch perf regressions early)
- **Before any external launch** (Show HN, blog, conference talk)
- **After bumping wasmtime, pyo3, or any dep** that touches the hot path
- **When a customer asks for fresh numbers** in a sales conversation

The `cell:benchmark-e2b` skill (in `.claude/skills/`) automates the full publish workflow including updating canonical numbers in `cell/CLAUDE.md` and writing a JC- journal entry.

## What this harness is NOT

- ❌ Not an exhaustive performance test suite — three scenarios cover the headline claims, not every code path
- ❌ Not a stress test — that lives at `cell/sdk/tests/stress_test.py`
- ❌ Not a security audit — that lives at `cell/run_test.py` (Military Audit)
- ❌ Not a continuous integration gate — yet. Future: add `cell:benchmark-e2b` as a daily cron with regression alerts

## Honest framing

The headline `360× faster than E2B` claim throughout `cell/CLAUDE.md`, `cell/gtm/pricing.md`, and `cell/gtm/show-hn-draft.md` was originally measured on production Hetzner AX102 (AMD Ryzen 9 7950X3D, 128 GB DDR5, native Linux x86_64). JC-002 confirmed the architecture holds up — Mike's Mac with release-profile PyO3 hit ~1.5 ms warm-cache execution vs E2B's ~216 ms p50 = ~144× on a laptop.

For the public claim to be fully defensible, the harness needs to run on AX102 and the resulting RESULTS.jsonl row becomes the canonical reference. Until then, all public numbers should carry "see RESULTS.jsonl latest entry" as the methodology citation.

## Pointers

- [`../CLAUDE.md`](../CLAUDE.md) — canonical benchmark numbers table (single source of truth)
- [`../gtm/pricing.md`](../gtm/pricing.md) — pricing rationale tied to performance claims
- [`../journals/JC-002_wasmtime_upgrade.md`](../journals/JC-002_wasmtime_upgrade.md) — first verified post-wasmtime-upgrade Military Audit numbers
- [`.claude/skills/cell-benchmark-e2b/SKILL.md`](../../.claude/skills/cell-benchmark-e2b/SKILL.md) — automated publish workflow
