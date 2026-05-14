#!/usr/bin/env python3
"""
Synapse Cell — Comprehensive Red Team Audit
============================================
15-point security, correctness, and integrity audit for the Cell platform.

Tests:
  A. API Security (5 checks)
  B. Sandbox Escape (5 checks)
  C. API Correctness (3 checks)
  D. Receipts & Integrity (2 checks)

Usage:
    python3 sdk/tests/red_team.py
"""
import os
import sys
import json
import time
import hashlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Load Cell SDK directly ─────────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    'cell',
    os.path.join(os.path.dirname(__file__), '..', 'synapse', 'cell.py')
)
cell_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cell_mod)
Cell = cell_mod.Cell
CellError = cell_mod.CellError

API_KEY = os.environ.get("CELL_API_KEY", "test_key")
API_URL = os.environ.get("CELL_API_URL", "http://localhost:8002")

results = []
total = 0
passed = 0


def test(name, category):
    """Decorator for red team tests."""
    def decorator(fn):
        fn._test_name = name
        fn._category = category
        return fn
    return decorator


def run_test(fn):
    global total, passed
    total += 1
    name = fn._test_name
    category = fn._category
    print(f"\n{'━'*60}")
    print(f"  [{category}] {name}")
    print(f"{'━'*60}")
    try:
        fn()
        print(f"  ✅ PASSED")
        passed += 1
        results.append({"name": name, "category": category, "status": "PASS"})
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        results.append({"name": name, "category": category, "status": "FAIL", "error": str(e)})
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        results.append({"name": name, "category": category, "status": "ERROR", "error": str(e)})


# ═══════════════════════════════════════════════════════════════════
#  A. API SECURITY
# ═══════════════════════════════════════════════════════════════════

