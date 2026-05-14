/**
 * WebGPU Bridge for Browser-Native Sharding
 * 
 * Takes the WGSL computation logic established by the other agent in `consume/engine_gpu.py`
 * and executes it via the browser's `navigator.gpu` for 0ms latency, free tiered inference.
 */


let gpuDevice: GPUDevice | null = null;
const pipelineCache: Record<string, GPUComputePipeline> = {};

// Hardcode WGSL to avoid bundler/vite configuration issues for a clean npm package.
const SHADERS = {
    'ternary_matmul.wgsl': `
        struct Dimensions {
            M: u32,
            N: u32,
            K: u32,
            _pad: u32,
        };

        @group(0) @binding(0) var<uniform> dims: Dimensions;
        @group(0) @binding(1) var<storage, read> X: array<f32>;
        @group(0) @binding(2) var<storage, read> W: array<f32>;
        @group(0) @binding(3) var<storage, read_write> C: array<f32>;

        const TILE_SIZE: u32 = 16u;

        var<workgroup> tileX: array<array<f32, 16>, 16>;
        var<workgroup> tileW: array<array<f32, 16>, 16>;

        @compute @workgroup_size(16, 16)
        fn main(
            @builtin(global_invocation_id) global_id: vec3<u32>,
            @builtin(local_invocation_id) local_id: vec3<u32>,
        ) {
            let row = global_id.y;
            let col = global_id.x;

            var acc: f32 = 0.0;
            let numTiles = (dims.N + TILE_SIZE - 1u) / TILE_SIZE;

            for (var t: u32 = 0u; t < numTiles; t++) {
                let xCol = t * TILE_SIZE + local_id.x;
                if (row < dims.M && xCol < dims.N) {
                    tileX[local_id.y][local_id.x] = X[row * dims.N + xCol];
                } else {
                    tileX[local_id.y][local_id.x] = 0.0;
                }

                let wRow = t * TILE_SIZE + local_id.y;
                if (wRow < dims.N && col < dims.K) {
                    tileW[local_id.y][local_id.x] = W[wRow * dims.K + col];
                } else {
                    tileW[local_id.y][local_id.x] = 0.0;
                }

                workgroupBarrier();

                for (var k: u32 = 0u; k < TILE_SIZE; k++) {
                    let w_val = tileW[k][local_id.x];
                    let x_val = tileX[local_id.y][k];

                    if (w_val > 0.5) {
                        acc += x_val;
                    } else if (w_val < -0.5) {
                        acc -= x_val;
                    }
                }

                workgroupBarrier();
            }

            if (row < dims.M && col < dims.K) {
                C[row * dims.K + col] = acc;
            }
        }
    `,
    'silu.wgsl': `
        struct Dimensions {
            length: u32,
            _pad1: u32,
            _pad2: u32,
            _pad3: u32,
        };

        @group(0) @binding(0) var<uniform> dims: Dimensions;
        @group(0) @binding(1) var<storage, read> X: array<f32>;
        @group(0) @binding(2) var<storage, read_write> Out: array<f32>;

        @compute @workgroup_size(256)
        fn main(
            @builtin(global_invocation_id) global_id: vec3<u32>,
        ) {
            let idx = global_id.x;
            if (idx >= dims.length) {
                return;
            }
            let x = X[idx];
            let sigmoid_x = 1.0 / (1.0 + exp(-x));
            Out[idx] = x * sigmoid_x;
        }
    `
};

/** Initialize the WebGPU hardware context */
export async function initWebGPU(): Promise<boolean> {
    if (typeof navigator === 'undefined' || !navigator.gpu) {
        console.warn("[WebGPUEngine] WebGPU not supported on this platform. (Try Chrome/Edge > 113 or enable flags)");
        return false;
    }
    const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
    if (!adapter) {
        console.warn("[WebGPUEngine] RequestAdapter failed.");
        return false;
    }
    gpuDevice = await adapter.requestDevice();
    console.log(`[WebGPUEngine] GPU Initialized: ${adapter.requestAdapterInfo ? (await adapter.requestAdapterInfo()).architecture : 'Unknown GPU'}`);
    return true;
}

/** Get the active WebGPU device */
export function getDevice(): GPUDevice {
    if (!gpuDevice) {
        throw new Error("WebGPU device not initialized. Call initWebGPU() first.");
    }
    return gpuDevice;
}

