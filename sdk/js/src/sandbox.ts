/**
 * E2B-compatible Sandbox API for Synapse Cell.
 *
 * Talks to a Synapse Cell gateway over HTTP. Get a running gateway via:
 *   - Local dev: `docker run -p 8001:8001 ghcr.io/freshfield-ai/cell-gateway:latest`
 *   - Self-hosted: see https://github.com/Synapse-Run/cell/blob/main/docs/SELF_HOSTED.md
 *   - Cloud (coming Q1 2026): localhost:8002
 *
 * @example
 * ```typescript
 * import { Sandbox } from '@runsynapse/sdk';
 *
 * const sbx = await Sandbox.create({ apiUrl: 'http://localhost:8001' });
 * const execution = await sbx.runCode('print(2+2)', { language: 'python' });
 * console.log(execution.logs.stdout);  // ['4']
 * await sbx.kill();
 * ```
 */

// ── Types matching E2B's interface ──────────────────────────────────

export interface OutputMessage {
  line: string;
  timestamp: number;
  error: boolean;
}

export interface Logs {
  stdout: string[];
  stderr: string[];
}

export interface ExecutionError {
  name: string;
  value: string;
  traceback: string[];
}

export interface Result {
  text?: string;
  html?: string;
  png?: string;
  jpeg?: string;
  svg?: string;
  json?: Record<string, unknown>;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Execution {
  results: Result[];
  logs: Logs;
  error?: ExecutionError;
  executionCount?: number;
  exitCode?: number;
  /** Server-side execution latency in ms */
  latencyMs?: number;
  /** SHA-256 execution receipt */
  receipt?: {
    executionId: string;
    codeHash: string;
    resultHash: string;
    template: string;
    timestamp: number;
  };
}

export interface RunCodeOpts {
  onStdout?: (output: OutputMessage) => Promise<unknown> | unknown;
  onStderr?: (output: OutputMessage) => Promise<unknown> | unknown;
  onResult?: (data: Result) => Promise<unknown> | unknown;
  onError?: (error: ExecutionError) => Promise<unknown> | unknown;
  envs?: Record<string, string>;
  timeoutMs?: number;
  requestTimeoutMs?: number;
  language?: string;
}

export interface SandboxOpts {
  /** Gateway URL. Env: SYNAPSE_API_URL or SYNAPSE_BASE_URL. */
  apiUrl?: string;
  /** API key. Env: SYNAPSE_API_KEY. */
  apiKey?: string;
  /** Template to create (python3, javascript, or custom). Default: python3. */
  template?: string;
  /** Persistent session (state carries across run_code calls). Default: false. */
  persistent?: boolean;
  /** Inactivity timeout in milliseconds. Default: 300000 (5 min). */
  timeoutMs?: number;
  /** Sandbox metadata (k/v tags). */
  metadata?: Record<string, string>;
  /** Environment variables. */
  envs?: Record<string, string>;
}

export interface FileInfo {
  name: string;
  path: string;
  type: 'file' | 'dir';
  size: number;
}

// ── Error class ─────────────────────────────────────────────────────

export class CellError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CellError';
  }
}

// ── Sandbox Files API ───────────────────────────────────────────────

class SandboxFiles {
  constructor(private sandbox: Sandbox) {}

  async list(path: string = '/'): Promise<FileInfo[]> {
    const body = await this.sandbox.request(
      `/v1/cells/${this.sandbox.id}/files/list?path=${encodeURIComponent(path)}`,
      undefined,
      'GET',
    );
    return (body as { entries?: FileInfo[] }).entries ?? (body as unknown as FileInfo[]);
  }

  async read(path: string): Promise<string> {
    const body = await this.sandbox.request(
      `/v1/cells/${this.sandbox.id}/files?path=${encodeURIComponent(path)}`,
      undefined,
      'GET',
    );
    return (body as { content: string }).content;
  }

  async write(path: string, content: string): Promise<void> {
    await this.sandbox.request(`/v1/cells/${this.sandbox.id}/files`, {
      path,
      content,
    });
  }

