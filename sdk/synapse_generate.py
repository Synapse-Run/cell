#!/usr/bin/env python3
"""
synapse_generate — Natural language → .syn code generation + execution.

Uses the Synapse-50M Transformer to generate .syn code from English prompts,
compiles and runs via Turbo FFI, with self-correcting retries on failure.

Usage:
    from sdk.synapse_generate import SynapseGenerator

    gen = SynapseGenerator()
    result = gen.generate("compute fibonacci of 10")
    # → {'code': '@f 1 fib [...]', 'result': 55, 'compiled': True, 'correct': True}

    # Quick helper:
    print(gen.run("compute 50 * 50"))  # → 2500
"""

import os
import sys
import time
import torch

# Add project root to path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_train_root = os.path.join(_project_root, "train")
if _train_root not in sys.path:
    sys.path.insert(0, _train_root)


class SynapseGenerator:
    """Generate .syn code from natural language using the Synapse-50M model.

    Features:
    - Self-correcting generation (retries with higher temperature on compile failure)
    - Turbo FFI evaluation (150K+ evals/sec)
    - Caches model + evaluator for fast repeated calls
    """

    def __init__(self, checkpoint_path=None, device=None, evaluator=None):
        """Initialize the generator.

        Args:
            checkpoint_path: Path to model checkpoint (.pt file).
                Defaults to train/checkpoints/synapse25m-grpo/best.pt
            device: PyTorch device ('mps', 'cuda', 'cpu'). Auto-detected if None.
            evaluator: Optional pre-initialized SynapseEvaluator instance.
        """
        os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')

        self._device = device or self._detect_device()
        self._checkpoint_path = checkpoint_path or self._default_checkpoint()
        self._model = None
        self._tokenizer = None
        self._evaluator = evaluator
        self._constrained_decoder = None

    def _detect_device(self):
        if torch.backends.mps.is_available():
            return 'mps'
        elif torch.cuda.is_available():
            return 'cuda'
        return 'cpu'

    def _default_checkpoint(self):
        """Find the best available checkpoint."""
        candidates = [
            os.path.join(_project_root, 'train', 'checkpoints', 'synapse25m-grpo', 'best.pt'),
            os.path.join(_project_root, 'train', 'checkpoints', 'synapse25m-sft', 'best.pt'),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        raise FileNotFoundError(
            f"No model checkpoint found. Looked in:\n" +
            "\n".join(f"  {p}" for p in candidates)
        )

    def _ensure_loaded(self):
        """Lazy-load model and tokenizer on first use."""
        if self._model is not None:
            return

        from syn_tokenizer import SynTokenizer
        from syn_model import Synapse25M
        from model_loading import load_model_checkpoint
        from syn_constrained_decoder import ConstrainedSynDecoder

        self._tokenizer = SynTokenizer()
        self._model, _cp, _config, _summary = load_model_checkpoint(
            Synapse25M,
            self._checkpoint_path,
            self._device,
            target_vocab_size=self._tokenizer.vocab_size,
        )
        self._model.eval()
        self._constrained_decoder = ConstrainedSynDecoder(self._tokenizer)

    def _ensure_evaluator(self):
        """Lazy-load evaluator on first use."""
        if self._evaluator is not None:
            return
        from sdk.synapse_eval import SynapseEvaluator
        self._evaluator = SynapseEvaluator()
        # Enable turbo mode
        try:
            test = self._evaluator.execute_fast("@f 0 main [ + 1 1 ]")
            if test.get('error') is None:
                self._evaluator.execute = self._evaluator.execute_fast
        except Exception:
            pass

    def _generate_code(self, prompt, temperature=0.1, max_tokens=256, constrained=False):
        """Generate .syn code from a natural language prompt."""
        self._ensure_loaded()
        tok = self._tokenizer

        prompt_ids = [tok.bos_id] + tok.encode(prompt) + [tok.sep_id]
        pt = torch.tensor([prompt_ids], dtype=torch.long, device=self._device)
        role_ids = torch.zeros_like(pt)
        code_mask = None
        if getattr(self._model.config, "mask_generation_to_code_vocab_after_sep", False):
            code_mask = torch.zeros(tok.vocab_size, dtype=torch.bool, device=self._device)
            code_mask[tok.code_generation_token_ids] = True

        with torch.no_grad():
            if constrained:
                gen, constraint_summary = self._constrained_decoder.generate(
                    self._model,
                    pt,
                    role_ids=role_ids,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                )
            else:
                gen = self._model.generate(
                    pt,
                    role_ids=role_ids,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    code_token_mask=code_mask,
                )
                gen_ids = gen[0, len(prompt_ids):].tolist()
                constraint_summary = self._constrained_decoder.analyze_token_ids(gen_ids, mode="analysis")

        gen_ids = gen[0, len(prompt_ids):].tolist()
        code = tok.decode(gen_ids)
        return code, constraint_summary.to_dict()

    def generate(self, prompt, max_retries=3, temperature=0.1, execute=True, constrained=False):
        """Generate .syn code from a natural language prompt.

        Args:
            prompt: English description of what the code should do.
            max_retries: Number of retry attempts on compile failure (with increasing temperature).
            temperature: Initial sampling temperature (0.0 = greedy, 1.0 = creative).
            execute: Whether to compile and run the generated code.

        Returns:
            dict with keys:
                code: The generated .syn source code
                result: Execution result (int) or None if not executed/failed
                compiled: Whether the code compiled successfully
                error: Error message if any, None on success
                attempts: Number of generation attempts used
                time_ms: Total time in milliseconds
        """
        t0 = time.perf_counter()
        self._ensure_loaded()

        best_code = None
        best_error = None
        best_summary = None

        for attempt in range(max_retries):
            temp = temperature + (attempt * 0.3)  # Increase temp on retries
            code, constraint_summary = self._generate_code(prompt, temperature=temp, constrained=constrained)

            if not execute:
                return {
                    'code': code,
                    'result': None,
                    'compiled': None,
                    'error': None,
                    'attempts': attempt + 1,
                    'constrained': constrained,
                    'constraint_summary': constraint_summary,
                    'time_ms': (time.perf_counter() - t0) * 1000,
                }

            # Try to compile and run
            self._ensure_evaluator()
            result = self._evaluator.execute_fast(code)

            if result.get('error') is None:
                return {
                    'code': code,
                    'result': result['result'],
                    'compiled': True,
                    'error': None,
                    'attempts': attempt + 1,
                    'constrained': constrained,
                    'constraint_summary': constraint_summary,
                    'time_ms': (time.perf_counter() - t0) * 1000,
                }

            # Save first attempt for reporting
            if best_code is None:
                best_code = code
                best_error = result.get('error')
                best_summary = constraint_summary

        # All retries failed
        return {
            'code': best_code or code,
            'result': None,
            'compiled': False,
            'error': best_error or result.get('error'),
            'attempts': max_retries,
            'constrained': constrained,
            'constraint_summary': best_summary or constraint_summary,
            'time_ms': (time.perf_counter() - t0) * 1000,
        }

    def run(self, prompt, **kwargs):
        """Convenience method: generate + execute, return just the result value."""
        r = self.generate(prompt, **kwargs)
        if r['error']:
            raise RuntimeError(f"Generation failed: {r['error']}\nCode: {r['code']}")
        return r['result']

    def generate_batch(self, prompts, **kwargs):
        """Generate .syn code for multiple prompts."""
        return [self.generate(p, **kwargs) for p in prompts]


# ════════════════════════════════════════════════════════════════════════
#  CLI Interface
# ════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog='syn-generate',
        description='Generate .syn code from natural language using Synapse-50M',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  syn-generate "compute fibonacci of 10"
  syn-generate "find GCD of 48 and 18" --show-code
  syn-generate --file prompts.txt --json
  syn-generate "compute 2^16" --temperature 0.5 --retries 5
        """,
    )
    parser.add_argument('prompt', nargs='?', help='Natural language prompt')
    parser.add_argument('--file', '-f', help='Read prompts from file (one per line)')
    parser.add_argument('--show-code', '-c', action='store_true', help='Show generated .syn code')
    parser.add_argument('--code-only', action='store_true', help='Output only the generated code')
    parser.add_argument('--json', '-j', action='store_true', help='Output results as JSON')
    parser.add_argument('--temperature', '-t', type=float, default=0.1, help='Sampling temperature (default: 0.1)')
    parser.add_argument('--retries', '-r', type=int, default=3, help='Max retries on failure (default: 3)')
    parser.add_argument('--checkpoint', help='Path to model checkpoint')
    parser.add_argument('--no-execute', action='store_true', help='Generate code without executing')
    parser.add_argument('--benchmark', action='store_true', help='Run benchmark suite')
    parser.add_argument('--constrained', action='store_true', help='Use structural constrained decoding')

    args = parser.parse_args()

    if args.benchmark:
        _run_benchmark()
        return

    # Collect prompts
    prompts = []
    if args.file:
        with open(args.file) as f:
            prompts = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    elif args.prompt:
        prompts = [args.prompt]
    else:
        parser.print_help()
        sys.exit(1)

    # Initialize generator
    gen = SynapseGenerator(checkpoint_path=args.checkpoint)

    results = []
    for prompt in prompts:
        result = gen.generate(
            prompt,
            temperature=args.temperature,
            max_retries=args.retries,
            execute=not args.no_execute,
            constrained=args.constrained,
        )
        results.append(result)

        if args.json:
            continue  # Print all at once at the end

        if args.code_only:
            print(result['code'])
        else:
            if len(prompts) > 1:
                print(f"\n  Prompt: {prompt}")

            if args.show_code:
                print(f"  Code:   {result['code']}")

            if result['error']:
                print(f"  ❌ Error: {result['error']}")
            elif result['result'] is not None:
                print(f"  → {result['result']}")
            else:
                print(f"  Code: {result['code']}")

            if len(prompts) > 1:
                print(f"  ({result['attempts']} attempt(s), {result['time_ms']:.0f}ms)")

    if args.json:
        import json
        output = results[0] if len(results) == 1 else results
        print(json.dumps(output, indent=2))


def _run_benchmark():
    """Run the standard 10-prompt benchmark suite."""
    prompts = [
        ("Write .syn code to add 21 and 21", 42),
        ("Write .syn code to compute 50 * 50", 2500),
        ("Write .syn code to compute 100 - 58", 42),
        ("Write .syn code to compute factorial of 5", 120),
        ("Write .syn code to sum 1 to 10", 55),
        ("Write .syn code to compute 2^10", 1024),
        ("Write .syn code to compute 10th Fibonacci num", 55),
        ("Write .syn code to find GCD of 48 and 18", 6),
        ("Write .syn code to compute abs of -42", 42),
        ("Write .syn code to compute (10+20)*3", 90),
    ]

    print()
    print("═" * 70)
    print("  Synapse-50M Benchmark Suite")
    print("═" * 70)
    print()

    gen = SynapseGenerator()
    compiled = 0
    correct = 0
    total_ms = 0

    for prompt, expected in prompts:
        result = gen.generate(prompt)
        total_ms += result['time_ms']

        if result['compiled']:
            compiled += 1
        if result['result'] == expected:
            correct += 1
            status = "✓"
        elif result['error']:
            status = "✗"
        else:
            status = "⚠"

        # Truncate prompt for display
        short = prompt.replace("Write .syn code to ", "")
        print(f"  {status} {short:40s} → {str(result['result']):>8s} (exp: {expected})")

    print()
    print(f"  Compile: {compiled}/{len(prompts)} ({100*compiled//len(prompts)}%)")
    print(f"  Correct: {correct}/{len(prompts)} ({100*correct//len(prompts)}%)")
    print(f"  Time:    {total_ms:.0f}ms ({total_ms/len(prompts):.0f}ms/prompt)")
    print()
    print("═" * 70)


if __name__ == '__main__':
    main()
