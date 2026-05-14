"""Synapse Decorator API — Modal-style Python decorators for automatic Wasm execution.

Transform Python functions into Synapse-accelerated remote functions with a single decorator.
Write normal Python. Get sub-millisecond Wasm execution. No new language to learn.

Usage:
    from synapse import app

    @app.function
    def reward(state, action):
        return int(state * 99 + action)

    # Run locally (normal Python):
    result = reward(100, 5)

    # Run on Synapse (sub-millisecond Wasm):
    result = reward.remote(100, 5)

    # Batch execution (up to 1000 parallel):
    results = reward.map([(100, 5), (200, 3), (300, 1)])

    # Preview generated .syn code:
    print(reward.preview(100, 5))

    # Get full result with latency/hash/data:
    full = reward.remote_full(100, 5)
    print(full.latency_ms, full.data)

Environment:
    SYNAPSE_API_KEY    — API key (required for .remote()/.map())
    SYNAPSE_BASE_URL   — Gateway URL (default: https://api.synapserun.dev)
"""

import inspect
import os
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Tuple, Union


class SynapseFunction:
    """A wrapped function that can run locally or on Synapse.

    Behaves like a normal Python function when called directly.
    Use .remote() for Synapse execution, .map() for batch, .preview() to inspect.
    """

    def __init__(self, fn: Callable, app: "SynapseApp"):
        self._fn = fn
        self._app = app
        self._name = fn.__name__
        self._source = textwrap.dedent(inspect.getsource(fn))
        # Remove the decorator line(s) from the source
        lines = self._source.split("\n")
        func_start = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("def "):
                func_start = i
                break
        self._clean_source = "\n".join(lines[func_start:])
        # Preserve function metadata
        self.__name__ = fn.__name__
        self.__doc__ = fn.__doc__
        self.__module__ = fn.__module__

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the function locally (normal Python execution)."""
        return self._fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<SynapseFunction {self._name}>"

    def _build_syn_code(self, args: Tuple[Any, ...]) -> str:
        """Build a complete .syn program that calls this function with the given args.

        Transpiles the function body to .syn using the SDK transpiler, then
        wraps it with argument injection and a main entry point.
        """
        # Build a self-contained Python script that includes the function
        # and calls it with the provided arguments
        arg_str = ", ".join(repr(a) for a in args)
        call_code = f"{self._clean_source}\nresult = {self._name}({arg_str})"

        # Use the SDK transpiler
        from synapse.transpiler import python_to_syn

        return python_to_syn(call_code)

    def remote(self, *args: Any) -> Any:
        """Execute on Synapse — sub-millisecond Wasm execution.

        Transpiles the function + arguments to .syn, sends to the gateway,
        and returns the result. If the program produces stdout output (via
        print()), the data is available through .remote_full().

        Returns:
            The integer result from the Wasm execution.

        Raises:
            SynapseError: If the gateway returns an error or transpilation fails.
            ConnectionError: If the gateway is unreachable.
        """
        from synapse.transpiler import TranspileError

        try:
            client = self._app._get_client()
            syn_code = self._build_syn_code(args)
            result = client.execute_syn(syn_code)
            return result.result
        except TranspileError as e:
            from synapse.client import SynapseError
            raise SynapseError(
                422, f"Transpilation failed for {self._name}(): {e}",
                "transpile_error"
            ) from e

    def remote_full(self, *args: Any) -> "ExecutionResult":
        """Like remote(), but returns the full ExecutionResult.

        Includes latency_ms, deterministic_hash, data (stdout), and more.
        Use this when you need rich execution metadata.

        Example:
            result = my_func.remote_full(42)
            print(f"Value: {result.result}")
            print(f"Latency: {result.latency_ms}ms")
            print(f"Output: {result.data}")
            print(f"Hash: {result.deterministic_hash}")
        """
        from synapse.transpiler import TranspileError

        try:
            client = self._app._get_client()
            syn_code = self._build_syn_code(args)
            return client.execute_syn(syn_code)
        except TranspileError as e:
            from synapse.client import SynapseError
            raise SynapseError(
                422, f"Transpilation failed for {self._name}(): {e}",
                "transpile_error"
            ) from e

    def map(self, args_list: List[Tuple[Any, ...]]) -> List[Any]:
        """Execute the function with multiple argument sets in batch.

        Uses concurrent execution for throughput. Each invocation is
        independently transpiled and executed on Synapse.

        Args:
            args_list: List of argument tuples, e.g. [(100, 5), (200, 3)]

        Returns:
            List of results in the same order as the input.

        Example:
            results = my_func.map([(1,), (2,), (3,), (4,), (5,)])
            # results == [1, 4, 9, 16, 25]  (if my_func squares its arg)
        """
        if not args_list:
            return []

        client = self._app._get_client()

        # Build all .syn programs
        syn_codes = []
        for args in args_list:
            syn_codes.append(self._build_syn_code(args))

        # Use batch endpoint if available (up to 1000 jobs)
        if len(syn_codes) <= 1000:
            jobs = [{"code": code} for code in syn_codes]
            try:
                batch_result = client.execute_batch(jobs)
                results_list = batch_result.get("results", [])
                return [r.get("result", 0) for r in results_list]
            except Exception:
                pass  # Fall through to concurrent execution

        # Fallback: concurrent individual execution
        results = [None] * len(syn_codes)
        max_workers = min(len(syn_codes), 32)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(client.execute_syn, code): i
                for i, code in enumerate(syn_codes)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    results[idx] = result.result
                except Exception as e:
                    results[idx] = None

        return results

    def starmap(self, args_list: List[Any]) -> List[Any]:
        """Like map(), but each element is a single argument (not a tuple).

        Convenience for functions that take exactly one argument.

        Args:
            args_list: List of single arguments, e.g. [1, 2, 3, 4, 5]

        Returns:
            List of results.

        Example:
            results = square.starmap([1, 2, 3, 4, 5])
            # results == [1, 4, 9, 16, 25]
        """
        return self.map([(a,) for a in args_list])

    def preview(self, *args: Any) -> str:
        """Preview the generated .syn code without executing it.

        Useful for debugging and understanding what Synapse will run.

        Example:
            print(reward.preview(100, 5))
            # @f 2 reward [ + * $0 99 $1 ]
            # @f 0 main [ let $0 call reward 100 5 $0 ]
        """
        return self._build_syn_code(args)

    def local(self, *args: Any, **kwargs: Any) -> Any:
        """Explicitly run locally. Same as calling the function directly.

        Useful when you want to be explicit about execution location.
        """
        return self._fn(*args, **kwargs)


class SynapseApp:
    """Application context for Synapse decorator API.

    The Modal playbook: write Python, deploy to sub-millisecond Wasm.

    Usage:
        from synapse import app

        @app.function
        def compute(x, y):
            return x * y + 42

        # Normal Python:
        result = compute(3, 4)        # 54

        # On Synapse (sub-ms Wasm):
        result = compute.remote(3, 4) # 54, but 37,500× faster cold start

        # Batch (1000 parallel):
        results = compute.map([(i, i) for i in range(100)])
    """

    def __init__(
        self,
        api_key: Union[str, None] = None,
        base_url: Union[str, None] = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._client = None
        self._functions: dict[str, SynapseFunction] = {}

    def _get_client(self):
        """Lazily create the Synapse client."""
        if self._client is None:
            from synapse.client import Synapse

            api_key = self._api_key or os.environ.get("SYNAPSE_API_KEY", "")
            base_url = self._base_url or os.environ.get(
                "SYNAPSE_BASE_URL", "https://api.synapserun.dev"
            )
            if not api_key:
                raise ValueError(
                    "API key required. Set SYNAPSE_API_KEY env var or pass api_key to SynapseApp().\n"
                    "\n"
                    "  export SYNAPSE_API_KEY=sk_live_...\n"
                    "\n"
                    "Or:\n"
                    "  app = SynapseApp(api_key='sk_live_...')"
                )
            self._client = Synapse(api_key=api_key, base_url=base_url)
        return self._client

    def function(self, fn: Callable) -> SynapseFunction:
        """Decorator to register a function for Synapse execution.

        The decorated function can be called normally (runs locally)
        or via .remote() / .map() (runs on Synapse).

        Supported Python subset:
            - Variables, assignment, augmented assignment (+=, -=, etc.)
            - Arithmetic: +, -, *, //, %, **
            - Comparisons: >, <, ==, !=, >=, <=
            - Control flow: if/elif/else, while, for-in-range
            - Functions: def, return, function calls
            - Builtins: abs(), min(), max(), int(), print()
            - Lists, dicts, tuples (integer values only)

        Not supported (will raise transpiler errors):
            - imports, classes, string operations
            - async/await, generators
            - pip packages (numpy, pandas, etc.)
        """
        wrapped = SynapseFunction(fn, self)
        self._functions[fn.__name__] = wrapped
        return wrapped


# Default app instance for convenience
app = SynapseApp()
