/**
 * Synapse SDK for the current preview gateway surface.
 *
 * @example
 * ```typescript
 * import { Synapse } from '@runsynapse/sdk';
 *
 * const client = new Synapse({ apiKey: 'sk_live_...' });
 * const result = await client.execute('@f 0 main [ + 21 21 ]');
 * console.log(result.result); // 42
 * console.log(result.latencyMs); // 0.52
 * ```
 */

export interface SynapseConfig {
  /** Optional edge/API key if your deployment enforces one */
  apiKey?: string;
  /** Gateway URL (default: https://api.synapserun.dev) */
  baseUrl?: string;
  /** Request timeout in ms (default: 15000) */
  timeout?: number;
  /** Max retries for transient errors (default: 3) */
  maxRetries?: number;
}

export interface ExecutionResult {
  /** Integer return value from the Wasm module */
  result: number;
  /** Captured stdout output */
  stdout: string;
  /** Arena memory position */
  arenaPos: number;
  /** End-to-end latency in milliseconds */
  latencyMs: number;
  /** Unique execution ID */
  executionId: string;
  /** Compile time in milliseconds */
  compileTimeMs: number;
  /** Wasm binary size in bytes */
  wasmSize: number;
  /** SHA-256 hash of the Wasm binary */
  deterministicHash: string;
  /** Cost in USD */
  costUsd: number;
}

export interface HealthStatus {
  status: string;
  latencyMs?: number;
}

export class SynapseError extends Error {
  statusCode: number;
  errorType: string;

  constructor(statusCode: number, message: string, errorType: string = '') {
    super(`Synapse API error ${statusCode}: ${message}`);
    this.name = 'SynapseError';
    this.statusCode = statusCode;
    this.errorType = errorType;
  }
}

export class Synapse {
  private apiKey: string;
  private baseUrl: string;
  private timeout: number;
  private maxRetries: number;

  constructor(config: SynapseConfig) {
    this.apiKey = config.apiKey || '';
    this.baseUrl = (config.baseUrl || 'https://api.synapserun.dev').replace(/\/$/, '');
    this.timeout = config.timeout || 15000;
    this.maxRetries = config.maxRetries || 3;
  }

  private async request(endpoint: string, payload?: Record<string, unknown>): Promise<Record<string, unknown>> {
    const url = `${this.baseUrl}${endpoint}`;
    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeout);

        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
        };
        if (this.apiKey) {
          headers['Authorization'] = `Bearer ${this.apiKey}`;
        }
        const resp = await fetch(url, {
          method: payload ? 'POST' : 'GET',
          headers,
          body: payload ? JSON.stringify(payload) : undefined,
          signal: controller.signal,
        });

        clearTimeout(timer);
        const body = await resp.json() as Record<string, unknown>;

        if (!resp.ok) {
          const errMsg = (body.error as string) || `HTTP ${resp.status}`;
          if (resp.status >= 400 && resp.status < 500) {
            throw new SynapseError(resp.status, errMsg, (body.error_type as string) || '');
          }
          throw new SynapseError(resp.status, errMsg);
        }

        return body;
      } catch (e) {
        if (e instanceof SynapseError && e.statusCode >= 400 && e.statusCode < 500) {
          throw e; // Don't retry client errors
        }
        lastError = e as Error;
        if (attempt < this.maxRetries) {
          await new Promise(r => setTimeout(r, Math.min(2 ** attempt * 100, 5000)));
        }
      }
    }

    throw lastError || new SynapseError(0, 'Unknown error');
  }

  /**
   * Execute .syn source code on native Wasm Wasm engine.
   *
   * @example
   * ```typescript
   * const result = await client.execute('@f 0 main [ + 21 21 ]');
   * console.log(result.result); // 42
   * ```
   */
  async execute(code: string): Promise<ExecutionResult> {
    const body = await this.request('/v1/execute', { code });

    if (body.status === 'error') {
      throw new SynapseError(400, (body.error as string) || 'execution_failed');
    }

    return {
      result: (body.result as number) || 0,
      stdout: (body.stdout as string) || '',
      arenaPos: (body.arena_pos as number) || 0,
      latencyMs: (body.latency_ms as number) || 0,
      executionId: (body.execution_id as string) || '',
      compileTimeMs: (body.compile_time_ms as number) || 0,
      wasmSize: (body.wasm_size as number) || 0,
      deterministicHash: (body.deterministic_hash as string) || '',
      costUsd: (body.cost_usd as number) || 0,
    };
  }

  /**
   * Execute restricted Python through the current preview endpoint.
   */
  async executePython(code: string): Promise<ExecutionResult> {
    const body = await this.request('/v1/execute/python', { code });

    if (body.status === 'error') {
      throw new SynapseError(400, (body.error as string) || 'execution_failed');
    }

    return {
      result: (body.result as number) || 0,
      stdout: (body.stdout as string) || '',
      arenaPos: (body.arena_pos as number) || 0,
      latencyMs: (body.latency_ms as number) || 0,
      executionId: (body.execution_id as string) || '',
      compileTimeMs: (body.compile_time_ms as number) || 0,
      wasmSize: (body.wasm_size as number) || 0,
      deterministicHash: (body.deterministic_hash as string) || '',
      costUsd: (body.cost_usd as number) || 0,
    };
  }

  /**
   * Execute a pre-compiled .wasm binary.
   */
  async executeWasm(wasmBytes: Uint8Array): Promise<ExecutionResult> {
    const base64 = Buffer.from(wasmBytes).toString('base64');
    const body = await this.request('/v1/execute', { wasm: base64 });

    if (body.status === 'error') {
      throw new SynapseError(400, (body.error as string) || 'execution_failed');
    }

    return {
      result: (body.result as number) || 0,
      stdout: (body.stdout as string) || '',
      arenaPos: (body.arena_pos as number) || 0,
      latencyMs: (body.latency_ms as number) || 0,
      executionId: (body.execution_id as string) || '',
      compileTimeMs: (body.compile_time_ms as number) || 0,
      wasmSize: (body.wasm_size as number) || 0,
      deterministicHash: (body.deterministic_hash as string) || '',
      costUsd: (body.cost_usd as number) || 0,
    };
  }

  /**
   * Check gateway health and kernel connectivity.
   */
  async health(): Promise<HealthStatus> {
    const body = await this.request('/health');
    return {
      status: (body.status as string) || 'unknown',
      latencyMs: body.latency_ms as number | undefined,
    };
  }
}

export default Synapse;

// ── E2B-compatible Sandbox API ──────────────────────────────────────
// Drop-in replacement: change `from '@e2b/code-interpreter'` to `from '@runsynapse/sdk'`
export { Sandbox, CellError } from './sandbox';
export type {
  Execution,
  ExecutionError as SandboxExecutionError,
  Result as SandboxResult,
  Logs,
  OutputMessage,
  RunCodeOpts,
  SandboxOpts,
  FileInfo,
} from './sandbox';
