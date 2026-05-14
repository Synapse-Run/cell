/**
 * safetensors verification harness
 */

import { parseSafetensors } from './safetensors.js';

async function main() {
    console.log("═══════════════════════════════════════════════════");
    console.log("  WebGPU Engine — Safetensors Javascript Verification");
    console.log("═══════════════════════════════════════════════════\n");

    console.log("[1/2] Creating mock .safetensors payload in memory...");
    
    // We want three tensors:
    // "tensor_f32": 4 elements F32
    // "tensor_i8": 8 elements I8
    // "tensor_f16": 4 elements F16

    const headerObj = {
        "__metadata__": { "format": "pt" },
        "tensor_f32": { "dtype": "F32", "shape": [4], "data_offsets": [0, 16] },
        "tensor_i8": { "dtype": "I8", "shape": [8], "data_offsets": [16, 24] },
        "tensor_f16": { "dtype": "F16", "shape": [4], "data_offsets": [24, 32] }
    };

    const headerStr = JSON.stringify(headerObj);
    const encoder = new TextEncoder();
    const headerBytes = encoder.encode(headerStr);
    const headerLength = headerBytes.byteLength;

    // Total size: 8 bytes (len) + headerLength + 32 bytes data
    const totalSize = 8 + headerLength + 32;
    const buffer = new ArrayBuffer(totalSize);
    const view = new DataView(buffer);

    // 1. Write header length (little endian uint64)
    view.setUint32(0, headerLength, true);
    view.setUint32(4, 0, true);

    // 2. Write header string
    const u8view = new Uint8Array(buffer);
    u8view.set(headerBytes, 8);

    // 3. Write data
    const dataOffset = 8 + headerLength;
    
    // F32 Data (1.0, 2.0, -3.5, 4.0)
    view.setFloat32(dataOffset + 0, 1.0, true);
    view.setFloat32(dataOffset + 4, 2.0, true);
    view.setFloat32(dataOffset + 8, -3.5, true);
    view.setFloat32(dataOffset + 12, 4.0, true);

    // I8 Data (-1, 0, 1, 2, 3, 4, 5, -128)
    view.setInt8(dataOffset + 16, -1);
    view.setInt8(dataOffset + 17, 0);
    view.setInt8(dataOffset + 18, 1);
    view.setInt8(dataOffset + 19, 2);
    view.setInt8(dataOffset + 20, 3);
    view.setInt8(dataOffset + 21, 4);
    view.setInt8(dataOffset + 22, 5);
    view.setInt8(dataOffset + 23, -128);

    // F16 Data (We'll just write mock hex bits for generic 1.0, 2.0, etc, or test the cast)
    // 0x3C00 is 1.0 in float16
    // 0x4000 is 2.0 in float16
    // 0xC000 is -2.0 in float16
    view.setUint16(dataOffset + 24, 0x3C00, true);
    view.setUint16(dataOffset + 26, 0x4000, true);
    view.setUint16(dataOffset + 28, 0xC000, true);
    view.setUint16(dataOffset + 30, 0x0000, true); // 0.0

    console.log("[2/2] Parsing Safetensors payload...");
    const parsed = parseSafetensors(buffer);

    let passes = 0;

    // Assert F32
    if (parsed["tensor_f32"].length === 4 && parsed["tensor_f32"][2] === -3.5) {
        console.log("  ✅ PASS — F32 Array Parsing"); passes++;
    } else {
        console.log("  ❌ FAIL — F32 Array Parsing");
    }

    // Assert I8
    if (parsed["tensor_i8"].length === 8 && parsed["tensor_i8"][7] === -128) {
        console.log("  ✅ PASS — I8 Array Parsing"); passes++;
    } else {
        console.log("  ❌ FAIL — I8 Array Parsing");
    }

    // Assert F16 -> F32 cast
    if (parsed["tensor_f16"].length === 4 && parsed["tensor_f16"][0] === 1.0 && parsed["tensor_f16"][2] === -2.0) {
        console.log("  ✅ PASS — F16 to F32 Cast Native Parsing"); passes++;
    } else {
        console.log("  ❌ FAIL — F16 to F32 Cast Native Parsing", parsed["tensor_f16"]);
    }

    console.log("\n═══════════════════════════════════════════════════");
    if (passes === 3) {
        console.log("  ✅ All safetensors parity tests complete.");
    } else {
        console.log("  ❌ Some checks failed! Execution halted.");
        process.exit(1);
    }
    console.log("═══════════════════════════════════════════════════");
}

main().catch(e => console.error(e));
