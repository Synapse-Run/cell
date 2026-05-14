#!/usr/bin/env python3
"""Tests for ROADMAP milestone 1.12 — Filesystem Completeness.

Covers: files.exists, files.get_info, files.remove, files.make_dir,
files.rename, upgraded files.list (EntryInfo), path-traversal security.

Run: python3 cell/sdk/tests/test_filesystem_completeness.py

Gateway-dependent tests (1-10) require a Cell API running at
http://127.0.0.1:8001. To start one:
  cd cell/gateway
  SYNAPSE_API_KEY=test CELL_PORT=8001 cargo run --release

Tests 11-12 always run (local PyO3 mode).
"""
import sys
import os

# Add parent directory so we can import synapse without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse.cell import Cell, CellError, EntryInfo
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


# --- Test 1: write then exists -----------------------------------------

def test_write_then_exists():
    """Test 1: write a file, assert files.exists returns True."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.write("/data/hello.txt", "world")
        assert sbx.files.exists("/data/hello.txt"), "file should exist after write"
        print("PASS: write then exists returns True")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 2: exists for nonexistent file --------------------------------

def test_exists_nonexistent():
    """Test 2: files.exists for nonexistent path returns False."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        result = sbx.files.exists("/data/no_such_file.txt")
        assert result is False, f"Expected False, got {result}"
        print("PASS: exists on nonexistent file returns False")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 3: write then get_info ----------------------------------------

def test_write_then_get_info():
    """Test 3: write 100 bytes, get_info returns EntryInfo with correct fields."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        payload = "x" * 100
        sbx.files.write("/data/info_test.txt", payload)
        info = sbx.files.get_info("/data/info_test.txt")
        assert isinstance(info, EntryInfo), f"Expected EntryInfo, got {type(info)}"
        assert info.size >= 100, f"Expected size >= 100, got {info.size}"
        assert info.name == "info_test.txt", f"Expected name 'info_test.txt', got {info.name!r}"
        assert info.type == "file", f"Expected type 'file', got {info.type!r}"
        print(f"PASS: get_info returns EntryInfo (size={info.size}, name={info.name!r})")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 4: make_dir then exists ---------------------------------------

def test_make_dir_then_exists():
    """Test 4: make_dir creates a directory that exists with type 'dir'."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.make_dir("/data/subdir")
        assert sbx.files.exists("/data/subdir"), "subdir should exist after make_dir"
        info = sbx.files.get_info("/data/subdir")
        assert info.type == "dir", f"Expected type 'dir', got {info.type!r}"
        print("PASS: make_dir creates dir, exists and type correct")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 5: write then remove ------------------------------------------

