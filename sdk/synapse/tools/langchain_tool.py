"""LangChain tools for Synapse — Execute .syn code or generate from English.

Two tools:
  SynapseExecuteTool   — Takes .syn code, compiles + executes via Turbo FFI
  SynapseGenerateTool  — Takes English, generates .syn via Synapse-50M, executes

Both run fully local — no API key, no network, sub-second latency.

Usage:
    from synapse.tools.langchain_tool import SynapseGenerateTool, SynapseExecuteTool

    # Natural language → result (recommended for AI agents)
    generate_tool = SynapseGenerateTool()
    result = generate_tool.run("compute fibonacci of 10")  # → "55"

    # Direct .syn execution (for when agent already has .syn code)
    execute_tool = SynapseExecuteTool()
    result = execute_tool.run("@f 0 main [ + 21 21 ]")  # → "42"

    # Use in a LangChain agent:
    from langchain.agents import initialize_agent
    agent = initialize_agent(
        tools=[SynapseGenerateTool(), SynapseExecuteTool()],
        llm=llm, agent="zero-shot-react-description",
    )
    agent.run("What is the 20th fibonacci number?")

Requires: pip install langchain-core
"""
import os
import sys

try:
    from langchain_core.tools import BaseTool
    from langchain_core.callbacks import CallbackManagerForToolRun
except ImportError:
    try:
        from langchain.tools import BaseTool
        CallbackManagerForToolRun = None
    except ImportError:
        class BaseTool:
            name = ""
            description = ""
            def _run(self, *a, **kw): raise NotImplementedError
            def run(self, *a, **kw): return self._run(*a, **kw)
        CallbackManagerForToolRun = None

from typing import Optional

# Add project root for imports
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class SynapseExecuteTool(BaseTool):
    """Execute .syn code locally via Turbo FFI (150K+ evals/sec).

    Input:  .syn source code
    Output: Integer result or error message
    Latency: <1ms per execution
    """

    name: str = "synapse_execute"
    description: str = (
        "Execute .syn code on a native WebAssembly engine. "
        "Input: .syn source code using prefix notation (e.g., '+ 3 4' means 3+4). "
        "Output: the integer result. Latency: <1ms. "
        "Use this when you already have .syn code to run."
    )

    _evaluator: object = None

    # Pydantic v2 model_config (LangChain v0.3+ requires Pydantic v2)
    model_config = {"arbitrary_types_allowed": True}

    def _ensure_evaluator(self):
        if self._evaluator is None:
            os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')
            from sdk.synapse_eval import SynapseEvaluator
            self._evaluator = SynapseEvaluator()

    def _run(self, code: str, run_manager: Optional[object] = None) -> str:
        self._ensure_evaluator()
        result = self._evaluator.execute_fast(code)
        if result.get('error'):
            return f"Error: {result['error']}"
        return str(result['result'])


class SynapseGenerateTool(BaseTool):
    """Generate .syn code from English and execute it (natural language compute).

    Input:  Natural language description of desired computation
    Output: Integer result from executing the generated code
    Latency: ~700ms (model generation + compilation + execution)

    This is the recommended tool for AI agents — they describe what they
    want in English and get a verified result back.
    """

    name: str = "synapse_compute"
    description: str = (
        "Generate and execute a computation described in natural language. "
        "Input: English description like 'compute fibonacci of 10' or 'find GCD of 48 and 18'. "
        "Output: the integer result. "
        "The tool generates .syn code using a local 50M-param Transformer, "
        "compiles it to WebAssembly, and executes it in a secure sandbox. "
        "Use this for math computations, algorithms, and numerical operations."
    )

    _generator: object = None

    # Pydantic v2 model_config (LangChain v0.3+ requires Pydantic v2)
    model_config = {"arbitrary_types_allowed": True}

    def _ensure_generator(self):
        if self._generator is None:
            os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')
            from sdk.synapse_generate import SynapseGenerator
            self._generator = SynapseGenerator()

    def _run(self, prompt: str, run_manager: Optional[object] = None) -> str:
        self._ensure_generator()
        # Normalize prompt to match training format
        lower = prompt.lower().strip()
        if not lower.startswith('write .syn') and not lower.startswith('write a .syn'):
            prompt = f"Write .syn code to {lower}"
        result = self._generator.generate(prompt)
        if result.get('error'):
            return f"Error: {result['error']}\nGenerated code: {result.get('code', 'N/A')}"
        return str(result['result'])