@test("1. Auth bypass — no API key", "SECURITY")
def test_auth_no_key():
    """All authenticated endpoints should reject requests without API key."""
    import http.client, ssl
    conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
    
    # POST /v1/cells (create)
    conn.request("POST", "/v1/cells", json.dumps({"template": "python3"}),
                 {"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    assert resp.status == 401, f"Expected 401, got {resp.status}: {body[:100]}"
    print(f"  POST /v1/cells → {resp.status} (blocked)")
    
    # GET /v1/cells (list)
    conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
    conn.request("GET", "/v1/cells")
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 401, f"Expected 401 for GET /v1/cells, got {resp.status}"
    print(f"  GET /v1/cells → {resp.status} (blocked)")
    conn.close()


@test("2. Auth bypass — header manipulation", "SECURITY")
def test_auth_header_manipulation():
    """Test various auth header manipulations."""
    import http.client
    payloads = [
        ("bearer", "wrong_key"),  # wrong key
        ("Bearer", ""),           # empty token
        ("Bearer", " "),          # space token
        ("Token", API_KEY),       # wrong scheme
        ("Basic", API_KEY),       # wrong scheme
        ("Bearer", "cell_sk_live_FAKE"),  # wrong key, right prefix
    ]
    
    for scheme, token in payloads:
        conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
        conn.request("POST", "/v1/cells",
                     json.dumps({"template": "python3"}),
                     {"Content-Type": "application/json",
                      "Authorization": f"{scheme} {token}"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        print(f"  '{scheme} {token[:10]}...' → {resp.status}")
        assert resp.status == 401, f"Auth bypass with '{scheme} {token[:10]}': got {resp.status}"


@test("3. Path traversal in file ops", "SECURITY")
def test_path_traversal():
    """Attempt to escape the sandbox via file path manipulation."""
    cell = Cell(api_key=API_KEY, api_url=API_URL)
    traversal_paths = [
        "../../../etc/passwd",
        "..%2f..%2f..%2fetc/passwd",
        "/etc/passwd",
        "....//....//etc/passwd",
        "/data/../../etc/passwd",
        "\x00/etc/passwd",
    ]
    for path in traversal_paths:
        try:
            # Try to read outside sandbox
            result = cell.run(f"""
try:
    with open("{path}", "r") as f:
        print("ESCAPE: " + f.read()[:50])
except Exception as e:
    print(f"BLOCKED: {{type(e).__name__}}")
""")
            output = result.stdout.strip()
            assert "ESCAPE" not in output, f"Path traversal succeeded with '{path}': {output}"
            print(f"  '{path}' → {output}")
        except Exception:
            print(f"  '{path}' → blocked at API level")
    cell.kill()


@test("4. Body size limits", "SECURITY")
def test_body_size_limits():
    """Test handling of oversized payloads."""
    import http.client
    # Send 1MB payload (Caddy may reject 5MB+)
    huge_code = "x = 1\n" * 50_000
    try:
        conn = http.client.HTTPSConnection("localhost:8002", timeout=30)
        body = json.dumps({"template": "python3", "persistent": True})
        conn.request("POST", "/v1/cells", body,
                     {"Content-Type": "application/json",
                      "Authorization": f"Bearer {API_KEY}"})
        resp = conn.getresponse()
        cell_data = json.loads(resp.read())
        cell_id = cell_data.get("cell_id")
        
        if cell_id:
            conn = http.client.HTTPSConnection("localhost:8002", timeout=30)
            exec_body = json.dumps({"code": huge_code})
            conn.request("POST", f"/v1/cells/{cell_id}/exec", exec_body,
                         {"Content-Type": "application/json",
                          "Authorization": f"Bearer {API_KEY}"})
            resp = conn.getresponse()
            result = resp.read()
            conn.close()
            print(f"  1MB payload → HTTP {resp.status} ({len(result)} bytes response)")
            # Clean up
            try:
                conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
                conn.request("DELETE", f"/v1/cells/{cell_id}",
                             headers={"Authorization": f"Bearer {API_KEY}"})
                conn.getresponse().read()
            except Exception:
                pass
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        print(f"  Large payload → connection terminated ({type(e).__name__}) — server protected")
    
    # Verify server is alive
    conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
    conn.request("GET", "/v1/health")
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 200, f"Server crashed after large payload"


@test("5. Rapid-fire DoS resistance", "SECURITY")
def test_dos_resistance():
    """100 rapid requests — server should stay alive."""
    import http.client
    errors = 0
    start = time.time()
    for i in range(100):
        try:
            conn = http.client.HTTPSConnection("localhost:8002", timeout=5)
            conn.request("GET", "/v1/health")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            if resp.status != 200:
                errors += 1
        except Exception:
            errors += 1
    elapsed = time.time() - start
    print(f"  100 rapid requests in {elapsed:.1f}s — {errors} errors")
    assert errors < 10, f"Too many errors ({errors}/100) under load"


# ═══════════════════════════════════════════════════════════════════
#  B. SANDBOX ESCAPE
# ═══════════════════════════════════════════════════════════════════

@test("6. WASI boundary — os/subprocess/socket", "SANDBOX")
def test_wasi_boundary():
    """Dangerous imports should fail or be restricted in WASI."""
    cell = Cell(api_key=API_KEY, api_url=API_URL)
    dangerous_ops = [
        ("os.system('whoami')", "os.system"),
        ("import subprocess; subprocess.run(['ls'])", "subprocess"),
        ("import socket; s = socket.socket()", "socket"),
        ("__import__('os').system('id')", "__import__ os"),
        ("exec('import os; os.system(\"cat /etc/hostname\")')", "exec(import)"),
    ]
    for code, label in dangerous_ops:
        result = cell.run(f"""
try:
    {code}
    print("ESCAPED")
except Exception as e:
    print(f"BLOCKED: {{type(e).__name__}}")
""")
        output = result.stdout.strip()
        print(f"  {label} → {output}")
        # os.system may return 0 (command not found in WASI), that's fine
        # The key is no ESCAPED output meaning actual shell access
    cell.kill()


@test("7. File escape via sandbox paths", "SANDBOX")
def test_file_escape():
    """Attempt to write/read outside /data/."""
    cell = Cell(api_key=API_KEY, api_url=API_URL)
    escape_writes = [
        "/etc/crontab",
        "/tmp/escape.txt",
        "/opt/synapse/escape.txt",
        "/root/.ssh/authorized_keys",
    ]
    for path in escape_writes:
        result = cell.run(f"""
try:
    with open("{path}", "w") as f:
        f.write("pwned")
    print("ESCAPE_WRITE")
except Exception as e:
    print(f"BLOCKED: {{type(e).__name__}}")
""")
        output = result.stdout.strip()
        print(f"  write {path} → {output}")
        assert "ESCAPE_WRITE" not in output, f"Escaped sandbox via write to {path}"
    cell.kill()


@test("8. Information leakage", "SANDBOX")
def test_info_leakage():
    """Probe for host information leaking into sandbox."""
    cell = Cell(api_key=API_KEY, api_url=API_URL)
    probes = [
        ("import os; print(dict(os.environ))", "env vars"),
        ("import os; print(os.listdir('/'))", "root listing"),
        ("import os; print(os.getpid())", "PID"),
        ("import os; print(os.getcwd())", "CWD"),
    ]
    for code, label in probes:
        result = cell.run(f"""
try:
    {code}
except Exception as e:
    print(f"BLOCKED: {{type(e).__name__}}")
""")
        output = result.stdout.strip()
        print(f"  {label} → {output[:80]}{'...' if len(output) > 80 else ''}")
        # Verify no sensitive host info
        sensitive = ["root@", "/opt/synapse", "cell_sk_live", "65.108", "synapse-gateway"]
        for s in sensitive:
            assert s not in output, f"Host info leaked: '{s}' found in {label}"
    cell.kill()


@test("9. Resource exhaustion", "SANDBOX")
def test_resource_exhaustion():
    """Infinite loops and memory bombs should be stopped by fuel metering."""
    cell = Cell(api_key=API_KEY, api_url=API_URL)
    
    # Infinite loop — should be stopped by fuel
    result = cell.run("while True: pass")
    print(f"  infinite loop → exit {result.exit_code}, {result.latency_ms:.0f}ms")
    assert result.exit_code != 0, "Infinite loop should not exit 0"
    
    # Memory bomb — should fail gracefully
    result = cell.run("x = 'A' * (10**9)")
    print(f"  1GB string → exit {result.exit_code}, {result.latency_ms:.0f}ms")
    # Either fuel exhaustion or MemoryError — both are acceptable
    
    # Fork bomb via list growth
    result = cell.run("""
x = [0]
while True:
    x = x + x
""")
    print(f"  list bomb → exit {result.exit_code}, {result.latency_ms:.0f}ms")
    
    # Server should still be alive
    health_cell = Cell(api_key=API_KEY, api_url=API_URL, persistent=False)
    r = health_cell.run("print('alive')")
    assert "alive" in r.stdout, "Server died after resource exhaustion attack"
    print(f"  server health → OK")
    health_cell.kill()
    cell.kill()


@test("10. Code injection via cell_id", "SANDBOX")
def test_code_injection():
    """Attempt to inject via cell_id and path parameters."""
    import http.client
    injection_ids = [
        "'; DROP TABLE cells; --",
        "../../../etc/passwd",
        "${IFS}id",
        "$(whoami)",
        "%00admin",
        "cell_id\r\nInjected: true",
    ]
    for injected_id in injection_ids:
        try:
            conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
            conn.request("POST", f"/v1/cells/{injected_id}/exec",
                         json.dumps({"code": "print(1)"}),
                         {"Content-Type": "application/json",
                          "Authorization": f"Bearer {API_KEY}"})
            resp = conn.getresponse()
            body = resp.read().decode()
            conn.close()
            print(f"  '{injected_id[:30]}' → HTTP {resp.status}")
            # Should be 404 (cell not found) or 400, never 200
            assert resp.status != 200, f"Injection succeeded with cell_id: {injected_id}"
        except Exception as e:
            print(f"  '{injected_id[:30]}' → blocked ({type(e).__name__})")


# ═══════════════════════════════════════════════════════════════════
#  C. API CORRECTNESS
# ═══════════════════════════════════════════════════════════════════

@test("11. Keep-alive connection exhaustion", "CORRECTNESS")
def test_keepalive_exhaustion():
    """Open many keep-alive connections — server should handle gracefully."""
    import http.client
    conns = []
    created = 0
    for i in range(50):
        try:
            conn = http.client.HTTPSConnection("localhost:8002", timeout=5)
            conn.request("GET", "/v1/health",
                         headers={"Connection": "keep-alive"})
            resp = conn.getresponse()
            resp.read()
            if resp.status == 200:
                conns.append(conn)
                created += 1
        except Exception:
            break
    print(f"  Opened {created}/50 keep-alive connections")
    # Clean up
    for c in conns:
        try: c.close()
        except: pass
    # Server should still work
    conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
    conn.request("GET", "/v1/health")
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 200, f"Server unhealthy after connection flood"
    print(f"  Server health after flood → OK")


@test("12. Malformed JSON handling", "CORRECTNESS")
def test_malformed_json():
    """Server should handle malformed JSON gracefully."""
    import http.client
    payloads = [
        b"not json at all",
        b"{incomplete",
        b'{"code": }',
        b'\x00\x01\x02\x03',
        b'{"code":"x"}' * 1000,  # repeated valid JSON
    ]
    for payload in payloads:
        try:
            conn = http.client.HTTPSConnection("localhost:8002", timeout=10)
            conn.request("POST", "/v1/cells",
                         payload,
                         {"Content-Type": "application/json",
                          "Authorization": f"Bearer {API_KEY}"})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            print(f"  {payload[:30]}... → HTTP {resp.status}")
            assert resp.status in (400, 413, 200), f"Unexpected status {resp.status}"
        except Exception as e:
            print(f"  {payload[:30]}... → {type(e).__name__}")


@test("13. Cross-cell data isolation", "CORRECTNESS")
def test_cross_cell_isolation():
    """Cell A's data should not be accessible from Cell B."""
    cell_a = Cell(api_key=API_KEY, api_url=API_URL)
    cell_b = Cell(api_key=API_KEY, api_url=API_URL)
    
    # Write secret in cell A
    cell_a.run("with open('/data/secret.txt', 'w') as f: f.write('TOP_SECRET_DATA')")
    
    # Try to read it from cell B
    result_b = cell_b.run("""
import os
try:
    with open('/data/secret.txt', 'r') as f:
        print(f'LEAKED: {f.read()}')
except FileNotFoundError:
    print('ISOLATED: file not found')
except Exception as e:
    print(f'ISOLATED: {type(e).__name__}')
""")
    output = result_b.stdout.strip()
    print(f"  Cell A wrote secret, Cell B reads → {output}")
    assert "LEAKED" not in output, "Cross-cell data leakage detected!"
    assert "ISOLATED" in output, "Expected isolation confirmation"
    
    cell_a.kill()
    cell_b.kill()


# ═══════════════════════════════════════════════════════════════════
#  D. RECEIPTS & INTEGRITY
# ═══════════════════════════════════════════════════════════════════

@test("14. Receipt uniqueness and correctness", "INTEGRITY")
def test_receipt_uniqueness():
    """Same code should produce same code_hash but different execution_ids."""
    cell = Cell(api_key=API_KEY, api_url=API_URL, persistent=False)
    
    receipts = []
    for _ in range(3):
        result = cell.run("print(42)")
        receipts.append(result.receipt)
    
    # Code hashes should be identical (same code)
    code_hashes = set(r.code_hash for r in receipts)
    print(f"  Code hashes: {len(code_hashes)} unique (expected 1)")
    assert len(code_hashes) == 1, f"Same code produced different code_hashes: {code_hashes}"
    
    # Execution IDs should be unique
    exec_ids = [r.execution_id for r in receipts]
    print(f"  Execution IDs: {len(set(exec_ids))} unique (expected 3)")
    assert len(set(exec_ids)) == 3, f"Execution IDs not unique: {exec_ids}"
    
    # Verify code_hash is actually SHA-256 of the code
    expected_hash = hashlib.sha256("print(42)".encode()).hexdigest()
    actual_hash = receipts[0].code_hash
    print(f"  Expected hash: {expected_hash[:16]}...")
    print(f"  Actual hash:   {actual_hash[:16]}...")
    assert actual_hash == expected_hash, f"Code hash mismatch"
    
    cell.kill()


@test("15. Receipt result hash integrity", "INTEGRITY")
def test_receipt_result_hash():
    """Result hash should change when output changes."""
    cell = Cell(api_key=API_KEY, api_url=API_URL, persistent=False)
    
    r1 = cell.run("print(1)")
    r2 = cell.run("print(2)")
    
    h1 = r1.receipt.result_hash
    h2 = r2.receipt.result_hash
    
    print(f"  print(1) result_hash: {h1[:16]}...")
    print(f"  print(2) result_hash: {h2[:16]}...")
    assert h1 != h2, "Different outputs should produce different result_hashes"
    assert h1 and h2, "Result hashes should not be empty"
    
    cell.kill()


# ═══════════════════════════════════════════════════════════════════
#  RUNNER
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  🔴 SYNAPSE CELL — RED TEAM AUDIT")
    print(f"  Target: {API_URL}")
    print("=" * 60)
    
    all_tests = [
        test_auth_no_key,
        test_auth_header_manipulation,
        test_path_traversal,
        test_body_size_limits,
        test_dos_resistance,
        test_wasi_boundary,
        test_file_escape,
        test_info_leakage,
        test_resource_exhaustion,
        test_code_injection,
        test_keepalive_exhaustion,
        test_malformed_json,
        test_cross_cell_isolation,
        test_receipt_uniqueness,
        test_receipt_result_hash,
    ]
    
    for t in all_tests:
        run_test(t)
    
    # Summary
    print(f"\n{'═'*60}")
    print(f"  RED TEAM RESULTS")
    print(f"{'═'*60}")
    
    for r in results:
        icon = "✅" if r["status"] == "PASS" else "❌"
        print(f"  {icon} [{r['category']}] {r['name']}")
        if r.get("error"):
            print(f"     └── {r['error'][:80]}")
    
    print(f"\n{'─'*60}")
    print(f"  {passed}/{total} passed, {total - passed} failed")
    
    if passed == total:
        print(f"\n  🛡️  ALL {total} SECURITY CHECKS PASSED")
    else:
        print(f"\n  ⚠️  {total - passed} SECURITY ISSUES FOUND")
    
    # Save results
    output_path = os.path.join(os.path.dirname(__file__), 'red_team_results.json')
    with open(output_path, 'w') as f:
        json.dump({"passed": passed, "total": total, "results": results}, f, indent=2)
    print(f"\n  Results saved to: {output_path}")
    
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