/** Core Dispatch function masking WebGPU buffer lifecycle */
export async function dispatchCompute(
    shaderName: keyof typeof SHADERS,
    dims: number[], // usually 4 uint32s
    inputArrays: Float32Array[],
    outputSize: number, // in elements
    workgroups: [number, number, number] = [1, 1, 1]
): Promise<Float32Array> {
    const device = getDevice();
    const shaderCode = SHADERS[shaderName];

    if (!pipelineCache[shaderName]) {
        const shaderModule = device.createShaderModule({ code: shaderCode });
        pipelineCache[shaderName] = await device.createComputePipelineAsync({
            layout: 'auto',
            compute: { module: shaderModule, entryPoint: 'main' }
        });
    }
    const pipeline = pipelineCache[shaderName];

    // Uniform buffer (Binding 0)
    const dimsArray = new Uint32Array(dims);
    const dimsBuf = device.createBuffer({
        size: dimsArray.byteLength,
        usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST
    });
    device.queue.writeBuffer(dimsBuf, 0, dimsArray);

    // Storage buffers (Bindings 1..N)
    const inputBufs: GPUBuffer[] = [];
    inputArrays.forEach((arr, i) => {
        const buf = device.createBuffer({
            size: arr.byteLength,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST
        });
        device.queue.writeBuffer(buf, 0, arr);
        inputBufs.push(buf);
    });

    // Output buffer (Binding N+1)
    const outputSizeBytes = outputSize * 4;
    const outputBuf = device.createBuffer({
        size: outputSizeBytes,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC
    });

    // Readback buffer (mapped locally)
    const readbackBuf = device.createBuffer({
        size: outputSizeBytes,
        usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ
    });

    const bindGroupEntries: GPUBindGroupEntry[] = [
        { binding: 0, resource: { buffer: dimsBuf } }
    ];
    inputBufs.forEach((buf, i) => {
        bindGroupEntries.push({ binding: i + 1, resource: { buffer: buf } });
    });
    bindGroupEntries.push({ binding: inputBufs.length + 1, resource: { buffer: outputBuf } });

    const bindGroup = device.createBindGroup({
        layout: pipeline.getBindGroupLayout(0),
        entries: bindGroupEntries
    });

    const commandEncoder = device.createCommandEncoder();
    const computePass = commandEncoder.beginComputePass();
    computePass.setPipeline(pipeline);
    computePass.setBindGroup(0, bindGroup);
    computePass.dispatchWorkgroups(...workgroups);
    computePass.end();

    commandEncoder.copyBufferToBuffer(outputBuf, 0, readbackBuf, 0, outputSizeBytes);
    device.queue.submit([commandEncoder.finish()]);

    await readbackBuf.mapAsync(GPUMapMode.READ);
    const result = new Float32Array(readbackBuf.getMappedRange()).slice();
    readbackBuf.unmap();

    // Cleanup resources to prevent memory leaks (JS GC handles ArrayBuffer backing)
    dimsBuf.destroy();
    inputBufs.forEach(buf => buf.destroy());
    outputBuf.destroy();
    readbackBuf.destroy();

    return result;
}

// ─── FFI-Compatible Tensor Operations ──────────────────────────────

export async function gpu_ternary_matmul(x: Float32Array, w: Float32Array, m: number, n: number, k: number): Promise<Float32Array> {
    const TILE = 16;
    const wg_x = Math.ceil(k / TILE);
    const wg_y = Math.ceil(m / TILE);

    return dispatchCompute(
        'ternary_matmul.wgsl',
        [m, n, k, 0],
        [x, w],
        m * k,
        [wg_x, wg_y, 1]
    );
}

export async function gpu_silu(x: Float32Array, length: number): Promise<Float32Array> {
    const wg_x = Math.ceil(length / 256);

    return dispatchCompute(
        'silu.wgsl',
        [length, 0, 0, 0],
        [x],
        length,
        [wg_x, 1, 1]
    );
}

// ─── Qwen Mathematics Fallbacks (Javascript CPU) ────────────────────

export function np_rms_norm(x: Float32Array, w: Float32Array, eps: number = 1e-6): Float32Array {
    let mean_sq = 0;
    for (let i = 0; i < x.length; i++) {
        mean_sq += x[i] * x[i];
    }
    mean_sq /= x.length;
    const rsqrt = 1.0 / Math.sqrt(mean_sq + eps);
    const result = new Float32Array(x.length);
    for (let i = 0; i < x.length; i++) {
        result[i] = x[i] * w[i] * rsqrt;
    }
    return result;
}

