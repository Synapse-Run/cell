/**
 * Synapse .cell SDK — Persistent sandboxed execution for AI agents.
 *
 * Drop-in E2B replacement with 67×+ faster cold starts, cryptographic
 * receipts, and $0.001/exec pricing.
 *
 * @example
 * ```typescript
 * import { Cell } from '@synapse/cell';
 *
 * const cell = await Cell.create({ apiKey: 'cell_sk_live_...' });
 * await cell.run('x = 42');
 * const result = await cell.run('print(x * 2)'); // stdout: "84"
 * await cell.kill();
 * ```
 */

// ─── Types ──────────────────────────────────────────────────────

export interface CellReceipt {
  execution_id: string;
  code_hash: string;
  result_hash: string;
  template: string;
  timestamp: number;
}

export interface CellResult {
  stdout: string;
  stderr: string;
  exit_code: number;
  latency_ms: number;
  receipt?: CellReceipt;
}

export interface FetchResult {
  status: number;
  body: string;
  content_type: string;
  body_size: number;
  latency_ms: number;
  error?: string;
}

export interface NetworkOptions {
  /** Allow outbound traffic to these hosts */
  allowOut?: string[];
  /** Block outbound traffic to these hosts */
  denyOut?: string[];
  /** Allow inbound traffic from the public internet */
  allowPublicTraffic?: boolean;
  /** Mask the request host header */
  maskRequestHost?: boolean;
}

export interface LifecycleOptions {
  /** What happens when the sandbox times out */
  onTimeout: 'pause' | 'kill';
  /** Automatically resume paused sandboxes on reconnect */
  autoResume?: boolean;
}

export interface CellOptions {
  /** Base URL of the .cell API */
  apiUrl?: string;
  /** API key for authenticated access */
  apiKey?: string;
  /** Language runtime (default: "python3") */
  template?: string;
  /** Enable persistent state between exec calls (default: true) */
  persistent?: boolean;
  /** Optional ID of a persistent volume to mount at /data/ */
  volumeId?: string;
  /** Session timeout in milliseconds (default: 1 hour) */
  timeoutMs?: number;
  // ─── E2B Sandbox.create parity fields (milestone 1.11) ──
  /** Key-value metadata stored on the cell */
  metadata?: Record<string, string>;
  /** Environment variables for the cell */
  envs?: Record<string, string>;
  /** Whether the cell can make outbound network calls (default: true) */
  allowInternetAccess?: boolean;
  /** Network configuration (E2B SandboxNetworkOpts shape) */
  network?: NetworkOptions;
  /** Volume mounts: {mountPath: volumeIdOrName} */
  volumeMounts?: Record<string, string>;
  /** Lifecycle configuration (E2B SandboxLifecycle shape) */
  lifecycle?: LifecycleOptions;
  /** Enable enhanced security (default: true) */
  secure?: boolean;
}

export interface FetchOptions {
  /** HTTP method (default: "GET") */
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  /** Request body (for POST/PUT) */
  body?: string;
  /** Save response to this file path in /data/ */
  saveTo?: string;
  /** Request timeout in seconds (default: 30) */
  timeoutSecs?: number;
}

// ─── Streaming + Background Options ─────────────────────────────

/**
 * Options for streaming code execution.
 *
 * When any callback is provided, Cell.run() uses the SSE streaming
 * endpoint (POST /exec/stream) instead of the blocking POST /exec.
 */
export interface RunOptions {
  /** Language runtime override */
  language?: string;
  /** Callback fired for each stdout line as it arrives */
  onStdout?: (line: string) => void;
  /** Callback fired for each stderr line as it arrives */
  onStderr?: (line: string) => void;
  /** Callback fired when the final result event arrives */
  onResult?: (event: Record<string, unknown>) => void;
  /** Callback fired on error events */
  onError?: (message: string) => void;
}

/**
 * Options for streaming command execution.
 *
 * When ``background`` is true, the command runs asynchronously and
 * Cell.command() returns a {@link CommandHandle} instead of a CellResult.
 */
export interface CommandOptions {
  /** Callback fired for each stdout line as it arrives */
  onStdout?: (line: string) => void;
  /** Callback fired for each stderr line as it arrives */
  onStderr?: (line: string) => void;
  /** Run asynchronously — returns a CommandHandle */
  background?: boolean;
}

// ─── Filesystem Entry Info ──────────────────────────────────────

/** Filesystem entry metadata (E2B-compatible). */
export interface EntryInfo {
  name: string;
  type: 'file' | 'dir' | null;
  path: string;
  size: number;
  mode: number;
  permissions: string;
  owner: string;
  group: string;
  /** ISO 8601 timestamp */
  modifiedTime: string | null;
  symlinkTarget?: string | null;
}

// ─── E2B-Compatible Sandbox Info Types ──────────────────────────

/** E2B-compatible sandbox state. */
export type SandboxState = 'running' | 'paused' | 'killed';

/** Sandbox network configuration (E2B-compatible). */
export interface SandboxNetworkOpts {
  allowOut?: string[];
  denyOut?: string[];
  allowPublicTraffic?: boolean;
  maskRequestHost?: boolean;
}

/** Sandbox lifecycle config (E2B-compatible). */
export interface SandboxLifecycle {
  onTimeout: 'pause' | 'kill';
  autoResume?: boolean;
}

/**
 * E2B-compatible typed sandbox info.
 * Returned by Cell.getInfo() / Sandbox.getInfo().
 * Wire format is snake_case; TS types are camelCase.
 */
export interface SandboxInfo {
  sandboxId: string;
  templateId: string;
  metadata: Record<string, string>;
  /** ISO 8601 timestamp */
  startedAt: string;
  /** ISO 8601 timestamp */
  endAt: string;
  state: SandboxState;
  cpuCount: number;
  memoryMb: number;
  envdVersion: string;
  name?: string | null;
  sandboxDomain?: string | null;
  allowInternetAccess?: boolean | null;
  network?: SandboxNetworkOpts | null;
  lifecycle?: SandboxLifecycle | null;
  volumeMounts?: Record<string, string>[];
}

