#!/usr/bin/env python3
"""Tests for ROADMAP milestone 1.13 — Streaming + Background Commands.

Covers: Cell.run(on_stdout=...), e2b_compat.Sandbox.run_code(on_stdout=...),
Cell.command(on_stdout=...), Cell.command(background=True) + CommandHandle.

Run: python3 cell/sdk/tests/test_streaming_background.py

Gateway-dependent tests (1-5) require a Cell API running at
http://127.0.0.1:8001. To start one:
  cd cell/gateway
  SYNAPSE_API_KEY=test CELL_PORT=8001 cargo run --release

Test 6 always runs (local PyO3 mode post-hoc callbacks).
"""
import sys
import os

# Add parent directory so we can import synapse without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse.cell import Cell, CellResult, CommandHandle, CellError
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


# ─── Test 1: Cell.run with streaming callbacks ──────────────────

def test_run_with_streaming_callbacks():
    """Test 1: Cell.run(on_stdout=collector) receives lines in real-time."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    cell = None
    try:
        cell = Cell(api_url=GATEWAY)
        collector = []
        result = cell.run(
            "for i in range(3): print(i)",
            on_stdout=collector.append,
        )
        assert isinstance(result, CellResult), f"Expected CellResult, got {type(result)}"
        assert result.exit_code == 0, f"exit_code: {result.exit_code}"
        # Collector should have received lines "0", "1", "2"
        assert len(collector) == 3, f"Expected 3 callback items, got {len(collector)}: {collector}"
        assert collector == ["0", "1", "2"], f"Unexpected collector contents: {collector}"
        print("PASS: Cell.run streaming callbacks receive 3 lines")
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


# ─── Test 2: e2b_compat Sandbox.run_code streaming ──────────────

def test_e2b_run_code_streaming():
    """Test 2: Sandbox.run_code(on_stdout=collector) fires callbacks via SSE."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    sbx = None
    try:
        sbx = Sandbox(api_url=GATEWAY)
        collector = []
        result = sbx.run_code("print('hello')", on_stdout=collector.append)
        assert result.text.strip() == "hello", f"text: {result.text!r}"
        assert len(collector) == 1, f"Expected 1 callback item, got {len(collector)}: {collector}"
        assert collector[0] == "hello", f"Unexpected callback value: {collector[0]!r}"
        print("PASS: Sandbox.run_code streaming callback receives 'hello'")
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


# ─── Test 3: Cell.command with streaming callbacks ───────────────

def test_command_streaming():
    """Test 3: Cell.command(on_stdout=collector) fires callbacks."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    cell = None
    try:
        cell = Cell(api_url=GATEWAY)
        collector = []
        result = cell.command("echo hello", on_stdout=collector.append)
        assert isinstance(result, CellResult), f"Expected CellResult, got {type(result)}"
        assert result.exit_code == 0, f"exit_code: {result.exit_code}"
        assert len(collector) >= 1, f"Expected >= 1 callback items, got {len(collector)}: {collector}"
        assert "hello" in collector[0], f"Expected 'hello' in callback, got: {collector[0]!r}"
        print("PASS: Cell.command streaming callback receives 'hello'")
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


# ─── Test 4: Background command with CommandHandle ───────────────

def test_background_command():
    """Test 4: Cell.command(background=True) returns CommandHandle with output."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    cell = None
    try:
        cell = Cell(api_url=GATEWAY)
        handle = cell.command("echo done", background=True)
        assert isinstance(handle, CommandHandle), \
            f"Expected CommandHandle, got {type(handle)}"
        handle.wait()
        assert "done" in handle.stdout, \
            f"Expected 'done' in stdout, got: {handle.stdout!r}"
        assert handle.exit_code == 0, f"exit_code: {handle.exit_code}"
        print("PASS: background command returns CommandHandle with output")
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


# ─── Test 5: Background command status polling ───────────────────

def test_background_command_status():
    """Test 5: Background command is_running transitions to False after completion."""
    if not _gateway_alive():
        print("SKIP: gateway not available")
        return None
    cell = None
    try:
        cell = Cell(api_url=GATEWAY)
        handle = cell.command("echo status_check", background=True)
        assert isinstance(handle, CommandHandle), \
            f"Expected CommandHandle, got {type(handle)}"
        handle.wait(timeout_ms=10000)
        # After wait(), command should be finished
        assert not handle.is_running, \
            f"Expected is_running=False after wait, got True"
        assert handle.exit_code == 0, f"exit_code: {handle.exit_code}"
        print("PASS: background command status transitions correctly")
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


# ─── Test 6: Local mode callbacks (post-hoc, always runs) ───────

def test_local_mode_callbacks_posthoc():
    """Test 6: Local mode fires callbacks post-hoc after execution."""
    try:
        cell = Cell(api_url="local")
    except CellError:
        # PyO3 backend not built — skip gracefully
        print("SKIP: PyO3 backend not available (run maturin develop)")
        return None
    try:
        collector = []
        result = cell.run("print(42)", on_stdout=collector.append)
        assert isinstance(result, CellResult), f"Expected CellResult, got {type(result)}"
        assert result.exit_code == 0, f"exit_code: {result.exit_code}"
        # In local mode, callbacks fire post-hoc after execution
        assert len(collector) >= 1, \
            f"Expected >= 1 callback items, got {len(collector)}: {collector}"
        assert "42" in collector[0], \
            f"Expected '42' in callback, got: {collector[0]!r}"
        print("PASS: local mode post-hoc callbacks fire correctly")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False
    finally:
        try:
            cell.kill()
        except Exception:
            pass


# ─── Runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Streaming + Background Commands Test Suite (ROADMAP 1.13)")
    print("=" * 60)

    tests = [
        test_run_with_streaming_callbacks,
        test_e2b_run_code_streaming,
        test_command_streaming,
        test_background_command,
        test_background_command_status,
        test_local_mode_callbacks_posthoc,
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
