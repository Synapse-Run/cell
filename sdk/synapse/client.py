"""Synapse Python SDK for the current preview gateway surface."""

import base64
import json
import time
import urllib.request
import urllib.error


class ExecutionResult:
    """Result of a Synapse execution."""

    __slots__ = (
        "result", "stdout", "data", "arena_pos", "latency_ms", "assertions",
        "execution_id", "compile_time_ms", "wasm_size", "deterministic_hash", "cost_usd",
    )

    def __init__(self, result: int, stdout: str, arena_pos: int, latency_ms: float,
                 assertions=None, execution_id: str = "", compile_time_ms: float = 0,
                 wasm_size: int = 0, deterministic_hash: str = "", cost_usd: float = 0.0,
                 data: str = ""):
        self.result = result
        self.stdout = stdout
        self.data = data
        self.arena_pos = arena_pos
        self.latency_ms = latency_ms
        self.assertions = assertions
        self.execution_id = execution_id
        self.compile_time_ms = compile_time_ms
        self.wasm_size = wasm_size
        self.deterministic_hash = deterministic_hash
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return (
            f"ExecutionResult(result={self.result}, stdout={self.stdout!r}, "
            f"latency_ms={self.latency_ms}, execution_id={self.execution_id!r})"
        )


class AssertionError(Exception):
    """Raised when an @assert directive fails."""
    def __init__(self, expected, got):
        self.expected = expected
        self.got = got
        super().__init__(f"@assert failed: expected {expected}, got {got}")


class SynapseError(Exception):
    """Error returned by the Synapse API."""

    def __init__(self, status_code: int, error: str, error_type: str = ""):
        super().__init__(f"Synapse API error {status_code}: {error}")
        self.status_code = status_code
        self.error = error
        self.error_type = error_type


class Synapse:
    """Client for the current Synapse preview/self-hosted gateway.

    Args:
        api_key: Optional edge/API key if your deployment enforces one.
        base_url: Base URL of the Synapse gateway.
        timeout: Request timeout in seconds.
        max_retries: Number of retries with exponential backoff for transient errors.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.synapserun.dev",
        timeout: float = 15.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, endpoint: str, payload: dict) -> dict:
        """Send a request with retry logic. Returns parsed JSON body."""
        url = f"{self.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")

        last_error = None
        for attempt in range(self.max_retries + 1):
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    error_body = json.loads(e.read().decode("utf-8"))
                    error_msg = error_body.get("error", str(e))
                    error_type = error_body.get("error_type", "")
                except Exception:
                    error_msg = str(e)
                    error_type = ""
                # Don't retry client errors (4xx)
                if 400 <= e.code < 500:
                    raise SynapseError(e.code, error_msg, error_type) from e
                last_error = SynapseError(e.code, error_msg, error_type)
            except urllib.error.URLError as e:
                last_error = SynapseError(0, f"Connection failed: {e}")
            except Exception as e:
                last_error = SynapseError(0, str(e))

            # Exponential backoff for retryable errors
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt * 0.1, 5))

        raise last_error

    def _execute(self, payload: dict, endpoint: str = "/v1/execute") -> ExecutionResult:
        """Execute and parse the response into ExecutionResult."""
        body = self._request(endpoint, payload)

        if body.get("status") == "error":
            raise SynapseError(400, body.get("error", "unknown_error"),
                             body.get("error_type", ""))

        return ExecutionResult(
            result=body.get("result", 0),
            stdout=body.get("stdout", ""),
            data=body.get("data", ""),
            arena_pos=body.get("arena_pos", 0),
            latency_ms=body.get("latency_ms", 0),
            assertions=body.get("assertions"),
            execution_id=body.get("execution_id", ""),
            compile_time_ms=body.get("compile_time_ms", 0),
            wasm_size=body.get("wasm_size", 0),
            deterministic_hash=body.get("deterministic_hash", ""),
            cost_usd=body.get("cost_usd", 0.0),
        )

    def execute_syn(self, code: str) -> ExecutionResult:
        """Execute ``.syn`` source code on the native Wasm kernel."""
        return self._execute({"code": code})

    def execute_python(self, code: str) -> ExecutionResult:
        """Execute the currently supported restricted Python subset."""
        return self._execute({"code": code}, endpoint="/v1/execute/python")

    def execute_wasm(self, wasm_bytes: bytes) -> ExecutionResult:
        """Execute a pre-compiled ``.wasm`` binary on the native Wasm kernel."""
        if not wasm_bytes or wasm_bytes[:4] != b"\x00asm":
            raise ValueError("Invalid .wasm binary: missing magic bytes")
        encoded = base64.b64encode(wasm_bytes).decode("ascii")
        return self._execute({"wasm": encoded})

    def execute_syn_with_assert(self, code: str) -> ExecutionResult:
        """Execute ``.syn`` code and verify ``@assert`` directives.

        Raises AssertionError if any assertion fails.
        """
        result = self.execute_syn(code)
        if result.assertions:
            for a in result.assertions:
                if not a.get("pass"):
                    raise AssertionError(a.get("expected"), a.get("got"))
        return result

    def compute(self, intent: str) -> dict:
        """Deprecated: the computation API is not part of the current product surface."""
        raise NotImplementedError("compute() is not part of the current Synapse preview surface")

    def validate(self, code: str) -> dict:
        """Deprecated: /v1/validate is not part of the current product surface."""
        raise NotImplementedError("validate() is not part of the current Synapse preview surface")

    def execute_batch(self, jobs: list) -> dict:
        """Deprecated: batch execution is not part of the current product surface."""
        raise NotImplementedError("execute_batch() is not part of the current Synapse preview surface")

    def health(self) -> dict:
        """Check gateway health."""
        url = f"{self.base_url}/health"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}