// ─── Pagination Types ──────────────────────────────────────────

export interface SandboxQuery {
  metadata?: Record<string, string>;
  state?: SandboxState[];
}

export interface SandboxListOptions {
  query?: SandboxQuery;
  limit?: number;
  nextToken?: string;
  apiUrl?: string;
  apiKey?: string;
}

export class SandboxPaginator {
  private _nextToken?: string;
  private _hasNext: boolean = true;
  private readonly apiUrl: string;
  private readonly apiKey?: string;
  readonly query?: SandboxQuery;
  readonly limit?: number;

  constructor(options: SandboxListOptions = {}) {
    this.query = options.query;
    this.limit = options.limit;
    this._nextToken = options.nextToken;
    this.apiUrl = (options.apiUrl ?? 'http://localhost:8002').replace(/\/$/, '');
    this.apiKey = options.apiKey ?? (typeof process !== 'undefined' ? process.env.SYNAPSE_API_KEY : undefined);
  }

  get hasNext(): boolean { return this._hasNext; }

  async nextItems(): Promise<SandboxInfo[]> {
    if (!this._hasNext) return [];
    const params = new URLSearchParams();
    if (this.limit !== undefined) params.set('limit', String(this.limit));
    if (this._nextToken) params.set('next_token', this._nextToken);
    if (this.query?.metadata) {
      const pairs = Object.entries(this.query.metadata)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join(',');
      params.set('metadata', pairs);
    }
    if (this.query?.state && this.query.state.length > 0) {
      params.set('state', this.query.state.join(','));
    }

    const qs = params.toString();
    const url = `${this.apiUrl}/v1/cells${qs ? `?${qs}` : ''}`;
    const resp = await fetch(url, {
      method: 'GET',
      headers: this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {},
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error((err as any).error ?? `Failed to list sandboxes: HTTP ${resp.status}`);
    }
    const items = await resp.json() as Record<string, unknown>[];
    this._nextToken = resp.headers.get('x-next-token') ?? undefined;
    this._hasNext = Boolean(this._nextToken);
    return items.map(this._toSandboxInfo);
  }

  private _toSandboxInfo = (raw: Record<string, unknown>): SandboxInfo => {
    const state = (raw.state ?? raw.status ?? 'running') as SandboxState;

    let startedAt: string;
    if (typeof raw.started_at === 'string') {
      startedAt = raw.started_at;
    } else {
      startedAt = new Date(Number(raw.created_at ?? 0)).toISOString();
    }

    let endAt: string;
    if (typeof raw.end_at === 'string') {
      endAt = raw.end_at;
    } else {
      const ms = Number(raw.created_at ?? 0);
      const timeoutMs = Number(raw.timeout_ms ?? 3_600_000);
      endAt = new Date(ms + timeoutMs).toISOString();
    }

    let network: SandboxNetworkOpts | null = null;
    if (raw.network && typeof raw.network === 'object') {
      const n = raw.network as Record<string, unknown>;
      network = {
        ...(n.allow_out !== undefined ? { allowOut: n.allow_out as string[] } : {}),
        ...(n.deny_out !== undefined ? { denyOut: n.deny_out as string[] } : {}),
        ...(n.allow_public_traffic !== undefined ? { allowPublicTraffic: n.allow_public_traffic as boolean } : {}),
        ...(n.mask_request_host !== undefined ? { maskRequestHost: n.mask_request_host as boolean } : {}),
      };
    }

    let lifecycle: SandboxLifecycle | null = null;
    if (raw.lifecycle && typeof raw.lifecycle === 'object') {
      const lc = raw.lifecycle as Record<string, unknown>;
      lifecycle = {
        onTimeout: (lc.on_timeout as 'pause' | 'kill') ?? 'kill',
        ...(lc.auto_resume !== undefined ? { autoResume: lc.auto_resume as boolean } : {}),
      };
    }

    return {
      sandboxId: (raw.sandbox_id ?? raw.cell_id ?? '') as string,
      templateId: (raw.template_id ?? raw.template ?? 'python3') as string,
      metadata: (raw.metadata ?? {}) as Record<string, string>,
      startedAt,
      endAt,
      state,
      cpuCount: Number(raw.cpu_count ?? 1),
      memoryMb: Number(raw.memory_mb ?? 512),
      envdVersion: (raw.envd_version ?? '0.2.0') as string,
      name: (raw.name ?? null) as string | null,
      sandboxDomain: (raw.sandbox_domain ?? null) as string | null,
      allowInternetAccess: (raw.allow_internet_access ?? null) as boolean | null,
      network,
      lifecycle,
      volumeMounts: (raw.volume_mounts ?? []) as Record<string, string>[],
    };
  };
}

// ─── Cell Class ─────────────────────────────────────────────────

export class Cell {
  readonly cellId: string;
  readonly apiUrl: string;
  readonly persistent: boolean;
  readonly template: string;
  readonly volumeId?: string;
  private apiKey?: string;
  private _executions = 0;

  private constructor(
    cellId: string,
    apiUrl: string,
    apiKey?: string,
    persistent = true,
    template = 'python3',
    volumeId?: string,
  ) {
    this.cellId = cellId;
    this.apiUrl = apiUrl;
    this.apiKey = apiKey;
    this.persistent = persistent;
    this.template = template;
    this.volumeId = volumeId;
  }

