"""
syn_executor.py — Local .syn execution without a server.

Compiles .syn to .wasm using sync_v4.py, then executes via wasmtime (if installed)
or falls back to the Cell API. Enables offline .syn development.

Usage:
    from synapse.syn_executor import execute_syn

    result = execute_syn("@f 0 main [ + 21 21 ]")
    print(result)  # 42
"""
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class SynResult:
    """Result of local .syn execution."""
    value: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    compile_ms: float = 0.0
    execute_ms: float = 0.0
    wasm_size: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        status = "✓" if self.ok else "✗"
        return f"SynResult({status} value={self.value} compile={self.compile_ms:.1f}ms exec={self.execute_ms:.1f}ms)"


def _find_sync_v4() -> str:
    """Find the sync_v4.py compiler."""
    candidates = [
        os.path.join(os.path.dirname(__file__), '..', '..', 'tools', 'sync_v4.py'),
        os.path.join(os.path.dirname(__file__), '..', 'tools', 'sync_v4.py'),
    ]
    # Also check SYNAPSE_ROOT env
    root = os.environ.get('SYNAPSE_ROOT')
    if root:
        candidates.insert(0, os.path.join(root, 'tools', 'sync_v4.py'))

    for c in candidates:
        p = os.path.abspath(c)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "Could not find sync_v4.py. Set SYNAPSE_ROOT or run from the synapse repo."
    )


def compile_syn(code: str) -> tuple:
    """Compile .syn source to .wasm binary.

    Args:
        code: .syn source code

    Returns:
        (wasm_path, compile_ms, wasm_size) on success
        Raises RuntimeError on failure.
    """
    compiler = _find_sync_v4()

    with tempfile.NamedTemporaryFile(suffix='.syn', mode='w', delete=False) as f:
        f.write(code)
        syn_path = f.name

    wasm_path = syn_path.replace('.syn', '.wasm')

    try:
        t0 = time.monotonic()
        result = subprocess.run(
            ['python3', compiler, syn_path, wasm_path],
            capture_output=True, text=True, timeout=10
        )
        compile_ms = (time.monotonic() - t0) * 1000

        if result.returncode != 0:
            os.unlink(syn_path)
            raise RuntimeError(f"Compilation failed: {result.stderr.strip()}")

        wasm_size = os.path.getsize(wasm_path) if os.path.exists(wasm_path) else 0
        if wasm_size == 0:
            os.unlink(syn_path)
            raise RuntimeError("Compilation produced empty .wasm")

        os.unlink(syn_path)
        return wasm_path, compile_ms, wasm_size

    except subprocess.TimeoutExpired:
        os.unlink(syn_path)
        raise RuntimeError("Compilation timed out")


def execute_syn(
    code: str,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> SynResult:
    """Compile and execute .syn code locally.

    Tries local wasmtime execution first. Falls back to the Cell API
    if wasmtime is not installed and api_url is provided.

    Args:
        code: .syn source code
        api_url: Cell API URL for remote fallback
        api_key: Cell API key for remote fallback

    Returns:
        SynResult with value, timing, and diagnostics

    Example:
        >>> result = execute_syn("@f 0 main [ + 21 21 ]")
        >>> print(result.value)  # 42
    """
    # Compile
    try:
        wasm_path, compile_ms, wasm_size = compile_syn(code)
    except (RuntimeError, FileNotFoundError) as e:
        return SynResult(stderr=str(e), exit_code=1)

    # Try local wasmtime execution
    try:
        t0 = time.monotonic()
        result = subprocess.run(
            ['wasmtime', 'run', '--invoke', 'main', wasm_path],
            capture_output=True, text=True, timeout=10
        )
        execute_ms = (time.monotonic() - t0) * 1000

        os.unlink(wasm_path)

        if result.returncode == 0:
            stdout = result.stdout.strip()
            try:
                value = int(stdout)
            except ValueError:
                value = None
            return SynResult(
                value=value, stdout=stdout, stderr=result.stderr,
                exit_code=0, compile_ms=compile_ms,
                execute_ms=execute_ms, wasm_size=wasm_size
            )
        else:
            return SynResult(
                stderr=result.stderr.strip(), exit_code=result.returncode,
                compile_ms=compile_ms, execute_ms=execute_ms,
                wasm_size=wasm_size
            )

    except FileNotFoundError:
        # wasmtime not installed — try remote
        os.unlink(wasm_path)

        if api_url:
            try:
                from synapse.cell import run_syn as remote_run_syn
                cell_result = remote_run_syn(code, api_key=api_key, api_url=api_url)
                try:
                    value = int(cell_result.stdout.strip())
                except (ValueError, AttributeError):
                    value = None
                return SynResult(
                    value=value, stdout=cell_result.stdout,
                    stderr=cell_result.stderr, exit_code=cell_result.exit_code,
                    compile_ms=compile_ms, execute_ms=cell_result.latency_ms,
                    wasm_size=wasm_size
                )
            except Exception as e:
                return SynResult(stderr=f"Remote fallback failed: {e}", exit_code=1)

        return SynResult(
            stderr="wasmtime not found. Install: curl https://wasmtime.dev/install.sh -sSf | bash",
            exit_code=1, compile_ms=compile_ms, wasm_size=wasm_size
        )

    except subprocess.TimeoutExpired:
        os.unlink(wasm_path)
        return SynResult(
            stderr="Execution timed out", exit_code=1,
            compile_ms=compile_ms, wasm_size=wasm_size
        )
