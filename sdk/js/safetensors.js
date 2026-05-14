/**
 * safetensors.ts
 *
 * Native Javascript parser for huggingface `.safetensors` files.
 * Streams into binary ArrayBuffers for zero-copy Float32Array/Int8Array slicing,
 * exactly what the WebGPU subsystem needs.
 */
export async function fetchSafetensors(url, progressCallback) {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Failed to fetch safetensors: ${response.status} ${response.statusText}`);
    }
    const contentLength = response.headers.get('content-length');
    let totalBytes = contentLength ? parseInt(contentLength, 10) : 0;
    // Fallback: simpler fetching if length unknown or streaming not requested
    if (!response.body || totalBytes === 0 || !progressCallback) {
        const arrayBuffer = await response.arrayBuffer();
        if (progressCallback)
            progressCallback(100);
        return parseSafetensors(arrayBuffer);
    }
    const reader = response.body.getReader();
    const chunks = [];
    let receivedBytes = 0;
    while (true) {
        const { done, value } = await reader.read();
        if (done)
            break;
        chunks.push(value);
        receivedBytes += value.byteLength;
        progressCallback(Math.floor((receivedBytes / totalBytes) * 100));
    }
    // Concatenate into single ArrayBuffer
    const combined = new Uint8Array(receivedBytes);
    let offset = 0;
    for (const chunk of chunks) {
        combined.set(chunk, offset);
        offset += chunk.byteLength;
    }
    return parseSafetensors(combined.buffer);
}
export function parseSafetensors(buffer) {
    const dataView = new DataView(buffer);
    // Safetensors 8-byte JSON length header (little-endian unsigned 64-bit int)
    const headerLenLow = dataView.getUint32(0, true);
    const headerLenHigh = dataView.getUint32(4, true);
    // Since bitwise operators in JS are 32-bit, we multiply by 2^32 if necessary,
    // but headers rarely exceed 4GB so taking the low 32 bits is usually fine.
    const headerLength = headerLenLow + headerLenHigh * 4294967296;
    const offset = 8;
    const headerSlice = new Uint8Array(buffer, offset, headerLength);
    const decoder = new TextDecoder('utf-8');
    const headerStr = decoder.decode(headerSlice);
    const headerObj = JSON.parse(headerStr);
    const tensors = {};
    const dataOffset = offset + headerLength;
    for (const [key, value] of Object.entries(headerObj)) {
        if (key === "__metadata__")
            continue;
        const def = value;
        const start = dataOffset + def.data_offsets[0];
        const end = dataOffset + def.data_offsets[1];
        const byteLength = end - start;
        // Handle byte alignment requirements for TypedArrays
        let srcBuffer = buffer;
        let viewStart = start;
        let elementSize = 1;
        if (def.dtype === "F32" || def.dtype === "I32")
            elementSize = 4;
        if (def.dtype === "F16" || def.dtype === "BF16")
            elementSize = 2;
        if (viewStart % elementSize !== 0) {
            srcBuffer = buffer.slice(start, end);
            viewStart = 0;
        }
        // Extract typed view without copying the underlying buffer! (if aligned)
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
// Float16 -> Float32 bit-bashing implementation
function _decodeFloat16(binary) {
    const exponent = (binary & 0x7C00) >> 10;
    const fraction = binary & 0x03FF;
    const sign = (binary & 0x8000) ? -1 : 1;
    if (exponent === 0) {
        return sign * Math.pow(2, -14) * (fraction / 1024);
    }
    else if (exponent === 0x1F) {
        return fraction ? NaN : sign * Infinity;
    }
    return sign * Math.pow(2, exponent - 15) * (1 + fraction / 1024);
}