  /** Create a new cell. This is the main entry point. */
  static async create(options: CellOptions = {}): Promise<Cell> {
    const apiUrl = (options.apiUrl ?? 'http://localhost:8002').replace(/\/$/, '');
    const apiKey = options.apiKey ?? (typeof process !== 'undefined' ? process.env.SYNAPSE_API_KEY : undefined);
    const persistent = options.persistent ?? true;
    const template = options.template ?? 'python3';
    const timeoutMs = options.timeoutMs ?? 3_600_000;

    const reqBody: Record<string, unknown> = {
      template,
      persistent,
      timeout_ms: timeoutMs,
    };
    if (options.volumeId) {
      reqBody.volume_id = options.volumeId;
    }
    if (options.metadata) {
      reqBody.metadata = options.metadata;
    }
    if (options.envs) {
      reqBody.envs = options.envs;
    }
    if (options.allowInternetAccess !== undefined) {
      reqBody.allow_internet_access = options.allowInternetAccess;
    }
    if (options.network) {
      reqBody.network = {
        ...(options.network.allowOut !== undefined ? { allow_out: options.network.allowOut } : {}),
        ...(options.network.denyOut !== undefined ? { deny_out: options.network.denyOut } : {}),
        ...(options.network.allowPublicTraffic !== undefined ? { allow_public_traffic: options.network.allowPublicTraffic } : {}),
        ...(options.network.maskRequestHost !== undefined ? { mask_request_host: options.network.maskRequestHost } : {}),
      };
    }
    if (options.volumeMounts) {
      reqBody.volume_mounts = Object.entries(options.volumeMounts).map(
        ([mountPath, volName]) => ({ path: mountPath, name: volName })
      );
    }
    if (options.lifecycle) {
      reqBody.lifecycle = {
        on_timeout: options.lifecycle.onTimeout,
        ...(options.lifecycle.autoResume !== undefined ? { auto_resume: options.lifecycle.autoResume } : {}),
      };
    }
    if (options.secure !== undefined) {
      reqBody.secure = options.secure;
    }

    const resp = await fetch(`${apiUrl}/v1/cells`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      body: JSON.stringify(reqBody),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error ?? `Failed to create cell: HTTP ${resp.status}`);
    }

    const info = await resp.json();
    return new Cell(info.cell_id, apiUrl, apiKey, persistent, template, options.volumeId);
  }

  /**
   * Attach to an existing running Cell by ID. Matches E2B's
   * Sandbox.connect(sandboxId) shape. No new cell is created; the
   * gateway is asked for the existing cell's info and a fresh Cell
   * object is bound to it.
   *
   * @throws Error if the cell does not exist or has been killed.
   */
  static async connect(
    cellId: string,
    options: { apiUrl?: string; apiKey?: string } = {},
  ): Promise<Cell> {
    const apiUrl = (options.apiUrl ?? 'http://localhost:8002').replace(/\/$/, '');
    const apiKey = options.apiKey ?? (typeof process !== 'undefined' ? process.env.SYNAPSE_API_KEY : undefined);

    const resp = await fetch(`${apiUrl}/v1/cells/${cellId}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
    });
    if (resp.status === 404) {
      throw new Error(`Cell not found: ${cellId}`);
    }
    if (!resp.ok) {
      throw new Error(`Failed to connect to cell ${cellId}: HTTP ${resp.status}`);
    }
    const info = await resp.json() as Record<string, unknown>;
    const state = (info.state ?? info.status) as string | undefined;
    if (state === 'killed') {
      throw new Error(`Cell ${cellId} has been killed`);
    }
    const template = (info.template ?? info.template_id ?? 'python3') as string;
    const persistent = (info.persistent ?? true) as boolean;
    const volumeId = info.volume_id as string | undefined;
    return new (Cell as any)(
      info.cell_id ?? info.sandbox_id ?? cellId,
      apiUrl,
      apiKey,
      persistent,
      template,
      volumeId,
    );
  }

  /**
   * List cells with pagination and optional filters.
   * Returns a SandboxPaginator; call nextItems() until hasNext is false.
   */
  static list(options: SandboxListOptions = {}): SandboxPaginator {
    return new SandboxPaginator(options);
  }

  /** Number of exec calls made on this cell */
  get executions(): number {
    return this._executions;
  }

  // ─── Core API ──────────────────────────────────────────────

  /**
   * Execute code in the cell. State persists between calls.
   *
   * When streaming callbacks are provided (onStdout, onStderr, etc.),
   * uses the SSE streaming endpoint for real-time output. Otherwise
   * falls back to the blocking POST endpoint.
   *
   * @param code - Code string to execute
   * @param optionsOrLanguage - Language string (back-compat) or RunOptions
   */
  async run(code: string, optionsOrLanguage?: string | RunOptions): Promise<CellResult> {
    // Parse args — backward compat: if string, it's the language param
    const options: RunOptions = typeof optionsOrLanguage === 'string'
      ? { language: optionsOrLanguage }
      : (optionsOrLanguage ?? {});
    const { language, onStdout, onStderr, onResult, onError } = options;

    if (onStdout || onStderr || onResult || onError) {
      // SSE streaming path
      return this._runStreaming(code, options);
    }

    // Existing blocking path (unchanged)
    const body: Record<string, string> = { code };
    if (language) body.language = language;

    const result = await this._request<CellResult>(
      'POST',
      `/v1/cells/${this.cellId}/exec`,
      body,
    );
    this._executions++;
    return result;
  }

  /**
   * Internal: SSE streaming execution via fetch + ReadableStream.
   * Hits POST /v1/cells/{cellId}/exec/stream and parses SSE events,
   * firing callbacks as they arrive.
   */
  private async _runStreaming(code: string, options: RunOptions): Promise<CellResult> {
    const resp = await fetch(`${this.apiUrl}/v1/cells/${this.cellId}/exec/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {}),
      },
      body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(`Stream failed: HTTP ${resp.status}`);

    const stdoutParts: string[] = [];
    const stderrParts: string[] = [];
    let exitCode = 0;
    let latencyMs = 0;

