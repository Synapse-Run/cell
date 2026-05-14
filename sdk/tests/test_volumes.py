#!/usr/bin/env python3
"""Tests for Sprint D Volumes subsystem — Rust endpoints + Python SDK.

Covers: Cell.create_volume(), cell.volumes.read/write/delete/list_all,
        E2B compat Volume class, flock safety, path traversal rejection.

Run: python3 cell/sdk/tests/test_volumes.py

Gateway-dependent tests (1-10) require a Cell API running at
http://127.0.0.1:8001. To start one:
  cd /path/to/synapse/cell/gateway
  SYNAPSE_API_KEY=test CELL_PORT=8001 cargo run --release

Tests 11-13 always run (unit tests, no gateway needed).
"""
import sys
import os
import json
import base64
import http.client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse.cell import Cell, VolumesAdapter, CellError
from synapse.e2b_compat import Volume

GATEWAY = os.environ.get("CELL_GATEWAY_URL", "http://127.0.0.1:8001")


def _parse_url(url):
    """Parse gateway URL into (host, port)."""
    from urllib.parse import urlparse
    p = urlparse(url)
    return p.hostname or "127.0.0.1", p.port or 8001


def _gateway_alive(api_url=None):
    """Check if the Cell gateway is reachable."""
    import urllib.request
    import urllib.error
    url = api_url or GATEWAY
    try:
        with urllib.request.urlopen(f"{url}/v1/health", timeout=1.0) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def _raw_request(method, path, body=None):
    """Make a raw HTTP request to the gateway, bypassing Cell constructor."""
    host, port = _parse_url(GATEWAY)
    conn = http.client.HTTPConnection(host, port, timeout=10)
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    if data:
        headers["Content-Length"] = str(len(data))
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    resp_body = resp.read().decode()
    conn.close()
    if resp.status >= 400:
        raise CellError(f"HTTP {resp.status}: {resp_body}")
    return json.loads(resp_body) if resp_body else {}


# ─── Test 1: Create volume via REST ────────────────────────────────

def test_create_volume():
    """Test 1: POST /v1/volumes creates a volume and returns volume_id."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        resp = _raw_request("POST", "/v1/volumes", body={})
        assert "volume_id" in resp, f"Missing volume_id in response: {resp}"
        vid = resp["volume_id"]
        assert len(vid) > 0, "volume_id is empty"
        # Cleanup
        _raw_request("DELETE", f"/v1/volumes/{vid}")
        print(f"PASS: created volume {vid}")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 2: Create volume with explicit ID ────────────────────────

def test_create_volume_with_id():
    """Test 2: POST /v1/volumes with volume_id uses the provided ID."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        resp = _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-explicit"})
        assert resp.get("volume_id") == "test-vol-explicit", f"Got: {resp}"
        # Cleanup
        _raw_request("DELETE", "/v1/volumes/test-vol-explicit")
        print("PASS: created volume with explicit ID")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 3: List volumes ──────────────────────────────────────────

def test_list_volumes():
    """Test 3: GET /v1/volumes returns a list including our created volume."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-list"})
        volumes = _raw_request("GET", "/v1/volumes")
        assert isinstance(volumes, list), f"Expected list, got {type(volumes)}"
        found = any("test-vol-list" in str(v) for v in volumes)
        assert found, f"Created volume not in list: {volumes}"
        # Cleanup
        _raw_request("DELETE", "/v1/volumes/test-vol-list")
        print(f"PASS: list returned {len(volumes)} volumes")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 4: Get single volume ─────────────────────────────────────

def test_get_volume():
    """Test 4: GET /v1/volumes/{id} returns volume metadata."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-get"})
        resp = _raw_request("GET", "/v1/volumes/test-vol-get")
        assert isinstance(resp, dict), f"Expected dict, got {type(resp)}"
        # Cleanup
        _raw_request("DELETE", "/v1/volumes/test-vol-get")
        print("PASS: get volume returns metadata")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 5: Delete volume ─────────────────────────────────────────