export function np_softmax(x: Float32Array): Float32Array {
    let max_val = -Infinity;
    for (let i = 0; i < x.length; i++) if (x[i] > max_val) max_val = x[i];
    const result = new Float32Array(x.length);
    let sum = 0;
    for (let i = 0; i < x.length; i++) {
        const e_x = Math.exp(x[i] - max_val);
        result[i] = e_x;
        sum += e_x;
    }
    for (let i = 0; i < x.length; i++) {
        result[i] /= sum;
    }
    return result;
}

export function apply_rope(q: Float32Array, k: Float32Array, pos: number, num_heads: number, num_kv_heads: number, head_dim: number, theta: number = 1000000.0) {
    for (let i = 0; i < head_dim; i += 2) {
        const freq = 1.0 / Math.pow(theta, i / head_dim);
        const val = pos * freq;
        const cos_val = Math.cos(val);
        const sin_val = Math.sin(val);

        for (let h = 0; h < num_heads; h++) {
            const base = h * head_dim + i;
            const q0 = q[base], q1 = q[base + 1];
            q[base] = q0 * cos_val - q1 * sin_val;
            q[base + 1] = q1 * cos_val + q0 * sin_val;
        }

        for (let h = 0; h < num_kv_heads; h++) {
            const base = h * head_dim + i;
            const k0 = k[base], k1 = k[base + 1];
            k[base] = k0 * cos_val - k1 * sin_val;
            k[base + 1] = k1 * cos_val + k0 * sin_val;
        }
    }
}

// ─── Qwen Architecture Classes ───────────────────────────────────────

export class QwenLayerGPU {
    apply_sparsity: boolean;
    q_proj: Float32Array; k_proj: Float32Array; v_proj: Float32Array; o_proj: Float32Array;
    gate_proj: Float32Array; up_proj: Float32Array; down_proj: Float32Array;
    input_layernorm: Float32Array; post_attention_layernorm: Float32Array;

    constructor(tensors: Record<string, Float32Array>, i: number, apply_sparsity: boolean = false) {
        this.apply_sparsity = apply_sparsity;
        // In physical loading, these should be [OUT, IN] transposed to [IN, OUT].
        this.q_proj = tensors[`model.layers.${i}.self_attn.q_proj.weight`];
        this.k_proj = tensors[`model.layers.${i}.self_attn.k_proj.weight`];
        this.v_proj = tensors[`model.layers.${i}.self_attn.v_proj.weight`];
        this.o_proj = tensors[`model.layers.${i}.self_attn.o_proj.weight`];
        
        this.gate_proj = tensors[`model.layers.${i}.mlp.gate_proj.weight`];
        this.up_proj = tensors[`model.layers.${i}.mlp.up_proj.weight`];
        this.down_proj = tensors[`model.layers.${i}.mlp.down_proj.weight`];

        this.input_layernorm = tensors[`model.layers.${i}.input_layernorm.weight`];
        this.post_attention_layernorm = tensors[`model.layers.${i}.post_attention_layernorm.weight`];
    }
}

export class KVCache {
    k: Float32Array;
    v: Float32Array;
    pos: number;
    head_dim: number;

    constructor(max_seq_len: number, kv_dim: number, head_dim: number) {
        this.k = new Float32Array(max_seq_len * kv_dim);
        this.v = new Float32Array(max_seq_len * kv_dim);
        this.pos = 0;
        this.head_dim = head_dim;
    }
}

export interface QwenConfig {
    num_layers: number; vocab_size: number; hidden_size: number;
    num_heads: number; num_kv_heads: number; head_dim: number;
    kv_dim: number; intermediate_size: number; apply_sparsity: boolean;
}

export interface QwenModel {
    embed_tokens: Float32Array;
    lm_head: Float32Array;
    norm_weight: Float32Array;
    layers: QwenLayerGPU[];
    kv_caches: KVCache[];
    config: QwenConfig;
}

export function loadQwenFromSafetensors(tensors: Record<string, Float32Array | Int8Array | Int32Array>, config: QwenConfig & { max_seq_len: number }): QwenModel {
    const layers: QwenLayerGPU[] = [];
    const kv_caches: KVCache[] = [];

    for (let i = 0; i < config.num_layers; i++) {
        // Cast everything to Float32Array for WebGPU shaders (which operate on f32 arrays)
        // Safetensors might hold I8 or I32 natively but we expect QwenLayerGPU to get Float32Arrays.
        // We know parser expanded BF16/F16 appropriately.
        layers.push(new QwenLayerGPU(tensors as Record<string, Float32Array>, i, config.apply_sparsity));
        kv_caches.push(new KVCache(config.max_seq_len, config.kv_dim, config.head_dim));
    }

    return {
        embed_tokens: tensors['model.embed_tokens.weight'] as Float32Array,
        lm_head: tensors['lm_head.weight'] as Float32Array,
        norm_weight: tensors['model.norm.weight'] as Float32Array,
        layers,
        kv_caches,
        config
    };
}