  async remove(path: string): Promise<void> {
    await this.sandbox.request(
      `/v1/cells/${this.sandbox.id}/files?path=${encodeURIComponent(path)}`,
      undefined,
      'DELETE',
    );
  }

  async makeDir(path: string): Promise<void> {
    await this.sandbox.request(`/v1/cells/${this.sandbox.id}/files/mkdir`, { path });
  }

  async exists(path: string): Promise<boolean> {
    const body = await this.sandbox.request(
      `/v1/cells/${this.sandbox.id}/files/exists?path=${encodeURIComponent(path)}`,
      undefined,
      'GET',
    );
    return (body as { exists: boolean }).exists;
  }
}

// ── Sandbox class (E2B-compatible) ──────────────────────────────────

export class Sandbox {
  readonly id: string;
  readonly files: SandboxFiles;

  private apiKey: string;
  private apiUrl: string;
  private killed: boolean = false;

  private constructor(apiKey: string, apiUrl: string, sandboxId: string) {
    this.id = sandboxId;
    this.apiKey = apiKey;
    this.apiUrl = apiUrl;
    this.files = new SandboxFiles(this);
  }

  /**
   * Create a new sandbox via the gateway.
   *
   * Requires a reachable Synapse Cell gateway. For local dev:
   *   docker run -p 8001:8001 ghcr.io/freshfield-ai/cell-gateway:latest
   *   new Sandbox({ apiUrl: 'http://localhost:8001' })
   */
  static async create(opts?: SandboxOpts): Promise<Sandbox> {
    const apiKey = resolveApiKey(opts);
    const apiUrl = resolveApiUrl(opts);

    const payload: Record<string, unknown> = {
      template: opts?.template ?? 'python3',
      persistent: opts?.persistent ?? false,
      timeout_ms: opts?.timeoutMs ?? 300_000,
    };
    if (opts?.metadata) payload.metadata = opts.metadata;
    if (opts?.envs) payload.envs = opts.envs;

    const body = await doRequest(apiUrl, '/v1/cells', apiKey, payload);
    const cellId = (body.cell_id as string) ?? (body.sandbox_id as string);
    if (!cellId) {
      throw new CellError(`Gateway returned invalid response: ${JSON.stringify(body)}`);
    }
    return new Sandbox(apiKey, apiUrl, cellId);
  }

  /**
   * Attach to an existing running sandbox by ID.
   */
  static async connect(sandboxId: string, opts?: SandboxOpts): Promise<Sandbox> {
    const apiKey = resolveApiKey(opts);
    const apiUrl = resolveApiUrl(opts);
    // Verify it exists
    await doRequest(apiUrl, `/v1/cells/${sandboxId}`, apiKey, undefined, 'GET');
    return new Sandbox(apiKey, apiUrl, sandboxId);
  }

  /** @internal */
  async request(
    endpoint: string,
    payload?: Record<string, unknown>,
    method?: string,
  ): Promise<Record<string, unknown>> {
    if (this.killed) {
      throw new CellError(`Sandbox ${this.id} has been killed`);
    }
    return doRequest(this.apiUrl, endpoint, this.apiKey, payload, method);
  }

  /**
   * Run code in the sandbox.
   */
  async runCode(code: string, opts?: RunCodeOpts): Promise<Execution> {
    const body = await this.request(`/v1/cells/${this.id}/exec`, {
      code,
      language: opts?.language,
    });

    const stdout = (body.stdout as string) || '';
    const stderr = (body.stderr as string) || '';
    const stdoutLines = stdout ? stdout.split('\n').filter((l) => l.length > 0) : [];
    const stderrLines = stderr ? stderr.split('\n').filter((l) => l.length > 0) : [];
    const now = Date.now();

    if (opts?.onStdout) {
      for (const line of stdoutLines) {
        await opts.onStdout({ line, timestamp: now, error: false });
      }
    }
    if (opts?.onStderr) {
      for (const line of stderrLines) {
        await opts.onStderr({ line, timestamp: now, error: true });
      }
    }

    const execution: Execution = {
      results: stdout ? [{ text: stdout }] : [],
      logs: { stdout: stdoutLines, stderr: stderrLines },
      exitCode: body.exit_code as number,
      latencyMs: body.latency_ms as number,
    };
    if (body.receipt && typeof body.receipt === 'object') {
      const r = body.receipt as Record<string, unknown>;
      execution.receipt = {
        executionId: r.execution_id as string,
        codeHash: r.code_hash as string,
        resultHash: r.result_hash as string,
        template: r.template as string,
        timestamp: r.timestamp as number,
      };
    }
    if (execution.exitCode !== undefined && execution.exitCode !== 0) {
      execution.error = {
        name: 'ExecutionError',
        value: stderr || `Exit code ${execution.exitCode}`,
        traceback: stderrLines,
      };
      if (opts?.onError) {
        await opts.onError(execution.error);
      }
    }
    return execution;
  }

