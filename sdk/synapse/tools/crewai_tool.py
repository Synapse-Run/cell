"""CrewAI tools for Synapse — Execute .syn code or generate from English.

Two tools:
  SynapseExecuteCrewTool   — Takes .syn code, compiles + executes via Turbo FFI
  SynapseGenerateCrewTool  — Takes English, generates .syn via Synapse-50M, executes

Both run fully local — no API key, no network, sub-second latency.

Usage:
    from synapse.tools.crewai_tool import SynapseGenerateCrewTool

    tool = SynapseGenerateCrewTool()
    result = tool.run("compute fibonacci of 10")  # → "55"

    # In a CrewAI crew:
    from crewai import Agent, Task, Crew
    agent = Agent(
        role="Compute Specialist",
        tools=[SynapseGenerateCrewTool()],
        llm=llm,
    )

Requires: pip install crewai
"""
import os
import sys

try:
    from crewai.tools import BaseTool as CrewBaseTool
except ImportError:
    class CrewBaseTool:
        name = ""
        description = ""
        def _run(self, *a, **kw): raise NotImplementedError
        def run(self, *a, **kw): return self._run(*a, **kw)


_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class SynapseExecuteCrewTool(CrewBaseTool):
    """Execute .syn code locally via Turbo FFI (150K+ evals/sec)."""

    name: str = "synapse_execute"
    description: str = (
        "Execute .syn code on a native WebAssembly engine. "
        "Input: .syn source code. Output: integer result. Latency: <1ms."
    )

    _evaluator: object = None

    # Pydantic v2 model_config (CrewAI v0.80+ uses Pydantic v2)
    model_config = {"arbitrary_types_allowed": True}

    def _ensure_evaluator(self):
        if self._evaluator is None:
            os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')
            from sdk.synapse_eval import SynapseEvaluator
            self._evaluator = SynapseEvaluator()

    def _run(self, code: str) -> str:
        self._ensure_evaluator()
        result = self._evaluator.execute_fast(code)
        if result.get('error'):
            return f"Error: {result['error']}"
        return str(result['result'])


class SynapseGenerateCrewTool(CrewBaseTool):
    """Generate .syn code from English and execute it (natural language compute)."""

    name: str = "synapse_compute"
    description: str = (
        "Generate and execute a computation from natural language. "
        "Input: English description like 'compute fibonacci of 10'. "
        "Output: integer result. Uses a local 50M-param model to generate "
        ".syn code, compiles to WebAssembly, executes in a secure sandbox."
    )

    _generator: object = None

    # Pydantic v2 model_config (CrewAI v0.80+ uses Pydantic v2)
    model_config = {"arbitrary_types_allowed": True}

    def _ensure_generator(self):
        if self._generator is None:
            os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')
            from sdk.synapse_generate import SynapseGenerator
            self._generator = SynapseGenerator()

    def _run(self, prompt: str) -> str:
        self._ensure_generator()
        # Normalize prompt to match training format
        lower = prompt.lower().strip()
        if not lower.startswith('write .syn') and not lower.startswith('write a .syn'):
            prompt = f"Write .syn code to {lower}"
        result = self._generator.generate(prompt)
        if result.get('error'):
            return f"Error: {result['error']}"
        return str(result['result'])
