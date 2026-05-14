/* tslint:disable */
/* eslint-disable */

export class SovereignEngine {
    free(): void;
    [Symbol.dispose](): void;
    get_last_receipt(): string;
    get_overlap(a: Uint8Array, b: Uint8Array): number;
    constructor(seed: bigint);
    step(current_input: Uint8Array, target_input: Uint8Array): Uint8Array;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_sovereignengine_free: (a: number, b: number) => void;
    readonly sovereignengine_get_last_receipt: (a: number) => [number, number];
    readonly sovereignengine_get_overlap: (a: number, b: number, c: number, d: number, e: number) => number;
    readonly sovereignengine_new: (a: bigint) => number;
    readonly sovereignengine_step: (a: number, b: number, c: number, d: number, e: number) => any;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
