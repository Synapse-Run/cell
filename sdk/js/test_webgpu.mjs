// sdk/js/webgpu_engine.js
var gpuDevice = null;
var pipelineCache = {};
var SHADERS = {
  "ternary_matmul.wgsl": `
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
  "silu.wgsl": `
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
async function initWebGPU() {
  if (typeof navigator === "undefined" || !navigator.gpu) {
    console.warn("[WebGPUEngine] WebGPU not supported on this platform. (Try Chrome/Edge > 113 or enable flags)");
    return false;
  }
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: "high-performance" });
  if (!adapter) {
    console.warn("[WebGPUEngine] RequestAdapter failed.");
    return false;
  }
  gpuDevice = await adapter.requestDevice();
  console.log(`[WebGPUEngine] GPU Initialized: ${adapter.requestAdapterInfo ? (await adapter.requestAdapterInfo()).architecture : "Unknown GPU"}`);
  return true;
}
function getDevice() {
  if (!gpuDevice) {
    throw new Error("WebGPU device not initialized. Call initWebGPU() first.");
  }
  return gpuDevice;
}
async function dispatchCompute(shaderName, dims, inputArrays, outputSize, workgroups = [1, 1, 1]) {
  const device = getDevice();
  const shaderCode = SHADERS[shaderName];
  if (!pipelineCache[shaderName]) {
    const shaderModule = device.createShaderModule({ code: shaderCode });
    pipelineCache[shaderName] = await device.createComputePipelineAsync({
      layout: "auto",
      compute: { module: shaderModule, entryPoint: "main" }
    });
  }
  const pipeline = pipelineCache[shaderName];
  const dimsArray = new Uint32Array(dims);
  const dimsBuf = device.createBuffer({
    size: dimsArray.byteLength,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST
  });
  device.queue.writeBuffer(dimsBuf, 0, dimsArray);
  const inputBufs = [];
  inputArrays.forEach((arr, i) => {
    const buf = device.createBuffer({
      size: arr.byteLength,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST
    });
    device.queue.writeBuffer(buf, 0, arr);
    inputBufs.push(buf);
  });
  const outputSizeBytes = outputSize * 4;
  const outputBuf = device.createBuffer({
    size: outputSizeBytes,
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC
  });
  const readbackBuf = device.createBuffer({
    size: outputSizeBytes,
    usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ
  });
  const bindGroupEntries = [
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
  dimsBuf.destroy();
  inputBufs.forEach((buf) => buf.destroy());
  outputBuf.destroy();
  readbackBuf.destroy();
  return result;
}
async function gpu_ternary_matmul(x, w, m, n, k) {
  const TILE = 16;
  const wg_x = Math.ceil(k / TILE);
  const wg_y = Math.ceil(m / TILE);
  return dispatchCompute("ternary_matmul.wgsl", [m, n, k, 0], [x, w], m * k, [wg_x, wg_y, 1]);
}
async function gpu_silu(x, length) {
  const wg_x = Math.ceil(length / 256);
  return dispatchCompute("silu.wgsl", [length, 0, 0, 0], [x], length, [wg_x, 1, 1]);
}

// sdk/js/test_webgpu.ts
async function main() {
  console.log("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");
  console.log("  WebGPU Engine \u2014 Javascript Verification Suite");
  console.log("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n");
  const initialized = await initWebGPU();
  if (!initialized) {
    console.error("\u274C WebGPU failed to initialize. Cannot run tests.");
    console.error("   Ensure this is running in an environment with WebGPU support.");
    console.error("   For Node.js, try: node --experimental-webgpu");
    process.exit(1);
  }
  console.log("\n[1/2] Testing SiLU activation shader...");
  const length = 10;
  const x = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    x[i] = Math.random() * 4 - 2;
  }
  const t0 = performance.now();
  const gpu_res_silu = await gpu_silu(x, length);
  const gpu_time_silu = performance.now() - t0;
  const js_res_silu = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    let val = x[i];
    js_res_silu[i] = val / (1 + Math.exp(-val));
  }
  let max_err_silu = 0;
  for (let i = 0; i < length; i++) {
    const err = Math.abs(gpu_res_silu[i] - js_res_silu[i]);
    if (err > max_err_silu) max_err_silu = err;
  }
  if (max_err_silu < 1e-5) {
    console.log(`  \u2705 PASS \u2014 SiLU (max error: ${max_err_silu.toExponential(3)}, time: ${gpu_time_silu.toFixed(2)}ms)`);
  } else {
    console.log(`  \u274C FAIL \u2014 SiLU (max error: ${max_err_silu.toExponential(3)})`);
  }
  console.log("\n[2/2] Testing Ternary MatMul shader...");
  const M = 64, N = 32, K = 16;
  const X = new Float32Array(M * N);
  for (let i = 0; i < M * N; i++) X[i] = Math.random();
  const W = new Float32Array(N * K);
  for (let i = 0; i < N * K; i++) {
    const rand = Math.random();
    if (rand < 0.3) W[i] = -1;
    else if (rand > 0.7) W[i] = 1;
    else W[i] = 0;
  }
  const t1 = performance.now();
  const gpu_res_matmul = await gpu_ternary_matmul(X, W, M, N, K);
  const gpu_time_matmul = performance.now() - t1;
  const js_res_matmul = new Float32Array(M * K);
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < K; j++) {
      const w_val = W[i * K + j];
      if (w_val > 0.5) {
        for (let r = 0; r < M; r++) {
          js_res_matmul[r * K + j] += X[r * N + i];
        }
      } else if (w_val < -0.5) {
        for (let r = 0; r < M; r++) {
          js_res_matmul[r * K + j] -= X[r * N + i];
        }
      }
    }
  }
  let max_err_matmul = 0;
  for (let i = 0; i < M * K; i++) {
    const err = Math.abs(gpu_res_matmul[i] - js_res_matmul[i]);
    if (err > max_err_matmul) max_err_matmul = err;
  }
  if (max_err_matmul < 1e-4) {
    console.log(`  \u2705 PASS \u2014 Ternary MatMul (max error: ${max_err_matmul.toExponential(3)}, time: ${gpu_time_matmul.toFixed(2)}ms)`);
  } else {
    console.log(`  \u274C FAIL \u2014 Ternary MatMul (max error: ${max_err_matmul.toExponential(3)})`);
  }
  console.log("\n\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");
  console.log("  All tests complete.");
  console.log("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");
}
main().catch((e) => console.error(e));
