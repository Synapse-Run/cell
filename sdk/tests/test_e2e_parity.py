"""
E2E Parity Test Suite — tests every SDK method against the live gateway.
Covers: sandbox lifecycle, filesystem, env vars, metadata, timeout,
pause/resume, snapshots, processes, volumes, templates, and p99 latency.

Requires: gateway running on localhost:8002

Design: Uses a single shared cell per test class to avoid hitting the
10-cell EdgeCell limit. Each class creates one cell, runs all tests, kills it.
"""
import pytest
import time
import statistics
import requests

BASE = "http://localhost:8002"
HEADERS = {"Content-Type": "application/json"}


def api(method, path, body=None):
    """Raw HTTP call to the gateway."""
    url = f"{BASE}{path}"
    r = getattr(requests, method.lower())(url, json=body, headers=HEADERS, timeout=10)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text}


def create_cell():
    status, data = api("POST", "/v1/cells", {"template": "python3"})
    assert status in (200, 201), f"create_cell failed: {status} {data}"
    return data["cell_id"]


def kill_cell(cell_id):
    api("DELETE", f"/v1/cells/{cell_id}")


# ─── Sandbox Lifecycle ─────────────────────────────────────────────────

class TestSandboxLifecycle:
    def test_create_get_kill(self):
        cid = create_cell()
        assert cid and len(cid) > 8
        # Get info
        s, d = api("GET", f"/v1/cells/{cid}")
        assert s == 200
        # Kill
        s, _ = api("DELETE", f"/v1/cells/{cid}")
        assert s == 200

    def test_list_cells(self):
        cid = create_cell()
        s, data = api("GET", "/v1/cells")
        assert s == 200
        assert isinstance(data, list)
        ids = [c.get("cell_id") or c.get("id") for c in data]
        assert cid in ids
        kill_cell(cid)

    def test_is_running(self):
        cid = create_cell()
        s, d = api("GET", f"/v1/cells/{cid}/is_running")
        assert s == 200
        assert d.get("running") is True or d.get("is_running") is True
        kill_cell(cid)


# ─── Code Execution ───────────────────────────────────────────────────

class TestCodeExecution:
    def test_simple_eval(self):
        cid = create_cell()
        s, d = api("POST", f"/v1/cells/{cid}/exec", {"code": "print(21 * 2)"})
        assert s == 200
        out = d.get("stdout") or d.get("output") or ""
        assert "42" in out
        kill_cell(cid)

    def test_multiline_loop(self):
        cid = create_cell()
        code = "x = 0\nfor i in range(10):\n    x += i\nprint(x)"
        s, d = api("POST", f"/v1/cells/{cid}/exec", {"code": code})
        assert s == 200
        out = d.get("stdout") or d.get("output") or ""
        assert "45" in out
        kill_cell(cid)

    def test_error_handling(self):
        cid = create_cell()
        s, d = api("POST", f"/v1/cells/{cid}/exec", {"code": "1/0"})
        assert s == 200
        stderr = d.get("stderr") or d.get("error") or str(d)
        assert "ZeroDivision" in stderr or "error" in stderr.lower()
        kill_cell(cid)


# ─── Filesystem (all ops on one cell) ─────────────────────────────────