    const reader = resp.body?.getReader();
    if (!reader) throw new Error('No response body for streaming');
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === 'stdout') {
            stdoutParts.push(event.text ?? '');
            options.onStdout?.(event.text ?? '');
          } else if (event.type === 'stderr') {
            stderrParts.push(event.text ?? '');
            options.onStderr?.(event.text ?? '');
          } else if (event.type === 'result') {
            exitCode = event.exit_code ?? 0;
            latencyMs = event.latency_ms ?? 0;
            options.onResult?.(event);
          } else if (event.type === 'error') {
            options.onError?.(event.message ?? JSON.stringify(event));
          }
        } catch { /* skip malformed SSE lines */ }
      }
    }
    this._executions++;
    return {
      stdout: stdoutParts.join('\n'),
      stderr: stderrParts.join('\n'),
      exit_code: exitCode,
      latency_ms: latencyMs,
    };
  }

  /**
   * Connect to the Interactive PTY Terminal via WebSockets.
   * Enables stateful, persistent stream execution into the sandbox without HTTP overhead.
   * 
   * @returns WebSocket instance connected to the Cell REPL
   */
  terminal(): WebSocket {
    if (!this.persistent) {
      throw new Error("Interactive WebSockets require a persistent Cell.");
    }
    const wsUrl = this.apiUrl.replace(/^http/, 'ws') + `/v1/cells/${this.cellId}/ws`;
    return new WebSocket(wsUrl);
  }

  /**
   * Execute a shell command in the cell (ls, cat, echo, mkdir, etc.)
   *
   * Supports streaming callbacks (onStdout/onStderr) and background
   * execution. When ``background`` is true, returns a {@link CommandHandle}
   * for polling status and output.
   *
   * @param cmd - Shell command string
   * @param options - Streaming callbacks and/or background flag
   */
  async command(cmd: string, options?: CommandOptions): Promise<CellResult | CommandHandle> {
    if (options?.background) {
      // Background mode — returns handle immediately
      const body = await this._request<Record<string, unknown>>(
        'POST', `/v1/cells/${this.cellId}/cmd`, { command: cmd, background: true });
      return new CommandHandle(body.command_id as string, this);
    }
    if (options?.onStdout || options?.onStderr) {
      // Streaming via subprocess wrapper
      const wrapperCode = `import subprocess, sys\nresult = subprocess.run(${JSON.stringify(cmd)}, shell=True, capture_output=True, text=True)\nif result.stdout: print(result.stdout, end="")\nif result.stderr: print(result.stderr, end="", file=sys.stderr)\nsys.exit(result.returncode)`;
      return this.run(wrapperCode, { onStdout: options.onStdout, onStderr: options.onStderr });
    }
    // Existing blocking path
    const result = await this._request<CellResult>(
      'POST',
      `/v1/cells/${this.cellId}/cmd`,
      { command: cmd },
    );
    this._executions++;
    return result;
  }

  /** Fetch a URL via the host's network stack. */
  async fetch(url: string, options: FetchOptions = {}): Promise<FetchResult> {
    return this._request<FetchResult>(
      'POST',
      `/v1/cells/${this.cellId}/fetch`,
      {
        url,
        method: options.method ?? 'GET',
        ...(options.body ? { body: options.body } : {}),
        ...(options.saveTo ? { save_to: options.saveTo } : {}),
        timeout_secs: options.timeoutSecs ?? 30,
      },
    );
  }

  /** Write a file to the cell's /data/ directory. */
  async writeFile(path: string, content: string): Promise<void> {
    await this._request('POST', `/v1/cells/${this.cellId}/files`, {
      path,
      content,
    });
  }

  /** Read a file from the cell's /data/ directory. */
  async readFile(path: string): Promise<string> {
    const result = await this._request<{ content?: string }>(
      'GET',
      `/v1/cells/${this.cellId}/files?path=${encodeURIComponent(path)}`,
    );
    return result.content ?? '';
  }

  /** List files in the cell's /data/ directory. Returns rich EntryInfo objects. */
  async listFiles(path: string = ''): Promise<EntryInfo[]> {
    const result = await this._request<{ files?: unknown[] } | unknown[]>(
      'GET',
      `/v1/cells/${this.cellId}/files/list?path=${encodeURIComponent(path)}`,
    );
    let filesRaw: unknown[];
    if (Array.isArray(result)) {
      filesRaw = result;
    } else if (result && typeof result === 'object' && 'files' in result) {
      filesRaw = (result as { files?: unknown[] }).files ?? [];
    } else {
      filesRaw = [];
    }
    // Handle both old (string[]) and new (object[]) response shapes
    if (filesRaw.length > 0 && typeof filesRaw[0] === 'string') {
      return filesRaw.map((f) => Cell._toEntryInfo({ name: f as string, path: f as string }));
    }
    return filesRaw.map((f) => Cell._toEntryInfo(f as Record<string, unknown>));
  }

  /** Check if a file or directory exists in the cell's /data/ directory. */
  async fileExists(path: string): Promise<boolean> {
    const result = await this._request<{ exists?: boolean }>(
      'GET',
      `/v1/cells/${this.cellId}/files/exists?path=${encodeURIComponent(path)}`,
    );
    return result.exists ?? false;
  }

  /** Get metadata about a file or directory. */
  async fileInfo(path: string): Promise<EntryInfo> {
    const result = await this._request<Record<string, unknown>>(
      'GET',
      `/v1/cells/${this.cellId}/files/info?path=${encodeURIComponent(path)}`,
    );
    return Cell._toEntryInfo(result);
  }

  /** Remove a file or directory from the cell. */
  async removeFile(path: string): Promise<void> {
    await this._request(
      'DELETE',
      `/v1/cells/${this.cellId}/files?path=${encodeURIComponent(path)}`,
    );
  }

  /** Create a directory (and parents) in the cell. */
  async makeDir(path: string): Promise<void> {
    await this._request('POST', `/v1/cells/${this.cellId}/files/mkdir`, { path });
  }

  /** Rename/move a file or directory within the cell. */
  async renameFile(oldPath: string, newPath: string): Promise<EntryInfo> {
    const result = await this._request<Record<string, unknown>>(
      'POST',
      `/v1/cells/${this.cellId}/files/rename`,
      { old_path: oldPath, new_path: newPath },
    );
    return Cell._toEntryInfo(result);
  }

  /** Convert snake_case wire format to camelCase EntryInfo. */
  private static _toEntryInfo(raw: Record<string, unknown>): EntryInfo {
    return {
      name: (raw.name ?? '') as string,
      type: (raw.type ?? null) as 'file' | 'dir' | null,
      path: (raw.path ?? '') as string,
      size: Number(raw.size ?? 0),
      mode: Number(raw.mode ?? 0),
      permissions: (raw.permissions ?? '') as string,
      owner: (raw.owner ?? 'sandbox') as string,
      group: (raw.group ?? 'sandbox') as string,
      modifiedTime: (raw.modified_time ?? raw.modifiedTime ?? null) as string | null,
      symlinkTarget: (raw.symlink_target ?? raw.symlinkTarget ?? null) as string | null,
    };
  }

  /** Kill (destroy) the cell and clean up resources. */
  async kill(): Promise<void> {
    try {
      await this._request('DELETE', `/v1/cells/${this.cellId}`);
    } catch {
      // Already dead
    }
  }

  /**
   * Get current cell info from the server (raw dict).
   * @deprecated Use {@link getInfo} for a typed SandboxInfo. Kept for back-compat.
   */
  async info(): Promise<Record<string, unknown>> {
    return this._request('GET', `/v1/cells/${this.cellId}`);
  }

  /**
   * Get typed E2B-compatible SandboxInfo for this cell.
   * Hits GET /v1/cells/{cellId} and normalizes snake_case wire format
   * to camelCase TypeScript interface.
   */
  async getInfo(): Promise<SandboxInfo> {
    const raw = await this._request<Record<string, unknown>>(
      'GET',
      `/v1/cells/${this.cellId}`,
    );

    const state = (raw.state ?? raw.status ?? 'running') as SandboxState;

    // started_at: prefer E2B ISO string, else derive from created_at (ms epoch)
    let startedAt: string;
    if (typeof raw.started_at === 'string') {
      startedAt = raw.started_at;
    } else {
      const ms = Number(raw.created_at ?? 0);
      startedAt = new Date(ms).toISOString();
    }

    // end_at: prefer E2B ISO string, else derive from created_at + timeout_ms
    let endAt: string;
    if (typeof raw.end_at === 'string') {
      endAt = raw.end_at;
    } else {
      const ms = Number(raw.created_at ?? 0);
      const timeoutMs = Number(raw.timeout_ms ?? 3_600_000);
      endAt = new Date(ms + timeoutMs).toISOString();
    }

    // network: snake_case -> camelCase
    let network: SandboxNetworkOpts | null = null;
    if (raw.network && typeof raw.network === 'object') {
      const n = raw.network as Record<string, unknown>;
      network = {
        ...(n.allow_out !== undefined ? { allowOut: n.allow_out as string[] } : {}),
        ...(n.deny_out !== undefined ? { denyOut: n.deny_out as string[] } : {}),
        ...(n.allow_public_traffic !== undefined ? { allowPublicTraffic: n.allow_public_traffic as boolean } : {}),
        ...(n.mask_request_host !== undefined ? { maskRequestHost: n.mask_request_host as boolean } : {}),
      };
    }

    // lifecycle: snake_case -> camelCase
    let lifecycle: SandboxLifecycle | null = null;
    if (raw.lifecycle && typeof raw.lifecycle === 'object') {
      const lc = raw.lifecycle as Record<string, unknown>;
      lifecycle = {
        onTimeout: (lc.on_timeout as 'pause' | 'kill') ?? 'kill',
        ...(lc.auto_resume !== undefined ? { autoResume: lc.auto_resume as boolean } : {}),
      };
    }

    return {
      sandboxId: (raw.sandbox_id ?? raw.cell_id ?? '') as string,
      templateId: (raw.template_id ?? raw.template ?? 'python3') as string,
      metadata: (raw.metadata ?? {}) as Record<string, string>,
      startedAt,
      endAt,
      state,
      cpuCount: Number(raw.cpu_count ?? 1),
      memoryMb: Number(raw.memory_mb ?? 512),
      envdVersion: (raw.envd_version ?? '0.2.0') as string,
      name: (raw.name ?? null) as string | null,
      sandboxDomain: (raw.sandbox_domain ?? null) as string | null,
      allowInternetAccess: (raw.allow_internet_access ?? null) as boolean | null,
      network,
      lifecycle,
      volumeMounts: (raw.volume_mounts ?? []) as Record<string, string>[],
    };
  }

  // ─── Internal ──────────────────────────────────────────────

  private async _request<T>(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<T> {
    const resp = await fetch(`${this.apiUrl}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(
        (err as { error?: string }).error ?? `HTTP ${resp.status}`,
      );
    }

    return resp.json() as Promise<T>;
  }

  // ─── Lifecycle Events & Webhooks ───────────────────────────────

  /** Get lifecycle events for this cell (create, pause, resume, kill, etc.). */
  async getLifecycleEvents(): Promise<Record<string, unknown>[]> {
    return this._request<Record<string, unknown>[]>('GET',
      `/v1/cells/${this.cellId}/events`);
  }

  /**
   * Get global lifecycle events across all cells (last 100).
   * @param apiUrl - Gateway URL (default: http://localhost:8002).
   */
  static async getGlobalEvents(
    apiUrl = 'http://localhost:8002',
  ): Promise<Record<string, unknown>[]> {
    const resp = await fetch(`${apiUrl}/v1/events`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json() as Promise<Record<string, unknown>[]>;
  }

  /**
   * Register a webhook to receive lifecycle event notifications.
   *
   * @param url - HTTP endpoint to receive POST callbacks.
   * @param events - Event types to subscribe to (default: ["*"] for all).
   * @param apiUrl - Gateway URL.
   * @returns Webhook registration with webhook_id, url, events, created_at.
   */
  static async registerWebhook(
    url: string,
    events: string[] = ['*'],
    apiUrl = 'http://localhost:8002',
  ): Promise<Record<string, unknown>> {
    const resp = await fetch(`${apiUrl}/v1/webhooks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, events }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json() as Promise<Record<string, unknown>>;
  }

  /**
   * List all registered webhooks.
   * @param apiUrl - Gateway URL.
   */
  static async listWebhooks(
    apiUrl = 'http://localhost:8002',
  ): Promise<Record<string, unknown>[]> {
    const resp = await fetch(`${apiUrl}/v1/webhooks`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json() as Promise<Record<string, unknown>[]>;
  }

  /**
   * Delete a webhook by ID.
   * @param webhookId - ID of the webhook to remove.
   * @param apiUrl - Gateway URL.
   */
  static async deleteWebhook(
    webhookId: string,
    apiUrl = 'http://localhost:8002',
  ): Promise<Record<string, unknown>> {
    const resp = await fetch(`${apiUrl}/v1/webhooks/${webhookId}`, {
      method: 'DELETE',
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json() as Promise<Record<string, unknown>>;
  }

  toString(): string {
    const status = this.persistent ? 'persistent' : 'ephemeral';
    return `Cell(${this.cellId.slice(0, 8)}... ${status} ${this.template} execs=${this._executions})`;
  }
}

// ─── Convenience Function ───────────────────────────────────────

/** One-shot code execution (ephemeral cell). */
export async function run(
  code: string,
  options: CellOptions = {},
): Promise<CellResult> {
  const cell = await Cell.create({ ...options, persistent: false });
  try {
    return await cell.run(code);
  } finally {
    await cell.kill();
  }
}

// ─── E2B-Compatible Sandbox Wrapper ────────────────────────────

/** E2B Sandbox-compatible options (camelCase surface) */
export interface SandboxOptions extends CellOptions {
  /** API key (alias for apiKey, E2B compat) */
  apiKey?: string;
}

/**
 * E2B-compatible filesystem adapter for Sandbox.
 * Provides sandbox.files.write/read/list/exists/getInfo/remove/makeDir/rename.
 * Normalizes E2B-style /home/user/ paths to Cell's /data/ convention.
 */
class SandboxFilesystemAdapter {
  private _cell: Cell;

  constructor(cell: Cell) {
    this._cell = cell;
  }

  private _normalizePath(p: string): string {
    for (const prefix of ['/home/user/', '/home/', '/tmp/']) {
      if (p.startsWith(prefix)) {
        p = p.slice(prefix.length);
        break;
      }
    }
    return p.replace(/^\/+/, '');
  }

  /** Write a file. E2B API: sandbox.files.write('/path', 'content') */
  async write(path: string, data: string): Promise<void> {
    return this._cell.writeFile(this._normalizePath(path), data);
  }

  /** Read a file. E2B API: content = sandbox.files.read('/path') */
  async read(path: string): Promise<string> {
    return this._cell.readFile(this._normalizePath(path));
  }

  /** List files. E2B API: files = sandbox.files.list('/path') */
  async list(path: string = ''): Promise<EntryInfo[]> {
    return this._cell.listFiles(this._normalizePath(path));
  }

  /** Check if a file exists. E2B API: sandbox.files.exists('/path') */
  async exists(path: string): Promise<boolean> {
    return this._cell.fileExists(this._normalizePath(path));
  }

  /** Get file metadata. E2B API: sandbox.files.getInfo('/path') */
  async getInfo(path: string): Promise<EntryInfo> {
    return this._cell.fileInfo(this._normalizePath(path));
  }

  /** Remove a file or directory. E2B API: sandbox.files.remove('/path') */
  async remove(path: string): Promise<void> {
    return this._cell.removeFile(this._normalizePath(path));
  }

  /** Create a directory. E2B API: sandbox.files.makeDir('/path') */
  async makeDir(path: string): Promise<void> {
    return this._cell.makeDir(this._normalizePath(path));
  }

  /** Rename/move. E2B API: sandbox.files.rename('/old', '/new') */
  async rename(oldPath: string, newPath: string): Promise<EntryInfo> {
    return this._cell.renameFile(this._normalizePath(oldPath), this._normalizePath(newPath));
  }
}

/**
 * E2B-compatible Sandbox class wrapping a Cell internally.
 *
 * Provides the familiar E2B surface: Sandbox.create(), sandbox.runCode(),
 * sandbox.kill(), sandbox.id. Thin wrapper — the Python e2b_compat.py
 * is the canonical implementation.
 */
export class Sandbox {
  private _cell: Cell;

  /** E2B-compatible filesystem interface: sandbox.files.write/read/list/... */
  readonly files: SandboxFilesystemAdapter;

  private constructor(cell: Cell) {
    this._cell = cell;
    this.files = new SandboxFilesystemAdapter(cell);
  }

  /** Create a new sandbox (E2B factory method). */
  static async create(options: SandboxOptions = {}): Promise<Sandbox> {
    const cell = await Cell.create({
      ...options,
      persistent: options.persistent ?? true,
    });
    return new Sandbox(cell);
  }

  /**
   * Attach to an existing running sandbox by ID. E2B-compatible
   * static factory — delegates to Cell.connect and wraps the result.
   *
   * @throws Error if the sandbox does not exist or has been killed.
   */
  static async connect(
    sandboxId: string,
    options: { apiUrl?: string; apiKey?: string } = {},
  ): Promise<Sandbox> {
    const cell = await Cell.connect(sandboxId, options);
    return new Sandbox(cell);
  }

  /**
   * List sandboxes with pagination and optional filters.
   * Returns a SandboxPaginator; call nextItems() until hasNext is false.
   * Delegates to Cell.list().
   */
  static list(options: SandboxListOptions = {}): SandboxPaginator {
    return Cell.list(options);
  }

  /** Sandbox ID (E2B compat). */
  get id(): string {
    return this._cell.cellId;
  }

  /**
   * Execute code in the sandbox (E2B: sandbox.runCode).
   *
   * Supports streaming callbacks via RunOptions. When callbacks are
   * provided, uses SSE streaming for real-time output.
   */
  async runCode(code: string, options?: RunOptions): Promise<CellResult> {
    return this._cell.run(code, options);
  }

  /**
   * Execute a shell command in the sandbox.
   *
   * Supports streaming callbacks and background execution via
   * CommandOptions. Delegates to Cell.command().
   */
  async command(cmd: string, options?: CommandOptions): Promise<CellResult | CommandHandle> {
    return this._cell.command(cmd, options);
  }

  /** Get typed E2B-compatible SandboxInfo for this sandbox. */
  async getInfo(): Promise<SandboxInfo> {
    return this._cell.getInfo();
  }

  /** Kill the sandbox and free resources. */
  async kill(): Promise<void> {
    return this._cell.kill();
  }

  toString(): string {
    return `Sandbox(id=${this.id.slice(0, 12)}... synapse-powered)`;
  }
}

// ─── CommandHandle ──────────────────────────────────────────────

/**
 * Handle for a background command. Returned by Cell.command() when
 * ``background: true`` is set in CommandOptions.
 *
 * Properties are async — they poll the gateway and cache terminal
 * states (``completed`` or ``failed``). Use {@link wait} to block
 * until the command finishes.
 *
 * @example
 * ```typescript
 * const handle = await cell.command('sleep 2 && echo done', { background: true });
 * if (handle instanceof CommandHandle) {
 *   await handle.wait();
 *   console.log(await handle.stdout);   // "done\n"
 *   console.log(await handle.exitCode); // 0
 * }
 * ```
 */
export class CommandHandle {
  readonly commandId: string;
  private _cell: Cell;
  private _data?: Record<string, unknown>;

  constructor(commandId: string, cell: Cell) {
    this.commandId = commandId;
    this._cell = cell;
  }

  private async _fetch(): Promise<Record<string, unknown>> {
    if (this._data && ['completed', 'failed'].includes(this._data.status as string)) {
      return this._data;
    }
    this._data = await (this._cell as any)._request('GET',
      `/v1/cells/${this._cell.cellId}/commands/${this.commandId}`);
    return this._data!;
  }

  /** True while the command is still executing. */
  get isRunning(): Promise<boolean> {
    return this._fetch().then(d => d.status === 'running');
  }

  /** Standard output captured so far (or final, if completed). */
  get stdout(): Promise<string> {
    return this._fetch().then(d => (d.stdout as string) ?? '');
  }

  /** Standard error captured so far (or final, if completed). */
  get stderr(): Promise<string> {
    return this._fetch().then(d => (d.stderr as string) ?? '');
  }

  /** Process exit code. null while still running. */
  get exitCode(): Promise<number | null> {
    return this._fetch().then(d => (d.exit_code as number) ?? null);
  }

  /**
   * Block until the command completes (polls every 100 ms).
   * @param timeoutMs - Maximum time to wait in milliseconds (default 30 s).
   * @returns this, for chaining.
   */
  async wait(timeoutMs = 30000): Promise<this> {
    const deadline = Date.now() + timeoutMs;
    while (await this.isRunning && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 100));
    }
    return this;
  }

  /** Kill the background command. */
  async kill(): Promise<void> {
    try {
      await (this._cell as any)._request('DELETE',
        `/v1/cells/${this._cell.cellId}/commands/${this.commandId}`);
    } catch { /* already dead */ }
  }
}

// ─── EdgeCell (Local Wasm Sandbox) ──────────────────────────────

import { createFfiImports, EdgeExecutionState } from './ffi_runtime.js';
import { SynapseLease, validateLease, checkFeatureAccess, computeNodeId } from './atlantic_handshake.js';
import * as webgpu from './webgpu_engine.js';
import * as fs from 'fs';
import * as path from 'path';

export class EdgeCell {
  readonly persistent: boolean;
  private _executions = 0;
  private compilerInstance: WebAssembly.Instance | null = null;
  private compilerMemory: WebAssembly.Memory | null = null;
  private lease: SynapseLease | null = null;
  private leaseValid = false;

  private constructor(persistent = false) {
    this.persistent = persistent;
  }

  static async create(options: CellOptions = {}): Promise<EdgeCell> {
    const cell = new EdgeCell(options.persistent ?? false);
    await cell._loadLease();
    await cell._initCompiler();
    return cell;
  }

  /** Load and validate the license lease from env or file */
  private async _loadLease() {
    // Try environment variable first, then ~/.synapse/lease.json
    const leaseData = process.env.SYNAPSE_LEASE 
      || (() => {
        const leasePath = path.join(process.env.HOME || '~', '.synapse', 'lease.json');
        try { return fs.readFileSync(leasePath, 'utf-8'); } catch { return null; }
      })();

    if (!leaseData) {
      console.log('[EdgeCell] No license lease found — running in Free tier (basic sandbox only)');
      console.log('[EdgeCell] Set SYNAPSE_LICENSE_KEY to unlock Heavy FFI features');
      return;
    }

    try {
      this.lease = JSON.parse(leaseData);
      // In production, SYNAPSE_PUBLIC_KEY would be baked into compiler.wasm
      const pubKey = process.env.SYNAPSE_PUBLIC_KEY || '';
      if (pubKey) {
        const result = validateLease(this.lease!, pubKey);
        this.leaseValid = result.valid;
        if (!result.valid) {
          console.warn(`[EdgeCell] ⚠️  Lease invalid: ${result.reason}`);
        } else {
          console.log(`[EdgeCell] ✅ Pro license active — tier=${this.lease!.tier}, expires=${new Date(this.lease!.expires_at * 1000).toISOString()}`);
        }
      } else {
        // No public key available — trust the lease (dev mode)
        this.leaseValid = true;
        console.log(`[EdgeCell] License loaded (dev mode) — tier=${this.lease!.tier}`);
      }
    } catch (e) {
      console.warn('[EdgeCell] Failed to parse lease — running in Free tier');
    }
  }

  private async _initCompiler() {
    const wasmPath = path.join(__dirname, 'wasm', 'compiler.wasm');
    const wasmBytes = fs.readFileSync(wasmPath);
    
    const wasmModule = await WebAssembly.instantiate(wasmBytes, {});
    this.compilerInstance = wasmModule.instance;
    this.compilerMemory = this.compilerInstance.exports.memory as WebAssembly.Memory;
  }

  /** Check if a specific FFI feature is unlocked by the current lease */
  hasFeature(ffiName: string): boolean {
    const result = checkFeatureAccess(ffiName, this.leaseValid ? this.lease : null);
    return result.allowed;
  }

  /** Get the current license tier */
  get tier(): string {
    return this.leaseValid && this.lease ? this.lease.tier : 'free';
  }

  /** Initialize the Browser WebGPU Subsystem for 0ms Native Inference */
  async initGPU(): Promise<boolean> {
      try {
          return await webgpu.initWebGPU();
      } catch (e) {
          console.error("[EdgeCell] Failed to initialize WebGPU:", e);
          return false;
      }
  }

  /** Load a Safetensors open-weights model directly into WebGPU from a URL */
  async loadGPUModel(url: string, config: webgpu.QwenConfig & { max_seq_len: number }, progressCallback?: (p: number) => void): Promise<webgpu.QwenModel> {
      // Dynamic import to keep init payload small if webgpu isn't used
      const safetensors = await import('./safetensors.js');
      console.log(`[EdgeCell] Fetching safetensors from ${url}`);
      const tensors = await safetensors.fetchSafetensors(url, progressCallback);
      console.log(`[EdgeCell] Model chunks loaded into ArrayBuffer. Mapping architecture...`);
      return webgpu.loadQwenFromSafetensors(tensors, config);
  }

  private meshNetwork?: any; // Will load dynamically

  /** Execute a native transformer loop purely in the browser using WebGPU */
  async gpuInference(
      model: webgpu.QwenModel, 
      token: number | null,
      layerStart?: number,
      layerEnd?: number,
      incomingState?: Float32Array
  ): Promise<Float32Array> {
      if (!this.hasFeature('gpu_matmul')) {
          throw new Error('Tier 2 PRO license or active Shared Compute Session required for WebGPU offloading.');
      }
      return await webgpu.forward_one(model, token, layerStart, layerEnd, incomingState);
  }

  /**
   * Initializes the Atlantic Shared Compute mesh protocol, opening WebRTC channels
   * to automatically hand off floating point tensor vectors when computing partial slices.
   */
  async initMesh(signalingUrl: string, onStateReceived: (tokenIdx: number, state: Float32Array, receipt: any) => void) {
      const mesh = await import('./mesh_sharding.js');
      this.meshNetwork = new mesh.MeshNetwork(signalingUrl, onStateReceived);
  }

  /**
   * Broadcasts a computed hidden state to the next shard over the WebRTC network.
   * Splits into 16KB UDP chunks automatically.
   */
  async meshBroadcast(tokenIdx: number, state: Float32Array, receipt: any) {
      if (!this.meshNetwork) throw new Error("Mesh network not initialized!");
      this.meshNetwork.broadcastState(tokenIdx, state, receipt);
  }

  async run(code: string): Promise<CellResult> {
    const startTime = performance.now();
    
    const encoder = new TextEncoder();
    const codeBytes = encoder.encode(code);
    
    const allocFn = this.compilerInstance!.exports.alloc as (len: number) => number;
    const compileFn = this.compilerInstance!.exports.compile_python as (ptr: number, len: number) => number;
    const deallocFn = this.compilerInstance!.exports.dealloc as (ptr: number, len: number) => void;
    
    const ptr = allocFn(codeBytes.length);
    const memoryView = new Uint8Array(this.compilerMemory!.buffer);
    memoryView.set(codeBytes, ptr);
    
    const outPtr = compileFn(ptr, codeBytes.length);
    deallocFn(ptr, codeBytes.length);
    
    if (outPtr === 0) {
      return {
        stdout: "",
        stderr: "SyntaxError: Failed to transpile Python in APC local engine. Advanced feature not supported locally.",
        exit_code: 1,
        latency_ms: performance.now() - startTime
      };
    }
    
    const dataView = new DataView(this.compilerMemory!.buffer);
    const wasmLen = dataView.getUint32(outPtr, true);
    const payloadWasm = new Uint8Array(this.compilerMemory!.buffer, outPtr + 4, wasmLen);
    
    // Create FFI imports with lease-gated access
    const state: EdgeExecutionState = { stdout: "" };
    const rawImports = createFfiImports(state);

    // Gate heavy FFI: if no valid lease, wrap gated functions to return error sentinel
    const activeLease = this.leaseValid ? this.lease : null;
    const gatedEnv: Record<string, Function> = {};
    for (const [name, fn] of Object.entries(rawImports.env)) {
      const access = checkFeatureAccess(name, activeLease);
      if (!access.allowed) {
        // Replace with a function that logs the rejection and returns -999 (license error)
        gatedEnv[name] = (..._args: any[]) => {
          state.stdout += `\n[LICENSE] ${access.reason}\n`;
          return -999n;
        };
      } else {
        gatedEnv[name] = fn as Function;
      }
    }

    let exitCode = 0;
    try {
      const execModule = await WebAssembly.instantiate(payloadWasm, { env: gatedEnv });
      state.memory = execModule.instance.exports.memory as WebAssembly.Memory;
      
      const mainFn = execModule.instance.exports._start as () => void;
      if (mainFn) {
        mainFn();
      }
    } catch (e: any) {
      exitCode = 1;
      state.stdout += `\nRuntimeError: ${e.message}`;
    }
    
    this._executions++;
    
    return {
      stdout: state.stdout,
      stderr: "",
      exit_code: exitCode,
      latency_ms: performance.now() - startTime,
      receipt: {
        execution_id: `edge_${Math.random().toString(36).substring(2,10)}`,
        code_hash: "edge_unverified",
        result_hash: "edge_unverified",
        template: "python3-edge-wasm",
        timestamp: Date.now()
      }
    };
  }

  async kill(): Promise<void> {
    this.compilerInstance = null;
    this.compilerMemory = null;
    this.lease = null;
  }
}