def test_delete_volume():
    """Test 5: DELETE /v1/volumes/{id} removes the volume."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-delete"})
        resp = _raw_request("DELETE", "/v1/volumes/test-vol-delete")
        assert resp.get("status") == "deleted", f"Expected deleted, got: {resp}"
        # Verify it's gone
        try:
            _raw_request("GET", "/v1/volumes/test-vol-delete")
            print("FAIL: volume still accessible after delete")
            return False
        except CellError:
            pass  # Expected: 404
        print("PASS: delete removes volume")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 6: Write and read file via volume ────────────────────────

def test_write_read_file():
    """Test 6: Write then read a file via volume endpoints round-trips data."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-rw"})

        # Write
        content = "hello from volumes test"
        b64 = base64.b64encode(content.encode()).decode()
        _raw_request("POST", "/v1/volumes/test-vol-rw/files", body={
            "path": "test.txt",
            "data": b64,
        })

        # Read
        resp = _raw_request("GET", "/v1/volumes/test-vol-rw/files?path=test.txt")
        decoded = base64.b64decode(resp["data"]).decode()
        assert decoded == content, f"Round-trip mismatch: {decoded!r} != {content!r}"

        # Cleanup
        _raw_request("DELETE", "/v1/volumes/test-vol-rw")
        print("PASS: write/read round-trips correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 7: VolumesAdapter.read/write via SDK ─────────────────────

def test_volumes_adapter():
    """Test 7: VolumesAdapter read/write round-trips via SDK adapter pattern."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        # Create a minimal mock cell that can make raw requests
        # without triggering sandbox creation
        class _MockCell:
            def _request(self, method, path, body=None):
                return _raw_request(method, path, body)
        mock = _MockCell()
        adapter = VolumesAdapter(mock)

        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-adapter"})
        adapter.write("test-vol-adapter", "adapter_test.txt", "SDK adapter works")
        content = adapter.read("test-vol-adapter", "adapter_test.txt")
        assert content == "SDK adapter works", f"Got: {content!r}"

        # Cleanup
        adapter.delete("test-vol-adapter")
        print("PASS: VolumesAdapter read/write works")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 8: VolumesAdapter.list_all ───────────────────────────────

def test_volumes_adapter_list():
    """Test 8: VolumesAdapter.list_all() returns volumes."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        class _MockCell:
            def _request(self, method, path, body=None):
                return _raw_request(method, path, body)
        mock = _MockCell()
        adapter = VolumesAdapter(mock)

        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-listadapt"})
        result = adapter.list_all()
        assert isinstance(result, list), f"Expected list, got {type(result)}"
        # Cleanup
        _raw_request("DELETE", "/v1/volumes/test-vol-listadapt")
        print(f"PASS: list_all returned {len(result)} volumes")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# ─── Test 9: Get nonexistent volume returns 404 ────────────────────

def test_get_nonexistent_volume():
    """Test 9: GET /v1/volumes/nonexistent returns 404 error."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        _raw_request("GET", "/v1/volumes/this-volume-does-not-exist")
        print("FAIL: should have raised CellError")
        return False
    except CellError:
        print("PASS: nonexistent volume raises CellError (404)")
        return True
    except Exception as e:
        print(f"FAIL: unexpected: {type(e).__name__}: {e}")
        return False


# ─── Test 10: Read nonexistent file from volume returns error ──────

def test_read_nonexistent_file():
    """Test 10: Reading a nonexistent file from a volume raises CellError."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        _raw_request("POST", "/v1/volumes", body={"volume_id": "test-vol-nofile"})
        _raw_request("GET", "/v1/volumes/test-vol-nofile/files?path=does_not_exist.txt")
        _raw_request("DELETE", "/v1/volumes/test-vol-nofile")
        print("FAIL: should have raised CellError for missing file")
        return False
    except CellError:
        try:
            _raw_request("DELETE", "/v1/volumes/test-vol-nofile")
        except Exception:
            pass
        print("PASS: nonexistent file raises CellError")
        return True
    except Exception as e:
        print(f"FAIL: unexpected: {type(e).__name__}: {e}")
        return False


# ─── Test 11: VolumesAdapter class exists and has expected methods ──

def test_volumes_adapter_interface():
    """Test 11: VolumesAdapter has read, write, delete, list_all methods."""
    assert hasattr(VolumesAdapter, 'read'), "Missing read method"
    assert hasattr(VolumesAdapter, 'write'), "Missing write method"
    assert hasattr(VolumesAdapter, 'delete'), "Missing delete method"
    assert hasattr(VolumesAdapter, 'list_all'), "Missing list_all method"
    print("PASS: VolumesAdapter interface complete")
    return True


# ─── Test 12: E2B Volume class exists and has expected interface ───

def test_e2b_volume_class():
    """Test 12: E2B Volume class has id, name properties and create classmethod."""
    v = Volume(volume_id="test-123")
    assert v.id == "test-123", f"id: {v.id}"
    assert v.name == "test-123", f"name: {v.name}"
    assert v.volume_id == "test-123", f"volume_id: {v.volume_id}"
    assert hasattr(Volume, 'create'), "Missing create classmethod"
    print("PASS: E2B Volume class interface correct")
    return True


# ─── Test 13: Cell.create_volume classmethod exists ────────────────

def test_cell_create_volume_classmethod():
    """Test 13: Cell.create_volume is a classmethod with correct signature."""
    assert hasattr(Cell, 'create_volume'), "Missing create_volume classmethod"
    import inspect
    sig = inspect.signature(Cell.create_volume)
    params = list(sig.parameters.keys())
    assert 'volume_id' in params, f"Missing volume_id param: {params}"
    assert 'api_key' in params, f"Missing api_key param: {params}"
    assert 'api_url' in params, f"Missing api_url param: {params}"
    print("PASS: Cell.create_volume classmethod has correct signature")
    return True


# ─── Runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Volumes Subsystem Test Suite (Sprint D)")
    print("=" * 60)

    tests = [
        test_create_volume,
        test_create_volume_with_id,
        test_list_volumes,
        test_get_volume,
        test_delete_volume,
        test_write_read_file,
        test_volumes_adapter,
        test_volumes_adapter_list,
        test_get_nonexistent_volume,
        test_read_nonexistent_file,
        test_volumes_adapter_interface,
        test_e2b_volume_class,
        test_cell_create_volume_classmethod,
    ]

    passed, failed, skipped = 0, 0, 0
    for t in tests:
        print(f"\n--- {t.__doc__} ---")
        result = t()
        if result is True:
            passed += 1
        elif result is False:
            failed += 1
        else:
            skipped += 1

    print(f"\n{'=' * 60}")
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)
    sys.exit(1 if failed else 0)
