#!/usr/bin/env python3
"""Tests for ROADMAP milestone 1.11 — Sandbox Lifecycle Batch A.

Covers: Sandbox.create(full params), sandbox.get_info(), Sandbox.connect,
Sandbox.list(query, limit, next_token), SandboxPaginator.

Run: python3 cell/sdk/tests/test_lifecycle_batch_a.py

Gateway-dependent tests (1-12) require a Cell API running at
http://127.0.0.1:8001. To start one:
  cd cell/gateway
  SYNAPSE_API_KEY=test CELL_PORT=8001 cargo run --release

Tests 13-14 always run (local-mode rejection checks).
"""
import sys
import os
import time

# Add parent directory so we can import synapse without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse.cell import Cell, SandboxInfo, SandboxState, SandboxQuery, SandboxPaginator, CellError
from synapse.e2b_compat import Sandbox

GATEWAY = os.environ.get("CELL_GATEWAY_URL", "http://127.0.0.1:8001")


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


# ─── Test 1: Sandbox.create with metadata and envs ─────────────────

def test_create_with_metadata_and_envs():
    """Test 1: Sandbox.create with metadata and envs round-trips via get_info."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    try:
        s = Sandbox(
            api_url=GATEWAY,
            metadata={"owner": "alice", "env": "prod"},
            envs={"FOO": "bar"},
        )
        info = s.get_info()
        assert isinstance(info, SandboxInfo), f"Expected SandboxInfo, got {type(info)}"
        assert info.sandbox_id == s.id, f"sandbox_id mismatch: {info.sandbox_id} != {s.id}"
        assert info.template_id == "python3", f"template_id: {info.template_id}"
        assert info.metadata == {"owner": "alice", "env": "prod"}, f"metadata: {info.metadata}"
        print("PASS: create with metadata/envs round-trips correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if s:
            try:
                s.kill()
            except Exception:
                pass


# ─── Test 2: Sandbox.create with network ───────────────────────────

def test_create_with_network():
    """Test 2: Sandbox.create with network config round-trips via get_info."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    try:
        s = Sandbox(
            api_url=GATEWAY,
            network={"deny_out": ["1.2.3.4"]},
        )
        info = s.get_info()
        assert info.network == {"deny_out": ["1.2.3.4"]}, f"network: {info.network}"
        print("PASS: create with network round-trips correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if s:
            try:
                s.kill()
            except Exception:
                pass


# ─── Test 3: Sandbox.create with lifecycle ─────────────────────────

def test_create_with_lifecycle():
    """Test 3: Sandbox.create with lifecycle config round-trips via get_info."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    try:
        s = Sandbox(
            api_url=GATEWAY,
            lifecycle={"on_timeout": "pause", "auto_resume": True},
        )
        info = s.get_info()
        assert info.lifecycle == {"on_timeout": "pause", "auto_resume": True}, \
            f"lifecycle: {info.lifecycle}"
        print("PASS: create with lifecycle round-trips correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if s:
            try:
                s.kill()
            except Exception:
                pass


# ─── Test 4: Sandbox.create with volume_mounts ────────────────────

def test_create_with_volume_mounts():
    """Test 4: Sandbox.create with volume_mounts round-trips via get_info."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    try:
        s = Sandbox(
            api_url=GATEWAY,
            volume_mounts={"/data/foo": "vol_abc"},
        )
        info = s.get_info()
        # Gateway returns list-of-dicts shape: [{"path": "/data/foo", "name": "vol_abc"}]
        assert len(info.volume_mounts) >= 1, f"volume_mounts empty: {info.volume_mounts}"
        mount = info.volume_mounts[0]
        assert mount.get("path") == "/data/foo", f"mount path: {mount}"
        assert mount.get("name") == "vol_abc", f"mount name: {mount}"
        print("PASS: create with volume_mounts round-trips correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if s:
            try:
                s.kill()
            except Exception:
                pass


# ─── Test 5: get_info returns fully typed SandboxInfo ──────────────

def test_get_info_returns_sandbox_info():
    """Test 5: get_info returns SandboxInfo with all expected fields typed."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    try:
        s = Sandbox(api_url=GATEWAY)
        info = s.get_info()
        assert isinstance(info, SandboxInfo), f"type: {type(info)}"
        assert isinstance(info.sandbox_id, str) and len(info.sandbox_id) > 0, \
            f"sandbox_id: {info.sandbox_id}"
        assert isinstance(info.template_id, str), f"template_id type: {type(info.template_id)}"
        assert isinstance(info.state, SandboxState), f"state type: {type(info.state)}"
        from datetime import datetime
        assert isinstance(info.started_at, datetime), f"started_at type: {type(info.started_at)}"
        assert isinstance(info.end_at, datetime), f"end_at type: {type(info.end_at)}"
        assert isinstance(info.metadata, dict), f"metadata type: {type(info.metadata)}"
        print("PASS: get_info returns fully typed SandboxInfo")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if s:
            try:
                s.kill()
            except Exception:
                pass


# ─── Test 6: Static Sandbox.get_info_for ───────────────────────────

def test_static_get_info_for():
    """Test 6: Sandbox.get_info_for(sid) returns SandboxInfo with correct ID."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    try:
        s = Sandbox(api_url=GATEWAY)
        info = Sandbox.get_info_for(s.id, api_url=GATEWAY)
        assert isinstance(info, SandboxInfo), f"type: {type(info)}"
        assert info.sandbox_id == s.id, f"sandbox_id mismatch: {info.sandbox_id} != {s.id}"
        print("PASS: Sandbox.get_info_for returns correct SandboxInfo")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if s:
            try:
                s.kill()
            except Exception:
                pass


# ─── Test 7: get_info_for nonexistent raises CellError ─────────────

def test_get_info_nonexistent():
    """Test 7: Sandbox.get_info_for with bogus ID raises CellError."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        Sandbox.get_info_for("00000000-0000-0000-0000-000000000000", api_url=GATEWAY)
        print("FAIL: should have raised CellError")
        return False
    except CellError:
        print("PASS: get_info_for nonexistent raises CellError")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False


# ─── Test 8: Sandbox.connect round-trip (shared state) ─────────────

def test_connect_roundtrip():
    """Test 8: Sandbox.connect shares persistent session state."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    s = None
    s2 = None
    try:
        s = Sandbox(api_url=GATEWAY)
        s.run_code("x = 1")
        s2 = Sandbox.connect(s.id, api_url=GATEWAY)
        r = s2.run_code("print(x * 2)")
        assert r.text.strip() == "2", f"Expected '2', got {r.text!r}"
        print("PASS: connect round-trip shares state")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        for sbx in (s, s2):
            if sbx:
                try:
                    sbx.kill()
                except Exception:
                    pass


# ─── Test 9: Sandbox.connect nonexistent raises ────────────────────

def test_connect_nonexistent_raises():
    """Test 9: Sandbox.connect with bogus ID raises CellError."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    try:
        Sandbox.connect("00000000-0000-0000-0000-000000000000", api_url=GATEWAY)
        print("FAIL: should have raised CellError")
        return False
    except CellError:
        print("PASS: connect nonexistent raises CellError")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False


# ─── Test 10: Sandbox.list returns paginator with items ────────────

def test_list_returns_paginator():
    """Test 10: Sandbox.list returns SandboxPaginator with SandboxInfo items."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sandboxes = []
    try:
        for _ in range(3):
            sandboxes.append(Sandbox(api_url=GATEWAY))
        pag = Sandbox.list(api_url=GATEWAY, limit=10)
        assert isinstance(pag, SandboxPaginator), f"type: {type(pag)}"
        assert pag.has_next is True, "has_next should be True before first fetch"
        items = pag.next_items()
        assert len(items) >= 3, f"Expected >= 3 items, got {len(items)}"
        assert all(isinstance(i, SandboxInfo) for i in items), \
            f"Not all items are SandboxInfo: {[type(i) for i in items]}"
        print(f"PASS: list returns paginator with {len(items)} items")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        for sbx in sandboxes:
            try:
                sbx.kill()
            except Exception:
                pass


# ─── Test 11: Sandbox.list pagination (multi-page) ─────────────────

def test_list_pagination():
    """Test 11: Sandbox.list paginates correctly with limit=2 over 5 items."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sandboxes = []
    try:
        for _ in range(5):
            sandboxes.append(Sandbox(
                api_url=GATEWAY,
                metadata={"test": "pagination"},
            ))
        pag = Sandbox.list(
            limit=2,
            query=SandboxQuery(metadata={"test": "pagination"}),
            api_url=GATEWAY,
        )
        page1 = pag.next_items()
        assert len(page1) == 2, f"page1: expected 2, got {len(page1)}"
        assert pag.has_next, "should have more pages after page1"

        page2 = pag.next_items()
        assert len(page2) == 2, f"page2: expected 2, got {len(page2)}"

        page3 = pag.next_items()
        assert len(page3) == 1, f"page3: expected 1, got {len(page3)}"
        assert not pag.has_next, "should be exhausted after page3"

        print("PASS: pagination 2+2+1 over 5 items")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        for sbx in sandboxes:
            try:
                sbx.kill()
            except Exception:
                pass


# ─── Test 12: Sandbox.list metadata filter ─────────────────────────

def test_list_metadata_filter():
    """Test 12: Sandbox.list filters by metadata correctly."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sandboxes = []
    try:
        for _ in range(2):
            sandboxes.append(Sandbox(
                api_url=GATEWAY,
                metadata={"owner": "alice"},
            ))
        sandboxes.append(Sandbox(
            api_url=GATEWAY,
            metadata={"owner": "bob"},
        ))

        pag = Sandbox.list(
            query=SandboxQuery(metadata={"owner": "alice"}),
            limit=10,
            api_url=GATEWAY,
        )
        items = pag.next_items()
        assert len(items) == 2, f"Expected 2 alice items, got {len(items)}"
        assert all(
            i.metadata.get("owner") == "alice" for i in items
        ), f"Not all items owned by alice: {[i.metadata for i in items]}"
        print("PASS: list metadata filter returns only matching items")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        for sbx in sandboxes:
            try:
                sbx.kill()
            except Exception:
                pass


# ─── Test 13: Cell.list rejects local mode ─────────────────────────

def test_list_not_supported_in_local_mode():
    """Test 13: Cell.list(api_url='local') raises CellError."""
    try:
        Cell.list(api_url="local")
        print("FAIL: should have raised CellError")
        return False
    except CellError:
        print("PASS: Cell.list rejects local mode")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False


# ─── Test 14: Cell.connect rejects local mode ─────────────────────

def test_connect_not_supported_in_local_mode():
    """Test 14: Cell.connect(api_url='local') raises CellError."""
    try:
        Cell.connect("fake-id", api_url="local")
        print("FAIL: should have raised CellError")
        return False
    except CellError:
        print("PASS: Cell.connect rejects local mode")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False


# ─── Runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Lifecycle Batch A Test Suite (ROADMAP 1.11)")
    print("=" * 60)

    tests = [
        test_create_with_metadata_and_envs,
        test_create_with_network,
        test_create_with_lifecycle,
        test_create_with_volume_mounts,
        test_get_info_returns_sandbox_info,
        test_static_get_info_for,
        test_get_info_nonexistent,
        test_connect_roundtrip,
        test_connect_nonexistent_raises,
        test_list_returns_paginator,
        test_list_pagination,
        test_list_metadata_filter,
        test_list_not_supported_in_local_mode,
        test_connect_not_supported_in_local_mode,
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