class TestFilesystem:
    @pytest.fixture(autouse=True)
    def cell(self):
        self.cid = create_cell()
        yield
        kill_cell(self.cid)

    def test_write_and_read(self):
        s, _ = api("POST", f"/v1/cells/{self.cid}/files",
                    {"path": "hello.txt", "content": "hello world"})
        assert s == 200
        s, d = api("GET", f"/v1/cells/{self.cid}/files?path=hello.txt")
        assert s == 200
        assert "hello" in (d.get("content") or "")

    def test_exists_true(self):
        api("POST", f"/v1/cells/{self.cid}/files",
            {"path": "ex.txt", "content": "x"})
        s, d = api("GET", f"/v1/cells/{self.cid}/files/exists?path=ex.txt")
        assert s == 200
        assert d.get("exists") is True

    def test_exists_false(self):
        s, d = api("GET", f"/v1/cells/{self.cid}/files/exists?path=nope.txt")
        assert s == 200
        assert d.get("exists") is False

    def test_file_info(self):
        api("POST", f"/v1/cells/{self.cid}/files",
            {"path": "info.txt", "content": "info"})
        s, d = api("GET", f"/v1/cells/{self.cid}/files/info?path=info.txt")
        assert s == 200

    def test_list_files(self):
        api("POST", f"/v1/cells/{self.cid}/files",
            {"path": "list.txt", "content": "x"})
        s, d = api("GET", f"/v1/cells/{self.cid}/files/list?path=.")
        assert s == 200

    def test_make_dir(self):
        s, _ = api("POST", f"/v1/cells/{self.cid}/files/mkdir",
                    {"path": "mydir"})
        assert s == 200

    def test_remove(self):
        api("POST", f"/v1/cells/{self.cid}/files",
            {"path": "rm.txt", "content": "gone"})
        s, _ = api("DELETE", f"/v1/cells/{self.cid}/files?path=rm.txt")
        assert s == 200
        s, d = api("GET", f"/v1/cells/{self.cid}/files/exists?path=rm.txt")
        assert d.get("exists") is False

    def test_rename(self):
        api("POST", f"/v1/cells/{self.cid}/files",
            {"path": "old.txt", "content": "ren"})
        s, _ = api("POST", f"/v1/cells/{self.cid}/files/rename",
                    {"old_path": "old.txt", "new_path": "new.txt"})
        assert s == 200


# ─── Env vars, Metadata, Timeout (shared cell) ───────────────────────

class TestCellManagement:
    @pytest.fixture(autouse=True)
    def cell(self):
        self.cid = create_cell()
        yield
        kill_cell(self.cid)

    def test_get_envs(self):
        s, d = api("GET", f"/v1/cells/{self.cid}/envs")
        assert s == 200
        assert isinstance(d, dict)

    def test_patch_envs(self):
        s, d = api("PATCH", f"/v1/cells/{self.cid}/envs",
                    {"MY_VAR": "hello"})
        assert s == 200

    def test_patch_metadata(self):
        s, d = api("PATCH", f"/v1/cells/{self.cid}/metadata",
                    {"project": "synapse"})
        assert s == 200

    def test_set_timeout(self):
        s, _ = api("PUT", f"/v1/cells/{self.cid}/timeout", {"timeout": 120})
        assert s == 200

    def test_refresh(self):
        s, _ = api("POST", f"/v1/cells/{self.cid}/refresh")
        assert s == 200


# ─── Pause / Resume / Snapshots ──────────────────────────────────────

class TestPauseResumeSnapshots:
    @pytest.fixture(autouse=True)
    def cell(self):
        self.cid = create_cell()
        yield
        kill_cell(self.cid)

    def test_pause_resume(self):
        s, _ = api("POST", f"/v1/cells/{self.cid}/pause")
        assert s == 200
        s, _ = api("POST", f"/v1/cells/{self.cid}/resume")
        assert s == 200

    def test_snapshot_and_list(self):
        s, _ = api("POST", f"/v1/cells/{self.cid}/snapshot")
        assert s == 200
        s, d = api("GET", f"/v1/cells/{self.cid}/snapshots")
        assert s == 200
        assert isinstance(d, list)


# ─── Background Processes ────────────────────────────────────────────

class TestProcesses:
    @pytest.fixture(autouse=True)
    def cell(self):
        self.cid = create_cell()
        yield
        kill_cell(self.cid)

    def test_start_and_list(self):
        s, d = api("POST", f"/v1/cells/{self.cid}/cmd",
                    {"command": "echo hello"})
        assert s == 200
        s, procs = api("GET", f"/v1/cells/{self.cid}/processes")
        assert s == 200

    def test_process_output(self):
        s, d = api("POST", f"/v1/cells/{self.cid}/cmd",
                    {"command": "echo test_output"})
        assert s == 200
        out = d.get("stdout") or d.get("output") or str(d)
        assert "test_output" in out


# ─── Templates ────────────────────────────────────────────────────────

class TestTemplates:
    def test_list(self):
        s, d = api("GET", "/v1/templates")
        assert s == 200
        assert isinstance(d, list)


