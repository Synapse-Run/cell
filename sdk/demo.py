#!/usr/bin/env python3
"""Synapse Cell — End-to-End Demo

This script demonstrates the full Cell sandbox workflow:
  1. Create a sandbox
  2. Execute Python code with persistent state
  3. Create a persistent volume
  4. Write/read files on the volume
  5. Use the E2B-compatible Sandbox interface
  6. Clean up

Prerequisites:
  Start the gateway with a Pro license:

    cd cell/gateway
    SYNAPSE_API_KEY=demo \\
    CELL_PORT=8001 \\
    SYNAPSE_LICENSE_CERT='...' \\
    cargo run --release

  Then run:
    python3 cell/sdk/demo.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

GATEWAY = os.environ.get("CELL_GATEWAY_URL", "http://127.0.0.1:8001")
API_KEY = os.environ.get("SYNAPSE_API_KEY", "demo")


def banner(msg: str):
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}")


def demo_cell_basic():
    """Demo 1: Basic Cell execution with persistent state."""
    from synapse.cell import Cell

    banner("Demo 1: Cell — Persistent Python Sandbox")

    cell = Cell(api_url=GATEWAY, api_key=API_KEY)
    print(f"  ✓ Created cell: {cell.cell_id}")

    # Execution 1: define a variable
    r1 = cell.run("import math; TAU = math.pi * 2")
    print(f"  ✓ Exec 1 (define TAU): latency={r1.latency_ms:.1f}ms")

    # Execution 2: use the variable — proves persistent state
    r2 = cell.run("print(f'TAU = {TAU:.6f}')")
    print(f"  ✓ Exec 2 (use TAU):    stdout={r2.stdout.strip()!r}")

    # Execution 3: return a computed value
    r3 = cell.run("result = sum(range(1, 101))")
    print(f"  ✓ Exec 3 (sum 1..100): result={r3.result}")

    # Cryptographic receipt chain
    print(f"  ✓ Receipt chain: {r3.receipt}")

    cell.kill()
    print(f"  ✓ Cell killed")
    return True


def demo_filesystem():
    """Demo 2: Filesystem operations."""
    from synapse.cell import Cell

    banner("Demo 2: Cell — Filesystem (read/write/list)")

    cell = Cell(api_url=GATEWAY, api_key=API_KEY)

    cell.write_file("hello.txt", "Hello from Synapse Cell!")
    content = cell.read_file("hello.txt")
    print(f"  ✓ Write + read: {content!r}")

    cell.make_dir("scripts")
    cell.write_file("scripts/calc.py", "print(42 * 42)")
    entries = cell.list_files("")
    print(f"  ✓ Directory listing: {[e.name for e in entries]}")

    cell.kill()
    return True


def demo_volumes():
    """Demo 3: Persistent Volumes — data survives sandbox death."""
    import json
    import http.client
    from urllib.parse import urlparse

    banner("Demo 3: Persistent Volumes (Pro License)")

    parsed = urlparse(GATEWAY)
    host, port = parsed.hostname, parsed.port or 8001

    def raw(method, path, body=None):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        data = json.dumps(body).encode() if body else None
        headers = {"Content-Type": "application/json"}
        if data:
            headers["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode()
        conn.close()
        if resp.status >= 400:
            print(f"  ⚠ Volumes require Pro license. Skipping. ({resp_body[:80]})")
            return None
        return json.loads(resp_body) if resp_body else {}

    # Create
    import base64
    resp = raw("POST", "/v1/volumes", {"volume_id": "demo-vol"})
    if resp is None:
        return None
    print(f"  ✓ Created volume: {resp.get('volume_id')}")

    # Write file
    content = "Persistent data survives sandbox restarts."
    b64 = base64.b64encode(content.encode()).decode()
    raw("POST", "/v1/volumes/demo-vol/files", {"path": "data.txt", "data": b64})
    print(f"  ✓ Wrote data.txt to volume")

    # Read file
    resp = raw("GET", "/v1/volumes/demo-vol/files?path=data.txt")
    decoded = base64.b64decode(resp["data"]).decode()
    print(f"  ✓ Read back: {decoded!r}")

    # List
    vols = raw("GET", "/v1/volumes")
    print(f"  ✓ Volumes on gateway: {len(vols)}")

    # Cleanup
    raw("DELETE", "/v1/volumes/demo-vol")
    print(f"  ✓ Volume deleted")
    return True


def demo_e2b_compat():
    """Demo 4: E2B drop-in compatibility — one-line import change."""
    banner("Demo 4: E2B Compatibility (drop-in Sandbox)")

    # This is the ONLY line that changes when migrating from E2B:
    #   from e2b_code_interpreter import Sandbox
    from synapse.e2b_compat import Sandbox

    sbx = Sandbox(api_url=GATEWAY, api_key=API_KEY)
    print(f"  ✓ Sandbox created: {sbx.id}")

    result = sbx.run_code("x = [i**2 for i in range(10)]; print(sum(x))")
    print(f"  ✓ run_code output: {result.text.strip()!r}")

    sbx.files.write("/home/user/msg.txt", "E2B API works on Synapse!")
    content = sbx.files.read("/home/user/msg.txt")
    print(f"  ✓ files.write/read: {content!r}")

    sbx.kill()
    print(f"  ✓ Sandbox killed")
    return True


def demo_timing():
    """Demo 5: Cold start benchmark."""
    from synapse.cell import Cell

    banner("Demo 5: Cold Start Benchmark (10 iterations)")

    times = []
    for i in range(10):
        t0 = time.perf_counter()
        cell = Cell(api_url=GATEWAY, api_key=API_KEY)
        r = cell.run("1+1")
        elapsed = (time.perf_counter() - t0) * 1000
        times.append(elapsed)
        cell.kill()

    avg = sum(times) / len(times)
    best = min(times)
    print(f"  Average: {avg:.1f}ms")
    print(f"  Best:    {best:.1f}ms")
    print(f"  All:     {', '.join(f'{t:.0f}ms' for t in times)}")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("  Synapse Cell — End-to-End Demo")
    print(f"  Gateway: {GATEWAY}")
    print("=" * 60)

    demos = [
        ("Basic Cell", demo_cell_basic),
        ("Filesystem", demo_filesystem),
        ("Volumes", demo_volumes),
        ("E2B Compat", demo_e2b_compat),
        ("Cold Start", demo_timing),
    ]

    passed, failed, skipped = 0, 0, 0
    for name, fn in demos:
        try:
            result = fn()
            if result is True:
                passed += 1
            elif result is None:
                skipped += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ {name} FAILED: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)
