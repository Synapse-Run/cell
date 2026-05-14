# Synapse Cell — Hardware Profiles

Pinned hardware specifications for every host that has run the canonical benchmark harness. Each `cell/benchmarks/RESULTS.jsonl` row references one of these profiles by ID. New hosts get a new profile entry below; existing hosts get updated in place when their software stack changes (rust toolchain, wasmtime version, etc.) — but never deleted.

---

## Profile: mike-mac (2026-04-15)

Mike's primary development laptop. Used for: rapid iteration on the harness, smoke-testing wasmtime upgrades, validating PyO3 builds before AX102 deploy.

**This is NOT the host for canonical published numbers.** Production AX102 (below, when established) is the canonical reference.

| Field | Value |
|-------|-------|
| Profile ID | mike-mac |
| Hostname | MacBook-Air.local |
| OS | Darwin 24.6.0 arm64 |
| CPU | Apple M4 |
| Cores (physical) | 10 |
| Cores (logical) | 10 |
| RAM | 32 GB |
| Rust | rustc 1.92.0 (ded5c06cf 2025-12-08) |
| wasmtime crate | 43.0.1 (per Cargo.lock) |
| wasmtime-wasi crate | 43.0.1 (per Cargo.lock) |
| pyo3 crate | 0.21.2 with abi3-py38 |
| Python (default) | Python 3.14.2 |
| Python (.venv-cell) | 3.14.2 |
| Build profile for canonical numbers | release (LTO, opt-level=3, codegen-units=1, strip) |

---

## Profile: ax102-helsinki (PLANNED — not yet measured under this harness)

Production benchmark server. Hetzner Helsinki, dedicated bare-metal. This is where canonical published numbers WILL come from once Day 8 deploy lands.

| Field | Value |
|-------|-------|
| Profile ID | ax102-helsinki |
| Hostname | (TBD — Hetzner-assigned) |
| Public IP | 65.108.120.219 (per AGENT_DIRECTIVE_CELL_COMMERCIAL.md) |
| OS | Linux (Ubuntu 24.04 LTS or similar — to confirm on first deploy) |
| CPU | AMD Ryzen 9 7950X3D (16 cores / 32 threads, 4.2 GHz base / 5.7 GHz boost, 128 MB L3) |
| RAM | 128 GB DDR5-5200 ECC |
| Storage | NVMe SSD (Hetzner-assigned, ≥1 TB) |
| Network | 1 Gbit/s symmetric, EU peering |
| Rust | (to confirm on first deploy) |
| wasmtime crate | 43.0.1 (must match Mac to ensure parity) |
| Build profile for canonical numbers | release (LTO, opt-level=3, codegen-units=1, strip) |

---

## Profile: ax102-ashburn (PLANNED — Horizon 2 milestone 2.6)

US-East deployment for latency parity with E2B's US customers. Same Hetzner AX102 spec as Helsinki, US data center. Roadmap milestone 2.6.

(Profile will be populated when Mike provisions the box.)

---

## How to add a new profile

1. Run the harness on the new host with `--print-hardware` (the harness prints a profile block to stdout).
2. Append the block to this file with a new `## Profile: <id>` heading.
3. Future runs of `e2b_apples_to_apples.py` on that host will tag their RESULTS.jsonl rows with `hardware_profile_id` matching the new heading.

## How NOT to use this file

- ❌ Don't aggregate numbers across profiles without an explicit caveat.
- ❌ Don't compare a debug-profile run on Mac to a release-profile run on AX102 and call it apples-to-apples — they're not.
- ❌ Don't delete an old profile when hardware changes — add a new entry. The history matters for trend analysis.
