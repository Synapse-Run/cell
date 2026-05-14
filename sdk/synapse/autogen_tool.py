"""AutoGen tool for Synapse Cell — Sprint B Batch 7.

Provides a Cell code-execution tool compatible with Microsoft AutoGen
(https://github.com/microsoft/autogen).

Usage:
    from synapse.autogen_tool import SynapseCellExecutor

    executor = SynapseCellExecutor()
    result = executor.execute_code_blocks([("python", "print('hello')")])

    # With AutoGen agents:
    from autogen import AssistantAgent, UserProxyAgent
    user = UserProxyAgent("user", code_execution_config={"executor": executor})

Requires: pip install pyautogen
"""
from __future__ import annotations

import os
from typing import Any, List


try:
    from autogen.coding import CodeExecutor, CodeBlock, CodeResult
    _HAS_AUTOGEN = True
except ImportError:
    try:
        from autogen import CodeExecutor, CodeBlock, CodeResult  # type: ignore
        _HAS_AUTOGEN = True
    except ImportError:
        _HAS_AUTOGEN = False
        # Stubs so the module is importable without autogen
        class CodeBlock:  # type: ignore[no-redef]
            def __init__(self, language: str = "", code: str = ""):
                self.language = language
                self.code = code

        class CodeResult:  # type: ignore[no-redef]
            def __init__(self, exit_code: int = 0, output: str = ""):
                self.exit_code = exit_code
                self.output = output

        class CodeExecutor:  # type: ignore[no-redef]
            pass


class SynapseCellExecutor(CodeExecutor):
    """AutoGen-compatible code executor using Synapse Cell.

    200x faster than E2B. Wasm sandbox with cryptographic receipts.
    Drop-in replacement for AutoGen's LocalCommandLineCodeExecutor or
    DockerCommandLineCodeExecutor.
    """

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        timeout: int = 60,
    ):
        self.api_url = api_url or os.environ.get("SYNAPSE_API_URL", "local")
        self.api_key = api_key or os.environ.get("SYNAPSE_API_KEY", "")
        self.timeout = timeout

    def execute_code_blocks(
        self,
        code_blocks: List[Any],
        **kwargs: Any,
    ) -> Any:
        """Execute a list of code blocks in a Synapse Cell sandbox.

        Args:
            code_blocks: List of CodeBlock objects or (language, code) tuples.

        Returns:
            CodeResult with exit_code and combined output.
        """
        from synapse.cell import Cell

        outputs = []
        last_exit_code = 0

        try:
            with Cell(
                api_key=self.api_key,
                api_url=self.api_url,
                persistent=True,
                timeout_ms=self.timeout * 1000,
            ) as cell:
                for block in code_blocks:
                    if isinstance(block, tuple):
                        language, code = block[0], block[1]
                    elif hasattr(block, 'code'):
                        language = getattr(block, 'language', 'python')
                        code = block.code
                    else:
                        language, code = "python", str(block)

                    result = cell.run(code, language=language)
                    last_exit_code = result.exit_code

                    output = result.stdout
                    if result.stderr:
                        output += f"\n[stderr] {result.stderr}"
                    outputs.append(output)

                    if result.exit_code != 0:
                        break

        except Exception as e:
            return CodeResult(exit_code=1, output=f"Cell error: {e!s}")

        return CodeResult(
            exit_code=last_exit_code,
            output="\n".join(outputs),
        )

    @property
    def code_extractor(self) -> Any:
        """AutoGen compatibility — returns None (uses default extractor)."""
        return None
