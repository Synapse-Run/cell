/**
 * ffi_runtime.ts
 *
 * Provides the Native FFI (Foreign Function Interface) bridging between the Synapse WASM bytecode
 * and the V8 Javascript Engine (Node/Cloudflare).
 */
export function createFfiImports(state) {
    return {
        env: {
            print: (ptr, len) => {
                if (!state.memory)
                    return;
                const buffer = new Uint8Array(state.memory.buffer, ptr, len);
                const decoder = new TextDecoder('utf-8');
                state.stdout += decoder.decode(buffer);
            },
            // Core ML/Hardware constraints not available at Edge yet. (Returns generic failure)
            ffi_numpy_dot: () => -1n,
            gpu_matmul: () => -1n,
            gpu_relu: () => -1n,
            gpu_softmax: () => -1n,
            gpu_silu: () => -1n,
            gpu_layernorm: () => -1n,
            gpu_add: () => -1n,
            load_weights: (dst_ptr, weight_id, offset, size) => {
                const dst = Number(dst_ptr);
                const off = Number(offset);
                const sz = Number(size);
                const MAX_MODEL_SIZE = 89456640;
                if (off < 0 || sz <= 0 || off + sz > MAX_MODEL_SIZE)
                    return -2n; // OOB
                if (!state.memory)
                    return -3n; // No memory
                let fs;
                try {
                    // Try requiring Node.js fs (will fail in browser/Cloudflare, which is correct for Tier 2)
                    fs = require('fs');
                    const path = require('path');
                    const bin_path = path.resolve(__dirname, "../../models/synapse-qwen-0.5b/synapse_weights.bin");
                    const fd = fs.openSync(bin_path, 'r');
                    const wasmArray = new Uint8Array(state.memory.buffer, dst, sz);
                    // readSync(fd, buffer, offset, length, position)
                    fs.readSync(fd, wasmArray, 0, sz, off);
                    fs.closeSync(fd);
                    return 0n; // Success
                }
                catch (e) {
                    console.error("[FFI load_weights] Error:", e);
                    return -1n;
                }
            },
            weight_info: () => -1n,
            crypto_sign: () => { },
            crypto_verify: () => 0,
            host_debug: () => 0n,
            tcp_bind: () => 0n,
            tcp_accept: () => 0n,
            tcp_recv: () => 0n,
            tcp_send: () => 0n,
            p2p_receive_compute: () => -1n,
            p2p_receive: () => -1n,
            p2p_respond_compute: () => -1n,
            p2p_request_compute: () => -1n,
            tcp_connect: () => -1n,
        }
    };
}