# ─── Volumes ──────────────────────────────────────────────────────────

class TestVolumes:
    def test_lifecycle(self):
        # Create
        s, d = api("POST", "/v1/volumes", {"name": "e2e-vol"})
        assert s in (200, 201)
        vid = d.get("volume_id") or d.get("id")
        assert vid
        # Write file
        s, _ = api("POST", f"/v1/volumes/{vid}/files",
                    {"path": "test.txt", "data": "volume_data"})
        assert s == 200
        # Read back
        s, d = api("GET", f"/v1/volumes/{vid}/files?path=test.txt")
        assert s == 200
        # List
        s, d = api("GET", "/v1/volumes")
        assert s == 200
        # Delete
        s, _ = api("DELETE", f"/v1/volumes/{vid}")
        assert s == 200


# ─── Health / Stats / Metrics ─────────────────────────────────────────

class TestHealth:
    def test_health(self):
        s, d = api("GET", "/v1/health")
        assert s == 200
        assert d.get("status") == "ok"

    def test_stats(self):
        s, _ = api("GET", "/v1/stats")
        assert s == 200

    def test_metrics(self):
        s, _ = api("GET", "/v1/metrics")
        assert s == 200


# ─── P99 Latency Benchmark ───────────────────────────────────────────

class TestP99Latency:
    def test_syn_fast_path_p99(self):
        """100 runs of simple arithmetic — measures .syn transpile path."""
        cid = create_cell()
        latencies = []
        # Warmup
        for _ in range(5):
            api("POST", f"/v1/cells/{cid}/exec", {"code": "print(1+1)"})
        # Measure
        for _ in range(100):
            t0 = time.perf_counter()
            s, d = api("POST", f"/v1/cells/{cid}/exec", {"code": "print(21*2)"})
            t1 = time.perf_counter()
            assert s == 200
            latencies.append((t1 - t0) * 1000)

        kill_cell(cid)
        latencies.sort()
        p50, p95, p99 = latencies[49], latencies[94], latencies[98]

        print(f"\n{'='*60}")
        print("  .syn FAST PATH  (100 runs, 5 warmup)")
        print(f"  p50  = {p50:.3f} ms")
        print(f"  p95  = {p95:.3f} ms")
        print(f"  p99  = {p99:.3f} ms")
        print(f"  mean = {statistics.mean(latencies):.3f} ms")
        print(f"  min  = {min(latencies):.3f} ms")
        print(f"  max  = {max(latencies):.3f} ms")
        print(f"{'='*60}")
        # Via HTTP, p99 includes network round-trip (~5-15ms on localhost)
        # The .syn transpile itself is <1ms; this measures end-to-end
        assert p99 < 100.0, f"p99={p99:.3f}ms exceeds 100ms threshold"

    def test_cpython_wasi_p99(self):
        """20 runs forcing CPython-WASI fallback."""
        cid = create_cell()
        latencies = []
        code = "import json; print(json.dumps({'r': sum(range(100))}))"
        # Warmup
        for _ in range(3):
            api("POST", f"/v1/cells/{cid}/exec", {"code": code})
        # Measure
        for _ in range(20):
            t0 = time.perf_counter()
            s, d = api("POST", f"/v1/cells/{cid}/exec", {"code": code})
            t1 = time.perf_counter()
            assert s == 200
            latencies.append((t1 - t0) * 1000)

        kill_cell(cid)
        latencies.sort()
        p50 = latencies[9]
        p95 = latencies[18]

        print(f"\n{'='*60}")
        print("  CPython-WASI FALLBACK  (20 runs, 3 warmup)")
        print(f"  p50  = {p50:.3f} ms")
        print(f"  p95  = {p95:.3f} ms")
        print(f"  mean = {statistics.mean(latencies):.3f} ms")
        print(f"{'='*60}")


# ─── Batch File Write (Phase A6) ─────────────────────────────────