  /**
   * Run a shell command.
   */
  async command(cmd: string): Promise<Execution> {
    const body = await this.request(`/v1/cells/${this.id}/cmd`, { command: cmd });
    const stdout = (body.stdout as string) || '';
    const stderr = (body.stderr as string) || '';
    return {
      results: stdout ? [{ text: stdout }] : [],
      logs: {
        stdout: stdout.split('\n').filter((l) => l.length > 0),
        stderr: stderr.split('\n').filter((l) => l.length > 0),
      },
      exitCode: body.exit_code as number,
      latencyMs: body.latency_ms as number,
    };
  }

  /**
   * Get sandbox info.
   */
  async getInfo(): Promise<Record<string, unknown>> {
    return this.request(`/v1/cells/${this.id}`, undefined, 'GET');
  }

  /**
   * Kill the sandbox.
   */
  async kill(): Promise<void> {
    if (this.killed) return;
    try {
      await this.request(`/v1/cells/${this.id}`, undefined, 'DELETE');
    } catch {
      /* already dead */
    }
    this.killed = true;
  }

  async close(): Promise<void> {
    return this.kill();
  }
}

// ── Helpers ─────────────────────────────────────────────────────────

function resolveApiKey(opts?: SandboxOpts): string {
  return (
    opts?.apiKey ||
    (typeof process !== 'undefined'
      ? process.env?.SYNAPSE_API_KEY || process.env?.E2B_API_KEY
      : '') ||
    ''
  );
}

function resolveApiUrl(opts?: SandboxOpts): string {
  const candidate =
    opts?.apiUrl ||
    (typeof process !== 'undefined'
      ? process.env?.SYNAPSE_API_URL || process.env?.SYNAPSE_BASE_URL
      : '') ||
    '';
  if (!candidate) {
    throw new CellError(
      'No Synapse Cell gateway URL configured. Set apiUrl in options, ' +
        'or the SYNAPSE_API_URL environment variable. ' +
        'For local dev: `docker run -p 8001:8001 ghcr.io/freshfield-ai/cell-gateway:latest` ' +
        'then pass { apiUrl: "http://localhost:8001" }. ' +
        'See https://github.com/Synapse-Run/cell#self-hosting for details.',
    );
  }
  return candidate.replace(/\/$/, '');
}

async function doRequest(
  apiUrl: string,
  endpoint: string,
  apiKey: string,
  payload?: Record<string, unknown>,
  method?: string,
): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
  }
  const httpMethod = method ?? (payload ? 'POST' : 'GET');
  let resp: Response;
  try {
    resp = await fetch(`${apiUrl}${endpoint}`, {
      method: httpMethod,
      headers,
      body: payload ? JSON.stringify(payload) : undefined,
    });
  } catch (e) {
    throw new CellError(
      `Gateway unreachable at ${apiUrl}${endpoint}: ${(e as Error).message}. ` +
        'For local dev: `docker run -p 8001:8001 ghcr.io/freshfield-ai/cell-gateway:latest`',
    );
  }
  const text = await resp.text();
  if (!resp.ok) {
    throw new CellError(`HTTP ${resp.status} at ${endpoint}: ${text.slice(0, 200)}`);
  }
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { raw: text };
  }
}

export default Sandbox;
