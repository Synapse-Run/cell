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
// ─── Cell Class ─────────────────────────────────────────────────
export class Cell {
    cellId;
    apiUrl;
    persistent;
    template;
    volumeId;
    apiKey;
    _executions = 0;
    constructor(cellId, apiUrl, apiKey, persistent = true, template = 'python3', volumeId) {
        this.cellId = cellId;
        this.apiUrl = apiUrl;
        this.apiKey = apiKey;
        this.persistent = persistent;
        this.template = template;
        this.volumeId = volumeId;
    }
    /** Create a new cell. This is the main entry point. */
    static async create(options = {}) {
        const apiUrl = (options.apiUrl ?? 'http://localhost:8002').replace(/\/$/, '');
        const apiKey = options.apiKey ?? (typeof process !== 'undefined' ? process.env.SYNAPSE_API_KEY : undefined);
        const persistent = options.persistent ?? true;
        const template = options.template ?? 'python3';
        const timeoutMs = options.timeoutMs ?? 3_600_000;
        const reqBody = {
            template,
            persistent,
            timeout_ms: timeoutMs,
        };
        if (options.volumeId) {
            reqBody.volume_id = options.volumeId;
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
    /** Number of exec calls made on this cell */
    get executions() {
        return this._executions;
    }
    // ─── Core API ──────────────────────────────────────────────
    /** Execute code in the cell. State persists between calls. */
    async run(code, language) {
        const body = { code };
        if (language)
            body.language = language;
        const result = await this._request('POST', `/v1/cells/${this.cellId}/exec`, body);
        this._executions++;
        return result;
    }
    /**
     * Connect to the Interactive PTY Terminal via WebSockets.
     * Enables stateful, persistent stream execution into the sandbox without HTTP overhead.
     *
     * @returns WebSocket instance connected to the Cell REPL
     */
    terminal() {
        if (!this.persistent) {
            throw new Error("Interactive WebSockets require a persistent Cell.");
        }
        const wsUrl = this.apiUrl.replace(/^http/, 'ws') + `/v1/cells/${this.cellId}/ws`;
        return new WebSocket(wsUrl);
    }
    /** Execute a shell command in the cell (ls, cat, echo, mkdir, etc.) */
    async command(cmd) {
        const result = await this._request('POST', `/v1/cells/${this.cellId}/cmd`, { command: cmd });
        this._executions++;
        return result;
    }
    /** Fetch a URL via the host's network stack. */
    async fetch(url, options = {}) {
        return this._request('POST', `/v1/cells/${this.cellId}/fetch`, {
            url,
            method: options.method ?? 'GET',
            ...(options.body ? { body: options.body } : {}),
            ...(options.saveTo ? { save_to: options.saveTo } : {}),
            timeout_secs: options.timeoutSecs ?? 30,
        });
    }
    /** Write a file to the cell's /data/ directory. */
    async writeFile(path, content) {
        await this._request('POST', `/v1/cells/${this.cellId}/files`, {
            path,
            content,
        });
    }
    /** Kill (destroy) the cell and clean up resources. */
    async kill() {
        try {
            await this._request('DELETE', `/v1/cells/${this.cellId}`);
        }
        catch {
            // Already dead
        }
    }
    /** Get current cell info from the server. */
    async info() {
        return this._request('GET', `/v1/cells/${this.cellId}`);
    }
    // ─── Internal ──────────────────────────────────────────────
    async _request(method, path, body) {
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
            throw new Error(err.error ?? `HTTP ${resp.status}`);
        }
        return resp.json();
    }
    toString() {
        const status = this.persistent ? 'persistent' : 'ephemeral';
        return `Cell(${this.cellId.slice(0, 8)}... ${status} ${this.template} execs=${this._executions})`;
    }
}
// ─── Convenience Function ───────────────────────────────────────
/** One-shot code execution (ephemeral cell). */
export async function run(code, options = {}) {
    const cell = await Cell.create({ ...options, persistent: false });
    try {
        return await cell.run(code);
    }
    finally {
        await cell.kill();
    }
}
// ─── EdgeCell (Local Wasm Sandbox) ──────────────────────────────
import { createFfiImports } from './ffi_runtime.js';
import { validateLease, checkFeatureAccess } from './atlantic_handshake.js';
import * as webgpu from './webgpu_engine.js';
import * as fs from 'fs';
import * as path from 'path';
export class EdgeCell {
    persistent;
    _executions = 0;
    compilerInstance = null;
    compilerMemory = null;
    lease = null;
    leaseValid = false;
    constructor(persistent = false) {
        this.persistent = persistent;
    }
    static async create(options = {}) {
        const cell = new EdgeCell(options.persistent ?? false);
        await cell._loadLease();
        await cell._initCompiler();
        return cell;
    }
    /** Load and validate the license lease from env or file */
    async _loadLease() {
        // Try environment variable first, then ~/.synapse/lease.json
        const leaseData = process.env.SYNAPSE_LEASE
            || (() => {
                const leasePath = path.join(process.env.HOME || '~', '.synapse', 'lease.json');
                try {
                    return fs.readFileSync(leasePath, 'utf-8');
                }
                catch {
                    return null;
                }
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
                const result = validateLease(this.lease, pubKey);
                this.leaseValid = result.valid;
                if (!result.valid) {
                    console.warn(`[EdgeCell] ⚠️  Lease invalid: ${result.reason}`);
                }
                else {
                    console.log(`[EdgeCell] ✅ Pro license active — tier=${this.lease.tier}, expires=${new Date(this.lease.expires_at * 1000).toISOString()}`);
                }
            }
            else {
                // No public key available — trust the lease (dev mode)
                this.leaseValid = true;
                console.log(`[EdgeCell] License loaded (dev mode) — tier=${this.lease.tier}`);
            }
        }
        catch (e) {
            console.warn('[EdgeCell] Failed to parse lease — running in Free tier');
        }
    }
    async _initCompiler() {
        const wasmPath = path.join(__dirname, 'wasm', 'compiler.wasm');
        const wasmBytes = fs.readFileSync(wasmPath);
        const wasmModule = await WebAssembly.instantiate(wasmBytes, {});
        this.compilerInstance = wasmModule.instance;
        this.compilerMemory = this.compilerInstance.exports.memory;
    }
    /** Check if a specific FFI feature is unlocked by the current lease */
    hasFeature(ffiName) {
        const result = checkFeatureAccess(ffiName, this.leaseValid ? this.lease : null);
        return result.allowed;
    }
    /** Get the current license tier */
    get tier() {
        return this.leaseValid && this.lease ? this.lease.tier : 'free';
    }
    /** Initialize the Browser WebGPU Subsystem for 0ms Native Inference */
    async initGPU() {
        try {
            return await webgpu.initWebGPU();
        }
        catch (e) {
            console.error("[EdgeCell] Failed to initialize WebGPU:", e);
            return false;
        }
    }
    /** Load a Safetensors open-weights model directly into WebGPU from a URL */
    async loadGPUModel(url, config, progressCallback) {
        // Dynamic import to keep init payload small if webgpu isn't used
        const safetensors = await import('./safetensors.js');
        console.log(`[EdgeCell] Fetching safetensors from ${url}`);
        const tensors = await safetensors.fetchSafetensors(url, progressCallback);
        console.log(`[EdgeCell] Model chunks loaded into ArrayBuffer. Mapping architecture...`);
        return webgpu.loadQwenFromSafetensors(tensors, config);
    }
    meshNetwork; // Will load dynamically
    /** Execute a native transformer loop purely in the browser using WebGPU */
    async gpuInference(model, token, layerStart, layerEnd, incomingState) {
        if (!this.hasFeature('gpu_matmul')) {
            throw new Error('Tier 2 PRO license or active Shared Compute Session required for WebGPU offloading.');
        }
        return await webgpu.forward_one(model, token, layerStart, layerEnd, incomingState);
    }
    /**
     * Initializes the Atlantic Shared Compute mesh protocol, opening WebRTC channels
     * to automatically hand off floating point tensor vectors when computing partial slices.
     */
    async initMesh(signalingUrl, onStateReceived) {
        const mesh = await import('./mesh_sharding.js');
        this.meshNetwork = new mesh.MeshNetwork(signalingUrl, onStateReceived);
    }
    /**
     * Broadcasts a computed hidden state to the next shard over the WebRTC network.
     * Splits into 16KB UDP chunks automatically.
     */
    async meshBroadcast(tokenIdx, state, receipt) {
        if (!this.meshNetwork)
            throw new Error("Mesh network not initialized!");
        this.meshNetwork.broadcastState(tokenIdx, state, receipt);
    }
    async run(code) {
        const startTime = performance.now();
        const encoder = new TextEncoder();
        const codeBytes = encoder.encode(code);
        const allocFn = this.compilerInstance.exports.alloc;
        const compileFn = this.compilerInstance.exports.compile_python;
        const deallocFn = this.compilerInstance.exports.dealloc;
        const ptr = allocFn(codeBytes.length);
        const memoryView = new Uint8Array(this.compilerMemory.buffer);
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
        const dataView = new DataView(this.compilerMemory.buffer);
        const wasmLen = dataView.getUint32(outPtr, true);
        const payloadWasm = new Uint8Array(this.compilerMemory.buffer, outPtr + 4, wasmLen);
        // Create FFI imports with lease-gated access
        const state = { stdout: "" };
        const rawImports = createFfiImports(state);
        // Gate heavy FFI: if no valid lease, wrap gated functions to return error sentinel
        const activeLease = this.leaseValid ? this.lease : null;
        const gatedEnv = {};
        for (const [name, fn] of Object.entries(rawImports.env)) {
            const access = checkFeatureAccess(name, activeLease);
            if (!access.allowed) {
                // Replace with a function that logs the rejection and returns -999 (license error)
                gatedEnv[name] = (..._args) => {
                    state.stdout += `\n[LICENSE] ${access.reason}\n`;
                    return -999n;
                };
            }
            else {
                gatedEnv[name] = fn;
            }
        }
        let exitCode = 0;
        try {
            const execModule = await WebAssembly.instantiate(payloadWasm, { env: gatedEnv });
            state.memory = execModule.instance.exports.memory;
            const mainFn = execModule.instance.exports._start;
            if (mainFn) {
                mainFn();
            }
        }
        catch (e) {
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
                execution_id: `edge_${Math.random().toString(36).substring(2, 10)}`,
                code_hash: "edge_unverified",
                result_hash: "edge_unverified",
                template: "python3-edge-wasm",
                timestamp: Date.now()
            }
        };
    }
    async kill() {
        this.compilerInstance = null;
        this.compilerMemory = null;
        this.lease = null;
    }
}