class TestBatchFileWrite:
    @pytest.fixture(autouse=True)
    def cell(self):
        self.cid = create_cell()
        yield
        kill_cell(self.cid)

    def test_batch_write_basic(self):
        files = [
            {"path": "a.txt", "content": "alpha"},
            {"path": "b.txt", "content": "bravo"},
            {"path": "c.txt", "content": "charlie"},
        ]
        s, d = api("POST", f"/v1/cells/{self.cid}/files/batch",
                    {"files": files})
        assert s == 200
        assert d["written"] == 3
        assert d["total"] == 3
        assert d["errors"] == []

    def test_batch_write_read_back(self):
        files = [
            {"path": "test1.py", "content": "print(1)"},
            {"path": "test2.py", "content": "print(2)"},
        ]
        api("POST", f"/v1/cells/{self.cid}/files/batch", {"files": files})
        # Read back first file
        s, d = api("GET", f"/v1/cells/{self.cid}/files?path=test1.py")
        assert s == 200
        assert "print(1)" in (d.get("content") or "")

    def test_batch_write_with_dirs(self):
        files = [
            {"path": "src/main.py", "content": "main()"},
            {"path": "src/utils.py", "content": "def util(): pass"},
        ]
        s, d = api("POST", f"/v1/cells/{self.cid}/files/batch",
                    {"files": files})
        assert s == 200
        assert d["written"] == 2

    def test_batch_write_max_limit(self):
        """Reject batches exceeding 100 files."""
        files = [{"path": f"f{i}.txt", "content": "x"} for i in range(101)]
        s, d = api("POST", f"/v1/cells/{self.cid}/files/batch",
                    {"files": files})
        assert s == 400


# ─── Code Contexts (Phase A2) ───────────────────────────────────

class TestCodeContexts:
    @pytest.fixture(autouse=True)
    def cell(self):
        # Persistent cell required for contexts
        s, d = api("POST", "/v1/cells", {"template": "python3",
                                          "persistent": True})
        assert s in (200, 201)
        self.cid = d["cell_id"]
        yield
        kill_cell(self.cid)

    def test_create_context(self):
        s, d = api("POST", f"/v1/cells/{self.cid}/contexts",
                    {"name": "analysis"})
        assert s == 200
        assert "context_id" in d
        assert d["name"] == "analysis"

    def test_list_contexts(self):
        api("POST", f"/v1/cells/{self.cid}/contexts", {"name": "ctx1"})
        api("POST", f"/v1/cells/{self.cid}/contexts", {"name": "ctx2"})
        s, d = api("GET", f"/v1/cells/{self.cid}/contexts")
        assert s == 200
        assert isinstance(d, list)
        assert len(d) >= 2

    def test_exec_in_context(self):
        s, ctx = api("POST", f"/v1/cells/{self.cid}/contexts",
                      {"name": "test"})
        assert s == 200
        ctx_id = ctx["context_id"]
        # Set variable in context
        s, d = api("POST", f"/v1/cells/{self.cid}/contexts/{ctx_id}/exec",
                    {"code": "x = 42; print(x)"})
        assert s == 200
        out = d.get("stdout") or d.get("output") or ""
        assert "42" in out

    def test_delete_context(self):
        s, ctx = api("POST", f"/v1/cells/{self.cid}/contexts",
                      {"name": "deleteme"})
        ctx_id = ctx["context_id"]
        s, d = api("DELETE", f"/v1/cells/{self.cid}/contexts/{ctx_id}")
        assert s == 200
        assert d.get("status") == "deleted"


# ─── Per-Cell Metrics (Phase A — Milestone 2.14) ────────────────

class TestCellMetrics:
    @pytest.fixture(autouse=True)
    def cell(self):
        self.cid = create_cell()
        yield
        kill_cell(self.cid)

    def test_get_metrics(self):
        s, d = api("GET", f"/v1/cells/{self.cid}/metrics")
        assert s == 200
        assert "executions" in d
        assert "uptime_ms" in d
        assert "idle_ms" in d

    def test_metrics_after_exec(self):
        # Run something first
        api("POST", f"/v1/cells/{self.cid}/exec", {"code": "print(1)"})
        s, d = api("GET", f"/v1/cells/{self.cid}/metrics")
        assert s == 200
        assert d["executions"] >= 1

    def test_metrics_not_found(self):
        s, d = api("GET", "/v1/cells/nonexistent-id/metrics")
        assert s == 404
