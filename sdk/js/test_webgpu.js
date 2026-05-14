/**
 * WebGPU Javascript Verification Suite
 *
 * Tests the JS WGSL dispatch bindings for Ternary MatMul and SiLU against standard
 * JS implementations to ensure precision and WebGPU buffer alignment.
 */
import { initWebGPU, gpu_ternary_matmul, gpu_silu } from './webgpu_engine.js';
async function main() {
    console.log("═══════════════════════════════════════════════════");
    console.log("  WebGPU Engine — Javascript Verification Suite");
    console.log("═══════════════════════════════════════════════════\n");
    const initialized = await initWebGPU();
    if (!initialized) {
        console.error("❌ WebGPU failed to initialize. Cannot run tests.");
        console.error("   Ensure this is running in an environment with WebGPU support.");
        console.error("   For Node.js, try: node --experimental-webgpu");
        process.exit(1);
    }
    console.log("\n[1/2] Testing SiLU activation shader...");
    // Create random input array (size 10)
    const length = 10;
    const x = new Float32Array(length);
    for (let i = 0; i < length; i++) {
        x[i] = (Math.random() * 4) - 2; // [-2.0, 2.0]
    }
    const t0 = performance.now();
    const gpu_res_silu = await gpu_silu(x, length);
    const gpu_time_silu = performance.now() - t0;
    // JS reference implementation
    const js_res_silu = new Float32Array(length);
    for (let i = 0; i < length; i++) {
        let val = x[i];
        js_res_silu[i] = val / (1.0 + Math.exp(-val));
    }
    let max_err_silu = 0;
    for (let i = 0; i < length; i++) {
        const err = Math.abs(gpu_res_silu[i] - js_res_silu[i]);
        if (err > max_err_silu)
            max_err_silu = err;
    }
    if (max_err_silu < 1e-5) {
        console.log(`  ✅ PASS — SiLU (max error: ${max_err_silu.toExponential(3)}, time: ${gpu_time_silu.toFixed(2)}ms)`);
    }
    else {
        console.log(`  ❌ FAIL — SiLU (max error: ${max_err_silu.toExponential(3)})`);
    }
    console.log("\n[2/2] Testing Ternary MatMul shader...");
    const M = 64, N = 32, K = 16;
    // X
    const X = new Float32Array(M * N);
    for (let i = 0; i < M * N; i++)
        X[i] = Math.random();
    // Ternary Weight Matrix {-1, 0, 1}
    const W = new Float32Array(N * K);
    for (let i = 0; i < N * K; i++) {
        const rand = Math.random();
        if (rand < 0.3)
            W[i] = -1.0;
        else if (rand > 0.7)
            W[i] = 1.0;
        else
            W[i] = 0.0;
    }
    const t1 = performance.now();
    const gpu_res_matmul = await gpu_ternary_matmul(X, W, M, N, K);
    const gpu_time_matmul = performance.now() - t1;
    // JS Reference (Zero-multiply ternary implementation)
    const js_res_matmul = new Float32Array(M * K);
    for (let i = 0; i < N; i++) {
        for (let j = 0; j < K; j++) {
            const w_val = W[i * K + j];
            if (w_val > 0.5) {
                // Add to column j, across all rows M
                for (let r = 0; r < M; r++) {
                    js_res_matmul[r * K + j] += X[r * N + i];
                }
            }
            else if (w_val < -0.5) {
                // Subtract from column j, across all rows M
                for (let r = 0; r < M; r++) {
                    js_res_matmul[r * K + j] -= X[r * N + i];
                }
            }
        }
    }
    let max_err_matmul = 0;
    for (let i = 0; i < M * K; i++) {
        const err = Math.abs(gpu_res_matmul[i] - js_res_matmul[i]);
        if (err > max_err_matmul)
            max_err_matmul = err;
    }
    if (max_err_matmul < 1e-4) {
        console.log(`  ✅ PASS — Ternary MatMul (max error: ${max_err_matmul.toExponential(3)}, time: ${gpu_time_matmul.toFixed(2)}ms)`);
    }
    else {
        console.log(`  ❌ FAIL — Ternary MatMul (max error: ${max_err_matmul.toExponential(3)})`);
    }
    console.log("\n═══════════════════════════════════════════════════");
    console.log("  All tests complete.");
    console.log("═══════════════════════════════════════════════════");
}
main().catch(e => console.error(e));
