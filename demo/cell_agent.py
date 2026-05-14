#!/usr/bin/env python3
"""cell-agent: A minimal AI coding agent with cryptographic execution receipts.

Every code execution runs inside a Cell sandbox (Wasm-isolated, 1MB footprint)
and produces a SHA-256 receipt proving exactly what ran and what it returned.

Usage:
    pip install synapserun
    python cell_agent.py "Calculate the first 20 fibonacci numbers"
    python cell_agent.py --receipt-log receipts.jsonl "Sort these numbers: 5,3,1,4,2"

This is the demo agent from Synapse Cell — the sandbox library you embed
inside your software, not the cloud service your software calls.

License: Apache-2.0 / AGPL-3.0 dual license
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# ── Try local dev SDK first, then installed package ──────────────
try:
    sdk_path = os.path.join(os.path.dirname(__file__), "..", "sdk")
    if os.path.isdir(sdk_path):
        sys.path.insert(0, sdk_path)
    from synapse.cell import Cell, CellResult
except ImportError:
    try:
        from synapserun import Cell, CellResult
    except ImportError:
        print("ERROR: Install Cell first: pip install synapserun")
        sys.exit(1)


# ── ANSI colors ──────────────────────────────────────────────────
class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"


def print_banner():
    print(f"""
{C.CYAN}╔══════════════════════════════════════════════════════╗
║  {C.BOLD}cell-agent{C.RESET}{C.CYAN}  — Sandboxed AI coding with receipts   ║
║  Every execution is Wasm-isolated + SHA-256 proven   ║
╚══════════════════════════════════════════════════════╝{C.RESET}
""")


def format_receipt(receipt) -> str:
    """Format a CellReceipt for human display."""
    if not receipt:
        return f"  {C.DIM}(no receipt){C.RESET}"
    return (
        f"  {C.DIM}┌─ Receipt ────────────────────────────────────┐{C.RESET}\n"
        f"  {C.DIM}│{C.RESET} exec_id:  {receipt.execution_id[:16]}…\n"
        f"  {C.DIM}│{C.RESET} code:     {receipt.code_hash[:16]}…\n"
        f"  {C.DIM}│{C.RESET} result:   {receipt.result_hash[:16]}…\n"
        f"  {C.DIM}│{C.RESET} chain:    {C.GREEN}{receipt.receipt_hash[:16]}…{C.RESET}\n"
        f"  {C.DIM}│{C.RESET} time:     {datetime.fromtimestamp(receipt.timestamp / 1000, tz=timezone.utc).isoformat()}\n"
        f"  {C.DIM}└──────────────────────────────────────────────┘{C.RESET}"
    )


def save_receipt(receipt, code: str, log_path: str):
    """Append receipt to JSONL log."""
    if not receipt:
        return
    entry = {
        "timestamp": receipt.timestamp,
        "execution_id": receipt.execution_id,
        "code_hash": receipt.code_hash,
        "result_hash": receipt.result_hash,
        "receipt_hash": receipt.receipt_hash,
        "code_preview": code[:200],
        "template": receipt.template,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def plan_steps(task: str) -> list[str]:
    """Break a task into executable Python steps.

    This is the simplest possible "planner" — no LLM needed.
    In production, this would call DeepSeek/Claude/GPT to generate
    the code steps. For the demo, we use pattern matching.
    """
    task_lower = task.lower()

    # Pattern: math/calculation
    if any(w in task_lower for w in ["fibonacci", "fib", "prime", "factorial"]):
        if "fibonacci" in task_lower or "fib" in task_lower:
            return [
                "# Calculate Fibonacci numbers\ndef fib(n):\n    a, b = 0, 1\n    result = []\n    for _ in range(n):\n        result.append(a)\n        a, b = b, a + b\n    return result\n\nprint(fib(20))",
            ]
        if "prime" in task_lower:
            return [
                "# Find prime numbers\ndef primes(n):\n    sieve = [True] * (n + 1)\n    sieve[0] = sieve[1] = False\n    for i in range(2, int(n**0.5) + 1):\n        if sieve[i]:\n            for j in range(i*i, n+1, i):\n                sieve[j] = False\n    return [i for i, v in enumerate(sieve) if v]\n\nprint(primes(100))",
            ]

    # Pattern: sort
    if "sort" in task_lower:
        # Extract numbers from the task
        import re
        nums = re.findall(r'-?\d+\.?\d*', task)
        nums_str = ", ".join(nums) if nums else "5, 3, 1, 4, 2"
        return [
            f"# Sort numbers\nnums = [{nums_str}]\nprint(f'Input:  {{nums}}')\nnums.sort()\nprint(f'Sorted: {{nums}}')",
        ]

    # Pattern: general code request
    if any(w in task_lower for w in ["hello", "print", "test"]):
        return [
            "print('Hello from Cell sandbox!')\nimport sys\nprint(f'Python {sys.version}')\nprint(f'Platform: sandboxed Wasm')",
        ]

    # Default: echo the task as a comment + simple demo
    return [
        f"# Task: {task}\n# (In production, an LLM would generate this code)\nprint('Cell sandbox is running!')\nprint(f'Task received: {task!r}')\nresult = 2 + 2\nprint(f'Quick math check: 2+2 = {{result}}')",
    ]


def run_agent(task: str, receipt_log: str | None = None, verbose: bool = False):
    """Run the agent: plan → execute in Cell → show receipts."""
    print_banner()
    print(f"  {C.BOLD}Task:{C.RESET} {task}\n")

    # Initialize Cell sandbox
    print(f"  {C.DIM}Initializing Cell sandbox...{C.RESET}")
    t0 = time.perf_counter()
    try:
        cell = Cell(api_url="local", template="python3")
    except Exception as e:
        print(f"  {C.RED}Failed to start Cell: {e}{C.RESET}")
        print(f"  {C.DIM}Make sure PyO3 backend is built: cd cell/gateway && maturin develop --release{C.RESET}")
        sys.exit(1)
    init_ms = (time.perf_counter() - t0) * 1000
    print(f"  {C.GREEN}✓ Sandbox ready in {init_ms:.1f}ms{C.RESET}\n")

    # Plan steps
    steps = plan_steps(task)
    print(f"  {C.BOLD}Plan:{C.RESET} {len(steps)} step(s)\n")

    # Execute each step
    total_receipts = []
    for i, code in enumerate(steps, 1):
        print(f"  {C.CYAN}── Step {i}/{len(steps)} ──{C.RESET}")
        if verbose:
            for line in code.split("\n"):
                print(f"  {C.DIM}  {line}{C.RESET}")
            print()

        result = cell.run(code)

        if result.ok:
            print(f"  {C.GREEN}✓ Exit 0 in {result.latency_ms:.2f}ms{C.RESET}")
            if result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    print(f"  {C.BOLD}  → {line}{C.RESET}")
        else:
            print(f"  {C.RED}✗ Exit {result.exit_code} in {result.latency_ms:.2f}ms{C.RESET}")
            if result.stderr.strip():
                for line in result.stderr.strip().split("\n"):
                    print(f"  {C.RED}  {line}{C.RESET}")

        # Show receipt
        print(format_receipt(result.receipt))

        if result.receipt:
            total_receipts.append(result.receipt)
            if receipt_log:
                save_receipt(result.receipt, code, receipt_log)
        print()

    # Summary
    print(f"  {C.CYAN}══════════════════════════════════════════════════{C.RESET}")
    print(f"  {C.BOLD}Execution complete.{C.RESET}")
    print(f"  Steps: {len(steps)} | Receipts: {len(total_receipts)} | All Wasm-isolated")
    if receipt_log and total_receipts:
        print(f"  Receipt log: {receipt_log} ({len(total_receipts)} entries)")
    print(f"  {C.DIM}Verify any receipt: sha256(exec_id || code_hash || result_hash){C.RESET}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="cell-agent: AI coding with cryptographic execution receipts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s "Calculate the first 20 fibonacci numbers"
  %(prog)s --receipt-log audit.jsonl "Sort 5,3,1,4,2"
  %(prog)s --verbose "Hello world test"
""",
    )
    parser.add_argument("task", help="Task description for the agent")
    parser.add_argument(
        "--receipt-log",
        metavar="FILE",
        help="Append execution receipts to this JSONL file",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show generated code before execution",
    )
    args = parser.parse_args()
    run_agent(args.task, args.receipt_log, args.verbose)


if __name__ == "__main__":
    main()
