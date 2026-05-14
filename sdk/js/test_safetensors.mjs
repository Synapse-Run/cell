// sdk/js/safetensors.ts
function parseSafetensors(buffer) {
  const dataView = new DataView(buffer);
  const headerLenLow = dataView.getUint32(0, true);
  const headerLenHigh = dataView.getUint32(4, true);
  const headerLength = headerLenLow + headerLenHigh * 4294967296;
  const offset = 8;
  const headerSlice = new Uint8Array(buffer, offset, headerLength);
  const decoder = new TextDecoder("utf-8");
  const headerStr = decoder.decode(headerSlice);
  const headerObj = JSON.parse(headerStr);
  const tensors = {};
  const dataOffset = offset + headerLength;
  for (const [key, value] of Object.entries(headerObj)) {
    if (key === "__metadata__") continue;
    const def = value;
    const start = dataOffset + def.data_offsets[0];
    const end = dataOffset + def.data_offsets[1];
    const byteLength = end - start;
    let srcBuffer = buffer;
    let viewStart = start;
    let elementSize = 1;
    if (def.dtype === "F32" || def.dtype === "I32") elementSize = 4;
    if (def.dtype === "F16" || def.dtype === "BF16") elementSize = 2;
    if (viewStart % elementSize !== 0) {
      srcBuffer = buffer.slice(start, end);
      viewStart = 0;
    }
    switch (def.dtype) {
      case "F32":
        tensors[key] = new Float32Array(srcBuffer, viewStart, byteLength / 4);
        break;
      case "F16":
        const f16_view = new Uint16Array(srcBuffer, viewStart, byteLength / 2);
        const f32_cast = new Float32Array(byteLength / 2);
        for (let i = 0; i < f16_view.length; i++) {
          f32_cast[i] = _decodeFloat16(f16_view[i]);
        }
        tensors[key] = f32_cast;
        break;
      case "I8":
        tensors[key] = new Int8Array(srcBuffer, viewStart, byteLength);
        break;
      case "I32":
        tensors[key] = new Int32Array(srcBuffer, viewStart, byteLength / 4);
        break;
      case "BF16":
        const bf16_view = new Uint16Array(srcBuffer, viewStart, byteLength / 2);
        const bf16_cast = new Float32Array(byteLength / 2);
        const bf16_cast_view = new Uint32Array(bf16_cast.buffer);
        for (let i = 0; i < bf16_view.length; i++) {
          bf16_cast_view[i] = bf16_view[i] << 16;
        }
        tensors[key] = bf16_cast;
        break;
      default:
        console.warn(`[Safetensors] Unhandled dtype ${def.dtype} for ${key}`);
        continue;
    }
  }
  return tensors;
}
function _decodeFloat16(binary) {
  const exponent = (binary & 31744) >> 10;
  const fraction = binary & 1023;
  const sign = binary & 32768 ? -1 : 1;
  if (exponent === 0) {
    return sign * Math.pow(2, -14) * (fraction / 1024);
  } else if (exponent === 31) {
    return fraction ? NaN : sign * Infinity;
  }
  return sign * Math.pow(2, exponent - 15) * (1 + fraction / 1024);
}

// sdk/js/test_safetensors.ts
async function main() {
  console.log("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");
  console.log("  WebGPU Engine \u2014 Safetensors Javascript Verification");
  console.log("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n");
  console.log("[1/2] Creating mock .safetensors payload in memory...");
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
  const totalSize = 8 + headerLength + 32;
  const buffer = new ArrayBuffer(totalSize);
  const view = new DataView(buffer);
  view.setUint32(0, headerLength, true);
  view.setUint32(4, 0, true);
  const u8view = new Uint8Array(buffer);
  u8view.set(headerBytes, 8);
  const dataOffset = 8 + headerLength;
  view.setFloat32(dataOffset + 0, 1, true);
  view.setFloat32(dataOffset + 4, 2, true);
  view.setFloat32(dataOffset + 8, -3.5, true);
  view.setFloat32(dataOffset + 12, 4, true);
  view.setInt8(dataOffset + 16, -1);
  view.setInt8(dataOffset + 17, 0);
  view.setInt8(dataOffset + 18, 1);
  view.setInt8(dataOffset + 19, 2);
  view.setInt8(dataOffset + 20, 3);
  view.setInt8(dataOffset + 21, 4);
  view.setInt8(dataOffset + 22, 5);
  view.setInt8(dataOffset + 23, -128);
  view.setUint16(dataOffset + 24, 15360, true);
  view.setUint16(dataOffset + 26, 16384, true);
  view.setUint16(dataOffset + 28, 49152, true);
  view.setUint16(dataOffset + 30, 0, true);
  console.log("[2/2] Parsing Safetensors payload...");
  const parsed = parseSafetensors(buffer);
  let passes = 0;
  if (parsed["tensor_f32"].length === 4 && parsed["tensor_f32"][2] === -3.5) {
    console.log("  \u2705 PASS \u2014 F32 Array Parsing");
    passes++;
  } else {
    console.log("  \u274C FAIL \u2014 F32 Array Parsing");
  }
  if (parsed["tensor_i8"].length === 8 && parsed["tensor_i8"][7] === -128) {
    console.log("  \u2705 PASS \u2014 I8 Array Parsing");
    passes++;
  } else {
    console.log("  \u274C FAIL \u2014 I8 Array Parsing");
  }
  if (parsed["tensor_f16"].length === 4 && parsed["tensor_f16"][0] === 1 && parsed["tensor_f16"][2] === -2) {
    console.log("  \u2705 PASS \u2014 F16 to F32 Cast Native Parsing");
    passes++;
  } else {
    console.log("  \u274C FAIL \u2014 F16 to F32 Cast Native Parsing", parsed["tensor_f16"]);
  }
  console.log("\n\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");
  if (passes === 3) {
    console.log("  \u2705 All safetensors parity tests complete.");
  } else {
    console.log("  \u274C Some checks failed! Execution halted.");
    process.exit(1);
  }
  console.log("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");
}
main().catch((e) => console.error(e));