export async function forward_one(
    model: QwenModel,
    token: number | null,
    layer_start: number = 0,
    layer_end: number = model.config.num_layers - 1,
    incoming_state?: Float32Array
): Promise<Float32Array> {
    const conf = model.config;
    
    // Determine input state (either from token embedding or previous shard)
    let state: Float32Array;
    if (incoming_state) {
        state = new Float32Array(incoming_state); // Copy to avoid mutating network buffer
    } else if (token !== null) {
        state = model.embed_tokens.slice(token * conf.hidden_size, (token + 1) * conf.hidden_size);
    } else {
        throw new Error("Must provide either a token or an incoming hidden state");
    }

    const n_rep = Math.floor(conf.num_heads / conf.num_kv_heads);

    for (let li = layer_start; li <= layer_end; li++) {
        const layer = model.layers[li];
        const kv = model.kv_caches[li];
        const pos = kv.pos;

        let ln_buf = np_rms_norm(state, layer.input_layernorm);

        const M = 1, N = conf.hidden_size;
        
        const q = await gpu_ternary_matmul(ln_buf, layer.q_proj, M, N, conf.num_heads * conf.head_dim);
        const k = await gpu_ternary_matmul(ln_buf, layer.k_proj, M, N, conf.kv_dim);
        const v = await gpu_ternary_matmul(ln_buf, layer.v_proj, M, N, conf.kv_dim);

        apply_rope(q, k, pos, conf.num_heads, conf.num_kv_heads, conf.head_dim);

        // Store in KV cache
        kv.k.set(k, pos * conf.kv_dim);
        kv.v.set(v, pos * conf.kv_dim);

        const attn_out = new Float32Array(conf.num_heads * conf.head_dim);

        for (let h = 0; h < conf.num_heads; h++) {
            const kv_h = Math.floor(h / n_rep);
            const current_q = q.subarray(h * conf.head_dim, (h + 1) * conf.head_dim);
            
            // Reconstruct past_k matrix [pos+1, head_dim]
            const scores = new Float32Array(pos + 1);
            for (let t = 0; t <= pos; t++) {
                let dot = 0;
                for (let d = 0; d < conf.head_dim; d++) {
                    const kv_val = kv.k[t * conf.kv_dim + (kv_h * conf.head_dim + d)];
                    dot += kv_val * current_q[d];
                }
                scores[t] = dot / Math.sqrt(conf.head_dim);
            }

            const p = np_softmax(scores);

            const out_h = new Float32Array(conf.head_dim);
            for (let t = 0; t <= pos; t++) {
                const pt = p[t];
                for (let d = 0; d < conf.head_dim; d++) {
                    out_h[d] += pt * kv.v[t * conf.kv_dim + (kv_h * conf.head_dim + d)];
                }
            }

            attn_out.set(out_h, h * conf.head_dim);
        }

        kv.pos++;

        const proj_out = await gpu_ternary_matmul(attn_out, layer.o_proj, 1, conf.num_heads * conf.head_dim, conf.hidden_size);
        for (let i = 0; i < state.length; i++) state[i] += proj_out[i];

        const ln_buf_post = np_rms_norm(state, layer.post_attention_layernorm);

        const gate = await gpu_ternary_matmul(ln_buf_post, layer.gate_proj, 1, conf.hidden_size, conf.intermediate_size);
        const up = await gpu_ternary_matmul(ln_buf_post, layer.up_proj, 1, conf.hidden_size, conf.intermediate_size);

        // SiLU GPU dispatch
        const gate_silu = await gpu_silu(gate, conf.intermediate_size);
        const mlp_in = new Float32Array(conf.intermediate_size);
        for(let i=0; i < mlp_in.length; i++) mlp_in[i] = gate_silu[i] * up[i];

        const mlp_out = await gpu_ternary_matmul(mlp_in, layer.down_proj, 1, conf.intermediate_size, conf.hidden_size);
        for (let i = 0; i < state.length; i++) state[i] += mlp_out[i];
    }

    // If this shard does not compute the final layer, return the intermediate hidden state
    if (layer_end < model.config.num_layers - 1) {
        return state;
    }

    // Otherwise, project to logits
    const final_ln = np_rms_norm(state, model.norm_weight);
    const logits = await gpu_ternary_matmul(final_ln, model.lm_head, 1, conf.hidden_size, conf.vocab_size);
    return logits;
}