def test_write_then_remove():
    """Test 5: write, verify exists, remove, verify not exists."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.write("/data/to_delete.txt", "temporary")
        assert sbx.files.exists("/data/to_delete.txt"), "file should exist before remove"
        sbx.files.remove("/data/to_delete.txt")
        assert not sbx.files.exists("/data/to_delete.txt"), "file should not exist after remove"
        print("PASS: write then remove works correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 6: write then rename ------------------------------------------

def test_write_then_rename():
    """Test 6: write old.txt, rename to new.txt, verify old gone and new present."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.write("/data/old.txt", "rename me")
        result = sbx.files.rename("/data/old.txt", "/data/new.txt")
        assert isinstance(result, EntryInfo), f"Expected EntryInfo from rename, got {type(result)}"
        assert sbx.files.exists("/data/new.txt"), "new.txt should exist after rename"
        assert not sbx.files.exists("/data/old.txt"), "old.txt should not exist after rename"
        print("PASS: rename moves file correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 7: list returns EntryInfo objects -----------------------------

def test_list_returns_entry_info():
    """Test 7: write 2 files, list root, assert List[EntryInfo] with >= 2 entries."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.write("/data/list_a.txt", "aaa")
        sbx.files.write("/data/list_b.txt", "bbb")
        entries = sbx.files.list("/data")
        assert isinstance(entries, list), f"Expected list, got {type(entries)}"
        assert len(entries) >= 2, f"Expected >= 2 entries, got {len(entries)}"
        for entry in entries:
            assert isinstance(entry, EntryInfo), f"Expected EntryInfo, got {type(entry)}"
            assert hasattr(entry, "name") and entry.name, f"Entry missing name: {entry}"
            assert hasattr(entry, "type"), f"Entry missing type: {entry}"
        names = {e.name for e in entries}
        assert "list_a.txt" in names, f"list_a.txt not in {names}"
        assert "list_b.txt" in names, f"list_b.txt not in {names}"
        print(f"PASS: list returns {len(entries)} EntryInfo objects")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 8: path traversal — exists ------------------------------------

def test_path_traversal_exists():
    """Test 8: files.exists with ../../etc/passwd raises CellError (security)."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.exists("../../etc/passwd")
        print("FAIL: should have raised CellError for path traversal")
        return False
    except CellError:
        print("PASS: path traversal on exists raises CellError")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 9: path traversal — remove -----------------------------------

def test_path_traversal_remove():
    """Test 9: files.remove with ../../etc/passwd raises CellError (security)."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.remove("../../etc/passwd")
        print("FAIL: should have raised CellError for path traversal")
        return False
    except CellError:
        print("PASS: path traversal on remove raises CellError")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 10: path traversal — rename ----------------------------------

def test_path_traversal_rename():
    """Test 10: files.rename with ../../etc/passwd raises CellError (security)."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        sbx.files.rename("../../etc/passwd", "steal.txt")
        print("FAIL: should have raised CellError for path traversal")
        return False
    except CellError:
        print("PASS: path traversal on rename raises CellError")
        return True
    except Exception as e:
        print(f"FAIL: unexpected exception type: {type(e).__name__}: {e}")
        return False
    finally:
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass


# --- Test 11: local mode — file_exists ----------------------------------

def test_local_mode_file_exists():
    """Test 11: Cell(api_url='local').file_exists for nonexistent returns False."""
    try:
        cell = Cell(api_url="local")
        result = cell.file_exists("nonexistent_file.txt")
        assert result is False, f"Expected False, got {result}"
        cell.kill()
        print("PASS: local mode file_exists returns False for nonexistent")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# --- Test 12: local mode — make_dir + file_exists -----------------------

def test_local_mode_make_dir():
    """Test 12: Cell(api_url='local').make_dir then file_exists returns True."""
    cell = None
    try:
        cell = Cell(api_url="local")
        cell.make_dir("test_dir_local")
        result = cell.file_exists("test_dir_local")
        assert result is True, f"Expected True, got {result}"
        print("PASS: local mode make_dir then file_exists returns True")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        if cell:
            try:
                cell.kill()
            except Exception:
                pass


# --- Test 13: local mode — path traversal regression ----------------------

def test_local_path_traversal_rejected():
    """Test 13: local mode path traversal raises ValueError (regression for JC-010)."""
    try:
        from synapse.cell import Cell
        cell = Cell(api_url="local")
        attacks = [
            "../../../../../../etc/passwd",
            "/../../etc/passwd",
            "legit/../../etc/passwd",
            "..",
            "../..",
            "subdir/../../../../etc/passwd",
        ]
        for attack in attacks:
            try:
                cell.read_file(attack)
                print(f"FAIL: read traversal {attack!r} succeeded")
                return False
            except ValueError as e:
                msg = str(e).lower()
                if "traversal" not in msg and "escapes" not in msg:
                    print(f"FAIL: unexpected error for {attack!r}: {e}")
                    return False
        for attack in attacks[:3]:
            try:
                cell.write_file(attack, "pwn")
                print(f"FAIL: write traversal {attack!r} succeeded")
                return False
            except ValueError:
                pass
        print(f"PASS: local mode rejected {len(attacks) + 3} traversal patterns")
        return True
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return False


# --- Runner -------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Filesystem Completeness Test Suite (ROADMAP 1.12)")
    print("=" * 60)

    tests = [
        test_write_then_exists,
        test_exists_nonexistent,
        test_write_then_get_info,
        test_make_dir_then_exists,
        test_write_then_remove,
        test_write_then_rename,
        test_list_returns_entry_info,
        test_path_traversal_exists,
        test_path_traversal_remove,
        test_path_traversal_rename,
        test_local_mode_file_exists,
        test_local_mode_make_dir,
        test_local_path_traversal_rejected,
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
