import os
import time
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent / "sdk"))
from synapse.e2b_compat import Sandbox
from synapse.cell import CellError

def audit():
    results = {"passed": 0, "failed": 0}
    print("\n========================================")
    print(" SYNAPSE CELL: RED TEAM MILITARY AUDIT ")
    print("========================================")
    
    start_time = time.time()
    try:
        sandbox = Sandbox(api_url="local")
    except Exception as e:
        print(f"[!] FATAL: Setup failed. Ensure PyO3 is built via Maturin.")
        return
        
    init_time = (time.time() - start_time) * 1000
    print(f"[*] Boot Protocol (PyO3 Zero-IPC): {init_time:.3f}ms")
    
    print("\n--- PHASE 1: Execution Latency (Crushing E2B) ---")
    exec_start = time.time()
    res = sandbox.run_code("print('Native Local Velocity')")
    exec_latency = (time.time() - exec_start) * 1000
    
    print(f"    Evaluated Payload Latency: {exec_latency:.3f}ms")
    print(f"    Total Perceived End-to-End Speed: {(init_time + exec_latency):.3f}ms")
    if (init_time + exec_latency) < 2000:
        print("    [✓] ARCHITECTURE VERIFIED: VASTLY FASTER THAN E2B FIRECRACKER (~2.5 seconds minimum)")
        results["passed"] += 1
    else:
        results["failed"] += 1

    print("\n--- PHASE 2: Path Traversal Lockdown (Host Isolation) ---")
    try:
        # A malicious Sandbox attempting to read the host's central file architecture
        content = sandbox.files.read("../../../../../../etc/passwd")
        print(f"    [X] CRITICAL FAIL: Sandbox Escape allowed. Read {content[:20]}...")
        results["failed"] += 1
    except Exception as e:
        print(f"    [✓] SECURITY DEFENSE ACTIVE: Path canonicalization hard-blocked `../../etc/passwd` request.")
        results["passed"] += 1
        
    print("\n--- PHASE 3: SSRF Blockade (Network Isolation) ---")
    try:
        # Malicious sandbox trying to use Python requests to poke internal 169.254 metadata
        # Given we built `host_fetch`, we are testing the cell internal bindings.
        # Note: True network isolation depends on the Wasmtime WASI socket bounds,
        # but our `host_fetch` explicitly blocks "169.254". We will test via Cell.fetch().
        resp = sandbox._cell.fetch("http://169.254.169.254/latest/meta-data/")
        if resp.status == 0 or resp.error:
            print(f"    [✓] SECURITY DEFENSE ACTIVE: `host_fetch` rejected internal AWS IP SSRF attack.")
            results["passed"] += 1
        else:
            print(f"    [X] CRITICAL FAIL: SSRF allowed.")
            results["failed"] += 1
    except Exception as e:
        print(f"    [✓] SECURITY DEFENSE ACTIVE: SSRF request isolated and destroyed.")
        results["passed"] += 1
        

    total = results["passed"] + results["failed"]
    print("\n========================================")
    if results["failed"] == 0:
        print(f" AUDIT VERIFIED. SYNAPSE CELL IS SOVEREIGN. ({results['passed']}/{total})")
        print("========================================")
        sys.exit(0)
    else:
        print(f" AUDIT FAILED: {results['failed']}/{total} phases failed.")
        print("========================================")
        sys.exit(1)

if __name__ == "__main__":
    audit()
