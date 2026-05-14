// Structural lint exceptions — these are intentional architectural decisions:
// - too_many_arguments: run_worker() needs all its params for thread-per-core
// - wrong_self_convention: FFI naming conventions differ from Rust idioms
// - dead_code: feature-gated code paths (PyO3 vs binary)
// Style lints suppressed for existing codebase (non-correctness):
#![allow(
    clippy::too_many_arguments,
    clippy::wrong_self_convention,
    clippy::needless_range_loop,
    clippy::redundant_pattern_matching,
    clippy::match_like_matches_macro,
    clippy::nonminimal_bool,
    clippy::vec_init_then_push,
    clippy::suspicious_open_options,
    clippy::empty_line_after_doc_comments,
    dead_code
)]
//! synapse-gateway — Thin Rust host for the self-hosting .syn gateway
//!
//! Loads gateway.wasm (compiled from gateway.syn) and provides the FFI surface:
//!   tcp_bind, tcp_accept, tcp_send_raw, tcp_recv_raw, spawn_wasm,
//!   print, time_now, sha256_hash, env_get, kv_get, kv_set
//!
//! Thread-per-core: N worker threads sharing the same TcpListener via try_clone().
//! Each thread has its own wasmtime Store + Instance + trap recovery loop.
//!
//! Module Cache: Compiled wasmtime Modules are cached by SHA-256 of source code.
//! Repeat requests skip Cranelift JIT compilation entirely.

mod cell;
mod cell_api;
mod compiler;
mod inference;
pub mod license;
mod transpiler;
mod util;
mod ws_api;

use util::resolve_synapse_root;

use moka::sync::Cache;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{IpAddr, TcpListener, TcpStream};
use std::sync::Arc;
use std::time::Instant;
use wasmtime::*;

// Z3 PURGE 2026-04-15: removed `use z3::ast::Ast;` and `enum Z3ExprResult`.
// The Z3 SMT solver belongs to the research swarm, not the commercial Cell
// product. FFI signatures for `z3_verify`, `z3_verify_expr`, and
// `z3_verify_property` are preserved (returning "unknown") so guest Wasm
// code that calls them does not trap. To re-enable real Z3 verification,
// restore the dependency in Cargo.toml and the imports/enum/FFI bodies here.

/// Thread-safe compiled module cache.
/// Key: hex-encoded SHA-256 of source code. Value: compiled wasmtime Module.
type ModuleCache = Cache<String, Module>;

/// Per-instance state shared with the Wasm guest via FFI.
struct GatewayState {
    /// Active TCP listeners keyed by port
    listeners: Vec<TcpListener>,
    /// Active TCP connections
    connections: Vec<Option<TcpStream>>,
    /// Captured stdout from print FFI
    stdout_buf: Vec<u8>,
    /// Monotonic clock baseline for time_now
    epoch: Instant,
    /// Shared child wasmtime engine (for compile_and_exec / spawn_wasm)
    child_engine: Arc<Engine>,
    /// Shared compiled-module cache
    module_cache: ModuleCache,
    /// API key loaded from environment
    api_key: Vec<u8>,
    /// KV store (rusqlite)
    db_path: String,
    /// Currently active connection index (for trap recovery)
    active_conn: Option<usize>,
    /// Rate limiter: IP → (count, window_start)
    rate_limits: HashMap<IpAddr, (u64, Instant)>,
    /// Max requests per second per IP (default 20)
    rate_limit_max: u64,
    /// Client IPs for each connection (indexed by connection ID)
    conn_ips: Vec<Option<IpAddr>>,
    /// Outbound P2P Node connections for The Atlantic Handshake Phase 2
    p2p_streams: Vec<Option<std::net::TcpStream>>,
}

struct ChildState {
    stdout_buf: Vec<u8>,
}

// resolve_synapse_root() moved to src/util.rs on 2026-04-15 (see JC-002).
// Both main.rs and lib.rs include `mod util;` so the function is visible
// to cell.rs via `crate::util::resolve_synapse_root()` regardless of
// whether the crate is built as a binary or as the PyO3 library.

fn clear_gateway_payload(caller: &mut Caller<'_, GatewayState>) {
    let Some(mem) = caller.get_export("memory").and_then(|e| e.into_memory()) else {
        return;
    };
    let data = mem.data_mut(caller);
    if data.len() >= 72 {
        data[56..64].copy_from_slice(&0i64.to_le_bytes());
        data[64..72].copy_from_slice(&0i64.to_le_bytes());
    }
}

fn write_gateway_payload(caller: &mut Caller<'_, GatewayState>, payload: &[u8]) {
    let Some(mem) = caller.get_export("memory").and_then(|e| e.into_memory()) else {
        return;
    };
    let pos: usize = 65536;
    let data = mem.data_mut(caller);
    if pos + payload.len() + 1 >= data.len() || data.len() < 72 {
        return;
    }
    data[pos..pos + payload.len()].copy_from_slice(payload);
    data[pos + payload.len()] = 0;
    data[56..64].copy_from_slice(&(pos as i64).to_le_bytes());
    data[64..72].copy_from_slice(&((payload.len()) as i64).to_le_bytes());
}

/// Extract Content-Length from raw HTTP headers.
/// Returns 0 if not found or unparseable.
fn extract_content_length(headers: &[u8]) -> usize {
    // Case-insensitive search for "content-length:"
    let hdr_str = String::from_utf8_lossy(headers);
    for line in hdr_str.split("\r\n") {
        let lower = line.to_ascii_lowercase();
        if lower.starts_with("content-length:") {
            if let Some(val) = line.split(':').nth(1) {
                return val.trim().parse().unwrap_or(0);
            }
        }
    }
    0
}

/// Register all FFI host functions on a Linker.
/// Called once per thread to build a thread-local linker.
fn register_ffi(linker: &mut Linker<GatewayState>) -> Result<(), Box<dyn std::error::Error>> {
    // --- print(ptr: i32, len: i32) -> () ---
    linker.func_wrap(
        "env",
        "print",
        |mut caller: Caller<'_, GatewayState>, ptr: i32, len: i32| {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let s = ptr as usize;
            let e = s + len as usize;
            if e <= data.len() {
                let chunk = data[s..e].to_vec();
                caller.data_mut().stdout_buf.extend_from_slice(&chunk);
            }
        },
    )?;

    // --- Standard FFI stubs (sync.py always injects these) ---
    // --- tcp_connect(addr_ptr, len) -> stream_id ---
    linker.func_wrap(
        "env",
        "tcp_connect",
        |mut caller: Caller<'_, GatewayState>, addr_ptr: i64, len: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = addr_ptr as usize;
            let end = start + len as usize;
            if end > data.len() {
                return -1;
            }

            let addr_str = String::from_utf8_lossy(&data[start..end]).to_string();
            match std::net::TcpStream::connect(addr_str) {
                Ok(stream) => {
                    let _ = stream.set_nonblocking(true); // Default to non-blocking for polling
                    let streams = &mut caller.data_mut().p2p_streams;
                    if let Some(pos) = streams.iter().position(|x| x.is_none()) {
                        streams[pos] = Some(stream);
                        pos as i64
                    } else {
                        let id = streams.len();
                        streams.push(Some(stream));
                        id as i64
                    }
                }
                Err(_) => -1,
            }
        },
    )?;

    // --- p2p_receive(stream_id, out_ptr, max_len) -> bytes_read ---
    linker.func_wrap(
        "env",
        "p2p_receive",
        |mut caller: Caller<'_, GatewayState>, stream_id: i64, out_ptr: i64, max_len: i64| -> i64 {
            if stream_id < 0 || stream_id as usize >= caller.data().p2p_streams.len() {
                return -1;
            }

            // We temporarily extract the socket to avoid mutability borrowing issues against memory
            let mut stream = caller.data_mut().p2p_streams[stream_id as usize].take();

            if let Some(ref mut tcp) = stream {
                let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
                let start = out_ptr as usize;
                let end = start + max_len as usize;
                let data = mem.data_mut(&mut caller);
                if end > data.len() {
                    caller.data_mut().p2p_streams[stream_id as usize] = stream; // put it back
                    return -1;
                }

                use std::io::Read;
                // Stream is non-blocking
                let res = match tcp.read(&mut data[start..end]) {
                    Ok(n) => n as i64,
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => 0,
                    Err(_) => -1, // Connection dropped
                };

                if res != -1 {
                    caller.data_mut().p2p_streams[stream_id as usize] = stream; // put it back
                }
                res
            } else {
                -1
            }
        },
    )?;

    // --- p2p_broadcast(channel_id, buf_ptr, buf_len) -> 0 ---
    linker.func_wrap(
        "env",
        "p2p_broadcast",
        |mut caller: Caller<'_, GatewayState>,
         _channel_id: i64,
         buf_ptr: i64,
         buf_len: i64|
         -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = buf_ptr as usize;
            let end = start + buf_len as usize;
            if end > data.len() {
                return -1;
            }

            let buf = data[start..end].to_vec();
            let mut failed_streams = Vec::new();

            use std::io::Write;
            for (i, stream_opt) in caller.data_mut().p2p_streams.iter_mut().enumerate() {
                if let Some(stream) = stream_opt {
                    if stream.write_all(&buf).is_err() {
                        failed_streams.push(i);
                    }
                }
            }

            for i in failed_streams {
                caller.data_mut().p2p_streams[i] = None;
            }
            0
        },
    )?;

    linker.func_wrap(
        "env",
        "p2p_discover",
        |_: Caller<'_, GatewayState>, _a: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "p2p_receive_compute",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "p2p_respond_compute",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64, _c: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "p2p_request_compute",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64, _c: i64| -> i64 { -1 },
    )?;
    // sync.py currently emits a superset of standard FFI imports for compiled modules.
    // The current product slice does not rely on these GPU/model-loading calls, so we
    // define inert stubs to keep gateway startup aligned with the bounded execution path.
    linker.func_wrap(
        "env",
        "gpu_matmul",
        |_: Caller<'_, GatewayState>,
         _a: i64,
         _b: i64,
         _c: i64,
         _d: i64,
         _e: i64,
         _f: i64|
         -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "gpu_relu",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "gpu_softmax",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64, _c: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "gpu_silu",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "gpu_layernorm",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64, _c: i64, _d: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "gpu_add",
        |_: Caller<'_, GatewayState>, _a: i64, _b: i64, _c: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "load_weights",
        |mut caller: Caller<'_, GatewayState>,
         dst_ptr: i64,
         _weight_id: i64,
         offset: i64,
         size_in_bytes: i64|
         -> i64 {
            const MAX_MODEL_SIZE: u64 = 89_456_640; // Exact size of synapse_weights.bin
                                                    // Boundary guard: prevent OOB reads past the model binary
            if offset < 0
                || size_in_bytes <= 0
                || (offset as u64) + (size_in_bytes as u64) > MAX_MODEL_SIZE
            {
                return -2; // OOB sentinel
            }
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let mut p = std::env::current_dir().unwrap();
            while !p.join("models").exists() && p.parent().is_some() {
                p = p.parent().unwrap().to_path_buf();
            }
            let bin_path = p
                .join("models")
                .join("synapse-qwen-0.5b")
                .join("synapse_weights.bin");
            if let Ok(mut file) = std::fs::File::open(&bin_path) {
                use std::io::{Read, Seek};
                if file.seek(std::io::SeekFrom::Start(offset as u64)).is_ok() {
                    let mut buf = vec![0u8; size_in_bytes as usize];
                    if file.read_exact(&mut buf).is_ok() {
                        let data = mem.data_mut(&mut caller);
                        let dp = dst_ptr as usize;
                        if dp + buf.len() <= data.len() {
                            data[dp..dp + buf.len()].copy_from_slice(&buf);
                            return 0; // Success
                        }
                    }
                }
            }
            -1 // File I/O error
        },
    )?;
    linker.func_wrap(
        "env",
        "weight_info",
        |_: Caller<'_, GatewayState>, _a: i64| -> i64 { -1 },
    )?;
    linker.func_wrap(
        "env",
        "crypto_sign",
        |_: Caller<'_, GatewayState>, _a: i32, _b: i32, _c: i32, _d: i32| {},
    )?;
    linker.func_wrap(
        "env",
        "crypto_verify",
        |_: Caller<'_, GatewayState>, _a: i32, _b: i32, _c: i32, _d: i32| -> i32 { 0 },
    )?;

    // --- ffi_numpy_dot(a_ptr, a_len, b_ptr, b_len, out_ptr) -> status ---
    linker.func_wrap(
        "env",
        "ffi_numpy_dot",
        |mut caller: Caller<'_, GatewayState>,
         a_ptr: i64,
         a_len: i64,
         b_ptr: i64,
         b_len: i64,
         out_ptr: i64|
         -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);

            let ap = a_ptr as usize;
            let al = a_len as usize;
            let bp = b_ptr as usize;
            let bl = b_len as usize;

            // Assume f64 arrays, length in elements is len / 8.
            let a_elems = al / 8;
            let b_elems = bl / 8;

            if ap + al > data.len() || bp + bl > data.len() {
                return -1;
            }

            // Extract f64 slices
            let mut a_vec = Vec::with_capacity(a_elems);
            for i in 0..a_elems {
                let start = ap + i * 8;
                let val = f64::from_le_bytes(data[start..start + 8].try_into().unwrap());
                a_vec.push(val);
            }
            let mut b_vec = Vec::with_capacity(b_elems);
            for i in 0..b_elems {
                let start = bp + i * 8;
                let val = f64::from_le_bytes(data[start..start + 8].try_into().unwrap());
                b_vec.push(val);
            }

            // Inner product loop implementation
            let mut result: f64 = 0.0;
            let min_elems = std::cmp::min(a_elems, b_elems);
            for i in 0..min_elems {
                result += a_vec[i] * b_vec[i];
            }

            // Write result
            let op = out_ptr as usize;
            let data_mut = mem.data_mut(&mut caller);
            if op + 8 > data_mut.len() {
                return -1;
            }
            data_mut[op..op + 8].copy_from_slice(&result.to_le_bytes());

            0
        },
    )?;

    // --- host_debug(tag, addr) -> 0 ---
    linker.func_wrap("env", "host_debug",
        |mut caller: Caller<'_, GatewayState>, tag: i64, addr: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let r = |a: usize| -> i64 {
                if a + 8 <= data.len() {
                    i64::from_le_bytes(data[a..a+8].try_into().unwrap())
                } else { -1 }
            };
            let (arena, emit_ptr, emit_pos, scan, scratch, ftab, fcount) =
                (r(0), r(8), r(16), r(24), r(32), r(40), r(48));
            let a = addr as usize;
            if tag == 10 {
                if a + 16 <= data.len() {
                    let bytes: Vec<u8> = data[a..a+16].to_vec();
                    let ascii: String = bytes.iter().map(|&b| if (32..127).contains(&b) { b as char } else { '.' }).collect();
                    let hex: String = bytes.iter().map(|b| format!("{:02x}", b)).collect();
                    eprintln!("[DBG tag=10] ident@{}: ascii=\"{}\" hex={}", addr, ascii, hex);
                }
            } else {
                eprintln!("[DBG tag={}] arena={} emit_ptr={} emit_pos={} scan={} scratch={} ftab={} fcount={}",
                    tag, arena, emit_ptr, emit_pos, scan, scratch, ftab, fcount);
                if a + 32 <= data.len() {
                    eprintln!("[DBG tag={}] @{}: [{}, {}, {}, {}]", tag, addr, r(a), r(a+8), r(a+16), r(a+24));
                }
            }
            0
        }
    )?;

    // --- tcp_bind(port) -> listener_id ---
    // With thread-per-core, the listener is pre-injected into GatewayState.
    // tcp_bind just returns 0 (reusing the existing listener).
    linker.func_wrap(
        "env",
        "tcp_bind",
        |mut caller: Caller<'_, GatewayState>, port: i64| -> i64 {
            let addr = format!("0.0.0.0:{}", port);
            // Listener is pre-injected by thread-per-core; reuse it
            if !caller.data().listeners.is_empty() {
                eprintln!("[synapse-gateway] Reusing existing listener on {}", addr);
                return 0;
            }
            match TcpListener::bind(&addr) {
                Ok(listener) => {
                    eprintln!("[synapse-gateway] Listening on {}", addr);
                    let id = caller.data().listeners.len();
                    caller.data_mut().listeners.push(listener);
                    id as i64
                }
                Err(e) => {
                    eprintln!("[synapse-gateway] bind error: {}", e);
                    -1
                }
            }
        },
    )?;

    // --- tcp_accept(listener_id) -> conn_id ---
    linker.func_wrap("env", "tcp_accept", |mut caller: Caller<'_, GatewayState>, lid: i64| -> i64 {
        let state = caller.data_mut();
        if let Some(listener) = state.listeners.get(lid as usize) {
            match listener.accept() {
                Ok((mut stream, addr)) => {
                    let _ = stream.set_nodelay(true);
                    let ip = addr.ip();

                    // --- Rate limiting ---
                    let now = Instant::now();
                    let max = state.rate_limit_max;
                    let entry = state.rate_limits.entry(ip).or_insert((0, now));
                    if now.duration_since(entry.1).as_secs() >= 1 {
                        entry.0 = 1;
                        entry.1 = now;
                    } else {
                        entry.0 += 1;
                        if entry.0 > max {
                            // Rate limited — send 429 directly and reject
                            let _ = stream.write_all(
                                b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\nRetry-After: 1\r\nContent-Length: 52\r\n\r\n{\"status\":\"error\",\"error\":\"rate_limit_exceeded\"}"
                            );
                            let _ = stream.flush();
                            drop(stream);
                            return -1;
                        }
                    }

                    // --- Connection compaction ---
                    // Evict closed connections when we exceed 256 slots
                    if state.connections.len() > 256 {
                        let mut new_conns: Vec<Option<TcpStream>> = Vec::new();
                        let mut new_ips: Vec<Option<IpAddr>> = Vec::new();
                        for (i, conn) in state.connections.drain(..).enumerate() {
                            if conn.is_some() {
                                new_conns.push(conn);
                                new_ips.push(state.conn_ips.get(i).copied().flatten());
                            }
                        }
                        state.connections = new_conns;
                        state.conn_ips = new_ips;
                    }

                    let cid = state.connections.len();
                    state.connections.push(Some(stream));
                    state.conn_ips.push(Some(ip));
                    state.active_conn = Some(cid);
                    cid as i64
                }
                Err(_) => -1,
            }
        } else {
            -1
        }
    })?;

    // --- tcp_recv_raw(conn_id, ptr, max_len) -> bytes_read ---
    linker.func_wrap(
        "env",
        "tcp_recv_raw",
        |mut caller: Caller<'_, GatewayState>, cid: i64, ptr: i64, max_len: i64| -> i64 {
            let max = max_len as usize;
            let mut tmp = vec![0u8; max];
            let total = {
                let state = caller.data_mut();
                if let Some(Some(ref mut stream)) = state.connections.get_mut(cid as usize) {
                    let n = match stream.read(&mut tmp) {
                        Ok(n) => n,
                        Err(_) => return -1,
                    };
                    if n == 0 {
                        return 0;
                    }

                    let mut total = n;

                    // Smart retry for reverse proxies (Caddy):
                    // 1. If headers are incomplete (no \r\n\r\n), retry with timeout
                    // 2. If headers are complete but body is missing, retry with timeout
                    let header_end_pos = tmp[..total]
                        .windows(4)
                        .position(|w| w == b"\r\n\r\n")
                        .map(|p| p + 4);

                    let needs_more = if let Some(hdr_end) = header_end_pos {
                        // Headers complete — check if we need to wait for body
                        // Parse Content-Length from headers
                        let hdr = &tmp[..hdr_end];
                        let content_length = extract_content_length(hdr);
                        if content_length > 0 {
                            let body_received = total - hdr_end;
                            body_received < content_length
                        } else {
                            false
                        }
                    } else {
                        // Headers incomplete — need more data
                        true
                    };

                    if needs_more && total < max {
                        let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(5)));
                        loop {
                            match stream.read(&mut tmp[total..]) {
                                Ok(0) => break,
                                Ok(n) => {
                                    total += n;
                                    if total >= max {
                                        break;
                                    }
                                    // Re-check if we have enough data
                                    let done = if let Some(hdr_end) = tmp[..total]
                                        .windows(4)
                                        .position(|w| w == b"\r\n\r\n")
                                        .map(|p| p + 4)
                                    {
                                        let cl = extract_content_length(&tmp[..hdr_end]);
                                        if cl > 0 {
                                            total - hdr_end >= cl
                                        } else {
                                            true // No content-length, headers are done
                                        }
                                    } else {
                                        false // Still no headers
                                    };
                                    if done {
                                        break;
                                    }
                                }
                                Err(_) => break,
                            }
                        }
                        let _ = stream.set_read_timeout(None);
                    }
                    total
                } else {
                    return -1;
                }
            };
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data_mut(&mut caller);
            let start = ptr as usize;
            if start + total > data.len() {
                return -1;
            }
            data[start..start + total].copy_from_slice(&tmp[..total]);
            total as i64
        },
    )?;

    // --- tcp_send_raw(conn_id, ptr, len) -> bytes_sent ---
    linker.func_wrap(
        "env",
        "tcp_send_raw",
        |mut caller: Caller<'_, GatewayState>, cid: i64, ptr: i64, len: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = ptr as usize;
            let end = start + len as usize;
            if end > data.len() {
                return -1;
            }
            let buf = data[start..end].to_vec();
            let state = caller.data_mut();
            if let Some(Some(ref mut stream)) = state.connections.get_mut(cid as usize) {
                match stream.write_all(&buf) {
                    Ok(()) => len,
                    Err(_) => -1,
                }
            } else {
                -1
            }
        },
    )?;

    // --- tcp_close(conn_id) -> 0 on success ---
    linker.func_wrap(
        "env",
        "tcp_close",
        |mut caller: Caller<'_, GatewayState>, cid: i64| -> i64 {
            let state = caller.data_mut();
            let idx = cid as usize;
            if idx < state.connections.len() {
                state.connections[idx] = None;
                0
            } else {
                -1
            }
        },
    )?;

    // --- time_now() -> microseconds since start ---
    linker.func_wrap(
        "env",
        "time_now",
        |caller: Caller<'_, GatewayState>| -> i64 {
            caller.data().epoch.elapsed().as_micros() as i64
        },
    )?;

    // --- sha256_hash(data_ptr, data_len, out_ptr) -> 32 (bytes written) ---
    linker.func_wrap(
        "env",
        "sha256_hash",
        |mut caller: Caller<'_, GatewayState>, data_ptr: i64, data_len: i64, out_ptr: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = data_ptr as usize;
            let end = start + data_len as usize;
            if end > data.len() {
                return -1;
            }
            let input = data[start..end].to_vec();
            let hash = Sha256::digest(&input);
            let out = out_ptr as usize;
            if out + 32 > data.len() {
                return -1;
            }
            let mem_data = mem.data_mut(&mut caller);
            mem_data[out..out + 32].copy_from_slice(&hash);
            32
        },
    )?;

    // --- env_get(key_ptr, key_len, val_out_ptr, val_max_len) -> val_len or -1 ---
    linker.func_wrap(
        "env",
        "env_get",
        |mut caller: Caller<'_, GatewayState>,
         key_ptr: i64,
         key_len: i64,
         val_ptr: i64,
         val_max: i64|
         -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let ks = key_ptr as usize;
            let ke = ks + key_len as usize;
            if ke > data.len() {
                return -1;
            }
            let key = String::from_utf8_lossy(&data[ks..ke]).to_string();
            let val = if key == "API_KEY" {
                String::from_utf8_lossy(&caller.data().api_key).to_string()
            } else {
                std::env::var(&key).unwrap_or_default()
            };
            let vb = val.as_bytes();
            let vl = std::cmp::min(vb.len(), val_max as usize);
            let vs = val_ptr as usize;
            let mem_data = mem.data_mut(&mut caller);
            mem_data[vs..vs + vl].copy_from_slice(&vb[..vl]);
            vl as i64
        },
    )?;

    // --- host_fetch(url_ptr, url_len, out_ptr, max_len) -> bytes written or -1 ---
    linker.func_wrap(
        "env",
        "host_fetch",
        |mut caller: Caller<'_, GatewayState>,
         url_ptr: i64,
         url_len: i64,
         out_ptr: i64,
         max_len: i64|
         -> i64 {
            let max_len = std::cmp::min(max_len, 10_000_000); // 10MB Host Memory Protection
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let s = url_ptr as usize;
            let e = s + url_len as usize;
            if e > data.len() {
                return -1;
            }
            let url = String::from_utf8_lossy(&data[s..e]).to_string();

            // SSRF Blockade
            if url.contains("169.254.169.254")
                || url.contains("127.0.")
                || url.contains("localhost")
                || url.contains("10.")
            {
                return -1;
            }

            // Perform HTTP GET request using ureq
            match ureq::get(&url).call() {
                Ok(response) => {
                    let mut buf = vec![];
                    if response.into_reader().read_to_end(&mut buf).is_ok() {
                        let bytes_to_copy = std::cmp::min(buf.len(), max_len as usize);
                        let out = out_ptr as usize;
                        let data_mut = mem.data_mut(&mut caller);
                        if out + bytes_to_copy <= data_mut.len() {
                            data_mut[out..out + bytes_to_copy]
                                .copy_from_slice(&buf[..bytes_to_copy]);
                            return bytes_to_copy as i64;
                        }
                    }
                    -1
                }
                Err(_) => -1,
            }
        },
    )?;

    // --- host_fs_write(path_ptr, path_len, data_ptr, data_len) -> result (0 success, -1 err) ---
    linker.func_wrap(
        "env",
        "host_fs_write",
        |mut caller: Caller<'_, GatewayState>,
         path_ptr: i64,
         path_len: i64,
         data_ptr: i64,
         data_len: i64|
         -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);

            let ps = path_ptr as usize;
            let pe = ps + path_len as usize;
            if pe > data.len() {
                return -1;
            }
            let path_str = String::from_utf8_lossy(&data[ps..pe]).to_string();

            let ds = data_ptr as usize;
            let de = ds + data_len as usize;
            if de > data.len() {
                return -1;
            }
            let write_data = &data[ds..de];

            // Resolve securely inside a sandbox_volumes directory
            let root_volumes = resolve_synapse_root().join("sandbox_volumes");
            std::fs::create_dir_all(&root_volumes).unwrap_or_default();
            let base_path = std::fs::canonicalize(&root_volumes).unwrap_or(root_volumes);
            let target_path = base_path.join(path_str);

            if let Some(parent) = target_path.parent() {
                std::fs::create_dir_all(parent).unwrap_or_default();
                if let Ok(canon_parent) = std::fs::canonicalize(parent) {
                    if !canon_parent.starts_with(&base_path) {
                        return -1; // Block Path Traversal
                    }
                } else {
                    return -1;
                }
            } else {
                return -1;
            }

            match std::fs::write(&target_path, write_data) {
                Ok(_) => 0,
                Err(_) => -1,
            }
        },
    )?;

    // --- host_fs_read(path_ptr, path_len, out_ptr, max_len) -> bytes read or -1 ---
    linker.func_wrap(
        "env",
        "host_fs_read",
        |mut caller: Caller<'_, GatewayState>,
         path_ptr: i64,
         path_len: i64,
         out_ptr: i64,
         max_len: i64|
         -> i64 {
            let max_len = std::cmp::min(max_len, 10_000_000); // 10MB Host Memory Protection
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let ps = path_ptr as usize;
            let pe = ps + path_len as usize;
            if pe > data.len() {
                return -1;
            }
            let path_str = String::from_utf8_lossy(&data[ps..pe]).to_string();

            let root_volumes = resolve_synapse_root().join("sandbox_volumes");
            std::fs::create_dir_all(&root_volumes).unwrap_or_default();
            let base_path = std::fs::canonicalize(&root_volumes).unwrap_or(root_volumes);
            let target_path = base_path.join(path_str);

            if let Ok(canon_target) = std::fs::canonicalize(&target_path) {
                if !canon_target.starts_with(&base_path) {
                    return -1; // Block Path Traversal
                }
            } else {
                return -1;
            }

            match std::fs::read(&target_path) {
                Ok(file_data) => {
                    let bytes_to_copy = std::cmp::min(file_data.len(), max_len as usize);
                    let out = out_ptr as usize;
                    let data_mut = mem.data_mut(&mut caller);
                    if out + bytes_to_copy <= data_mut.len() {
                        data_mut[out..out + bytes_to_copy]
                            .copy_from_slice(&file_data[..bytes_to_copy]);
                        bytes_to_copy as i64
                    } else {
                        -1
                    }
                }
                Err(_) => -1,
            }
        },
    )?;

    // --- host_mcp_call(server_ptr, server_len, payload_ptr, payload_len, out_ptr, max_len) -> bytes_written or -1 ---
    linker.func_wrap("env", "host_mcp_call",
        |mut caller: Caller<'_, GatewayState>, s_ptr: i64, s_len: i64, p_ptr: i64, p_len: i64, out_ptr: i64, max_len: i64| -> i64 {
            let max_len = std::cmp::min(max_len, 10_000_000); // 10MB Host Memory Protection
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);

            let ss = s_ptr as usize;
            let se = ss + s_len as usize;
            if se > data.len() { return -1; }
            let server_name = String::from_utf8_lossy(&data[ss..se]).to_string();

            let ps = p_ptr as usize;
            let pe = ps + p_len as usize;
            if pe > data.len() { return -1; }
            let _payload = String::from_utf8_lossy(&data[ps..pe]).to_string();

            // MCP Router Scaffold
            // A production environment will parse the JSON-RPC payload
            // and route it to the active stdio/sse MCP server via the GatewayState.
            let simulated_resp = format!("{{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{{\"server\":\"{}\", \"status\":\"mcp_scaffold_active\"}}}}", server_name);
            let resp_bytes = simulated_resp.as_bytes();

            let bytes_to_copy = std::cmp::min(resp_bytes.len(), max_len as usize);
            let out = out_ptr as usize;
            let data_mut = mem.data_mut(&mut caller);

            if out + bytes_to_copy <= data_mut.len() {
                data_mut[out..out + bytes_to_copy].copy_from_slice(&resp_bytes[..bytes_to_copy]);
                bytes_to_copy as i64
            } else {
                -1
            }
        }
    )?;

    // --- host_pause(state_id_ptr, state_id_len) -> result (0 success, -1 err) ---
    // Triggers an instantaneous zero-copy snapshot of the Wasm linear memory.
    linker.func_wrap(
        "env",
        "host_pause",
        |mut caller: Caller<'_, GatewayState>, id_ptr: i64, id_len: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);

            let ids = id_ptr as usize;
            let ide = ids + id_len as usize;
            if ide > data.len() {
                return -1;
            }
            let state_id = String::from_utf8_lossy(&data[ids..ide]).to_string();

            // Zero-copy state dump (instantaneous freeze)
            let snapshot_path = resolve_synapse_root()
                .join("sandbox_snapshots")
                .join(format!("{}.synsnap", state_id));
            if let Some(parent) = snapshot_path.parent() {
                std::fs::create_dir_all(parent).unwrap_or_default();
            }

            match std::fs::write(&snapshot_path, data) {
                Ok(_) => 0,
                Err(_) => -1,
            }
        },
    )?;

    // --- compile_and_exec(code_ptr, code_len) -> result (i32) ---
    linker.func_wrap(
        "env",
        "compile_and_exec",
        |mut caller: Caller<'_, GatewayState>, code_ptr: i64, code_len: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = code_ptr as usize;
            let end = start + code_len as usize;
            if end > data.len() {
                return -1;
            }
            let source = String::from_utf8_lossy(&data[start..end]).to_string();

            let compile_script = format!(
                "import sys; sys.path.insert(0, 'tools'); from sync import compile_syn_source; \
                 r = compile_syn_source({}); \
                 import sys; \
                 wasm = r.get('wasm', b''); \
                 sys.stdout.buffer.write(wasm) if wasm else sys.exit(1)",
                serde_json::to_string(&source).unwrap_or_default()
            );

            let output = match std::process::Command::new("python3")
                .arg("-c")
                .arg(&compile_script)
                .current_dir(resolve_synapse_root())
                .output()
            {
                Ok(o) => o,
                Err(e) => {
                    eprintln!("[compile_and_exec] python3 error: {}", e);
                    return -1;
                }
            };

            if !output.status.success() || output.stdout.is_empty() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                eprintln!("[compile_and_exec] compile error: {}", stderr);
                return -1;
            }

            let wasm_bytes = output.stdout;
            let child_engine = &caller.data().child_engine;

            // Cache lookup by SHA-256 of wasm bytes
            let wasm_hash = format!("{:x}", Sha256::digest(&wasm_bytes));
            let module = if let Some(cached) = caller.data().module_cache.get(&wasm_hash) {
                cached
            } else {
                match Module::new(child_engine, &wasm_bytes) {
                    Ok(m) => {
                        caller.data().module_cache.insert(wasm_hash, m.clone());
                        m
                    }
                    Err(e) => {
                        eprintln!("[compile_and_exec] wasm compile error: {}", e);
                        return -1;
                    }
                }
            };

            let mut child_linker = Linker::<ChildState>::new(child_engine);
            child_linker.allow_shadowing(true);
            if let Err(e) = child_linker.define_unknown_imports_as_traps(&module) {
                eprintln!("[compile_and_exec] child linker trap setup error: {}", e);
                return -1;
            }
            if let Err(e) = child_linker.func_wrap(
                "env",
                "print",
                |mut caller: Caller<'_, ChildState>, ptr: i32, len: i32| {
                    let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
                    let data = mem.data(&caller);
                    let start = ptr as usize;
                    let end = start + len as usize;
                    if end <= data.len() {
                        let chunk = data[start..end].to_vec();
                        caller.data_mut().stdout_buf.extend_from_slice(&chunk);
                    }
                },
            ) {
                eprintln!("[compile_and_exec] child print FFI error: {}", e);
                return -1;
            }

            let mut store = Store::new(
                child_engine,
                ChildState {
                    stdout_buf: Vec::new(),
                },
            );
            store.set_fuel(10_000_000).ok();

            let instance = match child_linker.instantiate(&mut store, &module) {
                Ok(i) => i,
                Err(e) => {
                    eprintln!("[compile_and_exec] instantiate error: {}", e);
                    return -1;
                }
            };

            let main_fn = match instance.get_typed_func::<(), i64>(&mut store, "main") {
                Ok(f) => f,
                Err(_) => return -1,
            };

            match main_fn.call(&mut store, ()) {
                Ok(result) => {
                    let child_stdout = &store.data().stdout_buf;
                    let stdout_len = child_stdout.len();
                    if stdout_len > 0 {
                        let stdout_copy = child_stdout.clone();
                        let pdata = mem.data_mut(&mut caller);
                        let stdout_pos: u32 = 65536;
                        if (stdout_pos as usize + stdout_len) < pdata.len() {
                            pdata[stdout_pos as usize..stdout_pos as usize + stdout_len]
                                .copy_from_slice(&stdout_copy);
                            let ptr_bytes = (stdout_pos as i64).to_le_bytes();
                            let len_bytes = (stdout_len as i64).to_le_bytes();
                            pdata[56..64].copy_from_slice(&ptr_bytes);
                            pdata[64..72].copy_from_slice(&len_bytes);
                        }
                    } else {
                        let pdata = mem.data_mut(&mut caller);
                        pdata[56..64].copy_from_slice(&0i64.to_le_bytes());
                        pdata[64..72].copy_from_slice(&0i64.to_le_bytes());
                    }
                    result
                }
                Err(e) => {
                    eprintln!("[compile_and_exec] exec error: {}", e);
                    -1
                }
            }
        },
    )?;

    // --- transpile_python(code_ptr, code_len) -> syn_len ---
    // Calls the Python SDK transpiler to convert Python source to .syn text.
    // Writes the .syn output to Wasm memory at offset 65536.
    // Stores ptr at memory[56..64] and len at memory[64..72].
    linker.func_wrap(
        "env",
        "transpile_python",
        |mut caller: Caller<'_, GatewayState>, code_ptr: i64, code_len: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = code_ptr as usize;
            let end = start + code_len as usize;
            if end > data.len() {
                return -1;
            }
            let python_source = String::from_utf8_lossy(&data[start..end]).to_string();

            // Call the Python SDK transpiler
            let transpile_script = format!(
                "import sys; sys.path.insert(0, 'sdk'); \
                 from synapse.transpiler import python_to_syn; \
                 result = python_to_syn({}); \
                 sys.stdout.write(result)",
                serde_json::to_string(&python_source).unwrap_or_default()
            );

            let output = match std::process::Command::new("python3")
                .arg("-c")
                .arg(&transpile_script)
                .current_dir(resolve_synapse_root())
                .output()
            {
                Ok(o) => o,
                Err(e) => {
                    eprintln!("[transpile_python] python3 error: {}", e);
                    return -1;
                }
            };

            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                eprintln!("[transpile_python] transpile error: {}", stderr);
                return -1;
            }

            let syn_code = output.stdout;
            let syn_len = syn_code.len();
            if syn_len == 0 {
                return -1;
            }

            // Write .syn code to Wasm memory at offset 65536
            let syn_pos: usize = 65536;
            let pdata = mem.data_mut(&mut caller);
            if syn_pos + syn_len < pdata.len() {
                pdata[syn_pos..syn_pos + syn_len].copy_from_slice(&syn_code);
                // Null-terminate
                if syn_pos + syn_len < pdata.len() {
                    pdata[syn_pos + syn_len] = 0;
                }
                // Store ptr and len at memory[56..72]
                let ptr_bytes = (syn_pos as i64).to_le_bytes();
                let len_bytes = (syn_len as i64).to_le_bytes();
                pdata[56..64].copy_from_slice(&ptr_bytes);
                pdata[64..72].copy_from_slice(&len_bytes);
            }

            syn_len as i64
        },
    )?;

    // --- Native Z3 Verification FFI ---
    // Single-call architecture: .syn code extracts @inv expressions,
    // passes each one to z3_verify_expr which does everything in one Rust call.
    // This avoids Rust lifetime issues with storing z3::ast across FFI calls.

    // --- z3_verify_expr(expr_ptr, expr_len) -> 2 (Z3 PURGE: always unknown) ---
    // FFI signature preserved so guest Wasm code that calls this does not trap.
    // Real Z3 verification was removed in the 2026-04-15 Z3 purge; static @inv
    // proofs belong to the research swarm, not the commercial Cell product.
    // Always returns 2 ("unknown") — guest code should treat as a non-fatal
    // verification miss and fall back to runtime sandbox enforcement.
    linker.func_wrap(
        "env",
        "z3_verify_expr",
        |_caller: Caller<'_, GatewayState>, _expr_ptr: i64, _expr_len: i64| -> i64 {
            2 // Z3 PURGE stub — verification unknown without research solver
        },
    )?;

    // --- z3_verify() STUB TO PREVENT CRASH ---
    linker.func_wrap(
        "env",
        "z3_verify",
        |_caller: Caller<'_, GatewayState>, _a: i64, _b: i64, _c: i64, _d: i64| -> i64 { 0 },
    )?;

    // --- z3_verify_property(property_id, source_ptr, source_len) -> 0=fail, 1=pass, 2=unknown ---
    // Semantic property verification for @inv pure, @inv terminates, @inv no_oob.
    // property_id: 1=pure, 2=terminates, 3=no_oob
    linker.func_wrap(
        "env",
        "z3_verify_property",
        |mut caller: Caller<'_, GatewayState>,
         property_id: i64,
         source_ptr: i64,
         source_len: i64|
         -> i64 {
            // Z3 PURGE 2026-04-15: per-request Z3 budget removed (no Z3 deps in commercial Cell).
            // Cases 1, 2, 4 below are pure structural pattern matching — they still work.
            // Case 3 (@inv no_oob bounds verification) is stubbed because it required Z3.
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = source_ptr as usize;
            let end = start + source_len as usize;
            if end > data.len() {
                return 2;
            }
            let source = match std::str::from_utf8(&data[start..end]) {
                Ok(s) => s.to_string(),
                Err(_) => return 2,
            };

            match property_id {
                // ─── @inv pure ─────────────────────────────────────────
                // Check: does the function body after @inv contain write/write8/FFI?
                // Find the @f declaration that follows the @inv pure annotation.
                1 => {
                    // Find the @inv annotation, then verify the NEXT @f function
                    // This aligns with Python verifier which scans per-annotation
                    let inv_pos = source.find("@inv pure").or_else(|| source.find("@inv "));
                    let func_start = if let Some(ip) = inv_pos {
                        // Find the @f after this @inv
                        source[ip..].find("@f ").map(|offset| ip + offset)
                    } else {
                        source.find("@f ")
                    };
                    if func_start.is_none() {
                        return 2;
                    }
                    let body = &source[func_start.unwrap()..];

                    // Skip "@f N name" prefix — find the function name end
                    // The function body starts after the name (could be on same line)
                    let name_end = body
                        .find([' ', '\n', '['])
                        .and_then(|p| body[p + 1..].find([' ', '\n', '[']).map(|q| p + 1 + q))
                        .unwrap_or(0);
                    let func_body = &body[name_end..];

                    // Find end of this function (next @f or @inv or EOF)
                    let func_end = func_body
                        .find("\n@f ")
                        .or_else(|| func_body.find("\n@inv "))
                        .unwrap_or(func_body.len());
                    let func_body = &func_body[..func_end];

                    // Check for impure operations
                    let has_write = func_body.contains("write ") || func_body.contains("write\t");
                    let has_write8 = func_body.contains("write8 ");
                    let has_ffi = func_body.contains("@import_ffi");

                    if has_write || has_write8 || has_ffi {
                        0 // Impure — verification failed
                    } else {
                        1 // Pure — no side effects found
                    }
                }

                // ─── @inv terminates ───────────────────────────────────
                // Check: do all while loops have bounded counters?
                // Pattern: while < $i N [...let $i + $i 1...]
                2 => {
                    let func_start = source.find("@f ");
                    if func_start.is_none() {
                        return 2;
                    }
                    let body = &source[func_start.unwrap()..];

                    // Check if there are any while loops
                    if !body.contains("while ") {
                        return 1; // No loops = trivially terminates
                    }

                    // For each "while" in the body, check for bounded counter pattern
                    let mut search_from = 0;
                    while let Some(while_pos) = body[search_from..].find("while ") {
                        let abs_pos = search_from + while_pos;
                        let rest = &body[abs_pos + 6..]; // skip "while "

                        // Check for "< $VAR BOUND" pattern (comparison with bound)
                        let has_bound = rest.starts_with("< $") ||
                                       rest.starts_with("<= $") ||
                                       rest.starts_with("& ") || // compound condition starting with &
                                       rest.starts_with("!= ");

                        if !has_bound {
                            // Check if it's a "while $VAR" (flag-based termination)
                            if rest.starts_with("$") {
                                // Flag-based loops — check if the flag is updated/cleared somewhere in the body
                                let flag_var: String = rest
                                    .chars()
                                    .take_while(|c| !c.is_whitespace() && *c != '[')
                                    .collect();
                                let body_start = rest.find('[');
                                let body_end = rest.find(']');

                                if let (Some(s), Some(e)) = (body_start, body_end) {
                                    let loop_body = &rest[s..e];
                                    let update_set = format!("set {}", flag_var);
                                    let update_let = format!("let {}", flag_var);

                                    if !loop_body.contains(&update_set)
                                        && !loop_body.contains(&update_let)
                                    {
                                        return 0; // Unbounded flag loop detected
                                    }
                                } else {
                                    return 0; // Malformed loop body
                                }
                                search_from = abs_pos + 6;
                                continue;
                            }
                            return 0; // Unbounded loop detected
                        }

                        search_from = abs_pos + 6;
                    }

                    1 // All loops appear bounded
                }

                // ─── @inv no_oob ───────────────────────────────────────
                // Z3 PURGE 2026-04-15: real bounds verification required Z3 to
                // reason about loop-derived offset ranges (write/read + $ptr * $i S
                // with $i ∈ [0, N-1] etc.). With Z3 removed for the commercial
                // Cell product, we fall back to a coarse structural check:
                //   - no memory access            → 1 (trivially safe)
                //   - read-only from arguments    → 1 (conservative pass)
                //   - any alloc + write present   → 2 (unknown — runtime sandbox enforces)
                // The runtime Wasm sandbox already bounds-checks all memory access
                // at execution time; this is defense-in-depth, not the primary
                // safety mechanism. Re-introduce Z3 to restore static proofs.
                3 => {
                    let func_start = source.find("@f ");
                    if func_start.is_none() {
                        return 2;
                    }
                    let body = &source[func_start.unwrap()..];

                    let has_read = body.contains("read ") || body.contains("read8 ");
                    let has_write = body.contains("write ") || body.contains("write8 ");

                    if !has_read && !has_write {
                        return 1; // No memory access = trivially safe
                    }

                    let has_alloc = body.contains("alloc ");
                    if !has_alloc && !has_write {
                        return 1; // Read-only from arguments = conservative pass
                    }

                    2 // Unknown — Wasm runtime sandbox is the actual safety net here
                }

                // ─── @inv trains ───────────────────────────────────────
                // Check: does this function have correct training loop structure?
                // Verifies: ad_begin → loss → ad_backward → optimizer, in a bounded loop.
                // Structural pattern matching (no Z3), consistent with @inv pure/@inv terminates.
                4 => {
                    let func_start = source.find("@f ");
                    if func_start.is_none() {
                        return 2;
                    }
                    let body = &source[func_start.unwrap()..];

                    // Required components
                    let has_ad_begin = body.contains("ad_begin");
                    let has_loss =
                        body.contains("ad_mse_loss") || body.contains("ad_cross_entropy_loss");
                    let has_backward = body.contains("ad_backward");
                    let has_optimizer =
                        body.contains("ad_sgd_step") || body.contains("ad_adamw_step");

                    if !has_ad_begin || !has_loss || !has_backward || !has_optimizer {
                        return 0; // Missing required training component
                    }

                    // Order check: loss before backward before optimizer
                    let loss_pos = body
                        .find("ad_mse_loss")
                        .or_else(|| body.find("ad_cross_entropy_loss"));
                    let backward_pos = body.find("ad_backward");
                    let optimizer_pos = body
                        .find("ad_sgd_step")
                        .or_else(|| body.find("ad_adamw_step"));

                    let ordered = match (loss_pos, backward_pos, optimizer_pos) {
                        (Some(l), Some(b), Some(o)) => l < b && b < o,
                        _ => false,
                    };

                    if !ordered {
                        return 0; // Wrong order: must be loss → backward → optimizer
                    }

                    // Bounded loop check: training must happen in a while loop
                    let has_while = body.contains("while ");
                    if !has_while {
                        return 0; // No training loop found
                    }

                    // Check all while loops are bounded (same logic as @inv terminates)
                    let mut search_from = 0;
                    while let Some(while_pos) = body[search_from..].find("while ") {
                        let abs_pos = search_from + while_pos;
                        let rest = &body[abs_pos + 6..];
                        let has_bound = rest.starts_with("< $")
                            || rest.starts_with("<= $")
                            || rest.starts_with("& ")
                            || rest.starts_with("!= ");
                        if !has_bound {
                            if rest.starts_with("$") {
                                let flag_var: String = rest
                                    .chars()
                                    .take_while(|c| !c.is_whitespace() && *c != '[')
                                    .collect();
                                let body_start = rest.find('[');
                                let body_end = rest.find(']');

                                if let (Some(s), Some(e)) = (body_start, body_end) {
                                    let loop_body = &rest[s..e];
                                    let update_set = format!("set {}", flag_var);
                                    let update_let = format!("let {}", flag_var);

                                    if !loop_body.contains(&update_set)
                                        && !loop_body.contains(&update_let)
                                    {
                                        return 0; // Unbounded training flag loop detected
                                    }
                                } else {
                                    return 0; // Malformed loop body
                                }
                                search_from = abs_pos + 6;
                                continue;
                            }
                            return 0; // Unbounded training loop
                        }
                        search_from = abs_pos + 6;
                    }

                    1 // Valid training loop structure
                }

                // ─── @inv infers ──────────────────────────────────────
                // Check: does this function have inference-only structure?
                // Verifies: has forward pass, no backward, no optimizer.
                5 => {
                    let func_start = source.find("@f ");
                    if func_start.is_none() {
                        return 2;
                    }
                    let body = &source[func_start.unwrap()..];

                    // Must have forward pass (weight loading or matmul)
                    let has_forward = body.contains("load_weights")
                        || body.contains("gpu_matmul")
                        || body.contains("ad_matmul");

                    if !has_forward {
                        return 0; // No forward pass found
                    }

                    // Must NOT have training operations
                    let has_backward = body.contains("ad_backward");
                    let has_optimizer =
                        body.contains("ad_sgd_step") || body.contains("ad_adamw_step");

                    if has_backward || has_optimizer {
                        return 0; // Training ops found in inference program
                    }

                    1 // Valid inference structure
                }
                // ─── @inv pure_ternary ─────────────────────────────────
                // Check: does this function avoid ALL floating-point accumulations and operations?
                // Verifies: no float ops or tensor ops.
                6 => {
                    let func_start = source.find("@f ");
                    if func_start.is_none() {
                        return 2;
                    }
                    let body = &source[func_start.unwrap()..];

                    let has_float_add = body.contains("f32.add") || body.contains("f64.add");
                    let has_float_mul = body.contains("f32.mul") || body.contains("f64.mul");
                    let has_tensor = body.contains("tensor.matmul") || body.contains("ad_matmul");
                    let has_grad = body.contains("@grad");

                    if has_float_add || has_float_mul || has_tensor || has_grad {
                        return 0; // Floating point pollution detected
                    }

                    1 // Valid pure ternary topology
                }

                _ => 2, // Unknown property
            }
        },
    )?;

    // --- spawn_wasm(ptr, len) -> result (i64) ---
    linker.func_wrap(
        "env",
        "spawn_wasm",
        |mut caller: Caller<'_, GatewayState>, wasm_ptr: i64, wasm_len: i64| -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let start = wasm_ptr as usize;
            let end = start + wasm_len as usize;
            if end > data.len() {
                write_gateway_payload(
                    &mut caller,
                    br#"{"status":"error","error":"invalid_wasm_input"}"#,
                );
                return -1;
            }
            let wasm_bytes = data[start..end].to_vec();
            let child_engine = caller.data().child_engine.clone();
            clear_gateway_payload(&mut caller);

            // Cache lookup by SHA-256 of wasm bytes
            let wasm_hash = format!("{:x}", Sha256::digest(&wasm_bytes));
            let module = if let Some(cached) = caller.data().module_cache.get(&wasm_hash) {
                cached
            } else {
                match Module::new(&child_engine, &wasm_bytes) {
                    Ok(m) => {
                        caller.data().module_cache.insert(wasm_hash, m.clone());
                        m
                    }
                    Err(e) => {
                        eprintln!("[spawn_wasm] wasm compile error: {}", e);
                        write_gateway_payload(
                            &mut caller,
                            br#"{"status":"error","error":"compile_failed"}"#,
                        );
                        return -1;
                    }
                }
            };

            let mut child_linker = Linker::<ChildState>::new(&child_engine);
            child_linker.allow_shadowing(true);
            if let Err(e) = child_linker.define_unknown_imports_as_traps(&module) {
                eprintln!("[spawn_wasm] child linker error: {}", e);
                write_gateway_payload(
                    &mut caller,
                    br#"{"status":"error","error":"runtime_setup_failed"}"#,
                );
                return -1;
            }
            if let Err(e) = child_linker.func_wrap(
                "env",
                "print",
                |mut caller: Caller<'_, ChildState>, ptr: i32, len: i32| {
                    let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
                    let data = mem.data(&caller);
                    let start = ptr as usize;
                    let end = start + len as usize;
                    if end <= data.len() {
                        let chunk = data[start..end].to_vec();
                        caller.data_mut().stdout_buf.extend_from_slice(&chunk);
                    }
                },
            ) {
                eprintln!("[spawn_wasm] child print FFI error: {}", e);
                write_gateway_payload(
                    &mut caller,
                    br#"{"status":"error","error":"runtime_setup_failed"}"#,
                );
                return -1;
            }

            let mut store = Store::new(
                &child_engine,
                ChildState {
                    stdout_buf: Vec::new(),
                },
            );
            store.set_fuel(10_000_000).ok();

            let instance = match child_linker.instantiate(&mut store, &module) {
                Ok(i) => i,
                Err(e) => {
                    eprintln!("[spawn_wasm] instantiate error: {}", e);
                    write_gateway_payload(
                        &mut caller,
                        br#"{"status":"error","error":"runtime_failed"}"#,
                    );
                    return -1;
                }
            };

            let main_fn = match instance.get_typed_func::<(), i64>(&mut store, "main") {
                Ok(f) => f,
                Err(_) => {
                    write_gateway_payload(
                        &mut caller,
                        br#"{"status":"error","error":"runtime_failed"}"#,
                    );
                    return -1;
                }
            };

            match main_fn.call(&mut store, ()) {
                Ok(result) => {
                    let child_stdout = &store.data().stdout_buf;
                    let stdout_len = child_stdout.len();
                    if stdout_len > 0 {
                        let stdout_copy = child_stdout.clone();
                        let pdata = mem.data_mut(&mut caller);
                        let stdout_pos: u32 = 65536;
                        if (stdout_pos as usize + stdout_len) < pdata.len() {
                            pdata[stdout_pos as usize..stdout_pos as usize + stdout_len]
                                .copy_from_slice(&stdout_copy);
                            let ptr_bytes = (stdout_pos as i64).to_le_bytes();
                            let len_bytes = (stdout_len as i64).to_le_bytes();
                            pdata[56..64].copy_from_slice(&ptr_bytes);
                            pdata[64..72].copy_from_slice(&len_bytes);
                        }
                    } else {
                        let pdata = mem.data_mut(&mut caller);
                        pdata[56..64].copy_from_slice(&0i64.to_le_bytes());
                        pdata[64..72].copy_from_slice(&0i64.to_le_bytes());
                    }
                    result
                }
                Err(e) => {
                    eprintln!("[spawn_wasm] exec error: {:#?}", e);
                    write_gateway_payload(
                        &mut caller,
                        br#"{"status":"error","error":"runtime_failed"}"#,
                    );
                    -1
                }
            }
        },
    )?;

    // --- kv_get(key_ptr, key_len, val_out_ptr, val_max_len) -> val_len ---
    linker.func_wrap(
        "env",
        "kv_get",
        |mut caller: Caller<'_, GatewayState>,
         key_ptr: i64,
         key_len: i64,
         val_ptr: i64,
         val_max: i64|
         -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let ks = key_ptr as usize;
            let ke = ks + key_len as usize;
            if ke > data.len() {
                return -1;
            }
            let key = String::from_utf8_lossy(&data[ks..ke]).to_string();

            let db_path = format!("{}/default.db", caller.data().db_path);
            let conn = match rusqlite::Connection::open(&db_path) {
                Ok(c) => c,
                Err(_) => return -1,
            };
            let _ = conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)",
                [],
            );
            let result: Result<String, _> =
                conn.query_row("SELECT value FROM kv WHERE key = ?1", [&key], |row| {
                    row.get(0)
                });
            match result {
                Ok(val) => {
                    let vb = val.as_bytes();
                    let vl = std::cmp::min(vb.len(), val_max as usize);
                    let vs = val_ptr as usize;
                    let mem_data = mem.data_mut(&mut caller);
                    mem_data[vs..vs + vl].copy_from_slice(&vb[..vl]);
                    vl as i64
                }
                Err(_) => 0,
            }
        },
    )?;

    // --- kv_set(key_ptr, key_len, val_ptr, val_len) -> 0 on success ---
    linker.func_wrap(
        "env",
        "kv_set",
        |mut caller: Caller<'_, GatewayState>,
         key_ptr: i64,
         key_len: i64,
         val_ptr: i64,
         val_len: i64|
         -> i64 {
            let mem = caller.get_export("memory").unwrap().into_memory().unwrap();
            let data = mem.data(&caller);
            let ks = key_ptr as usize;
            let ke = ks + key_len as usize;
            let vs = val_ptr as usize;
            let ve = vs + val_len as usize;
            if ke > data.len() || ve > data.len() {
                return -1;
            }
            let key = String::from_utf8_lossy(&data[ks..ke]).to_string();
            let val = String::from_utf8_lossy(&data[vs..ve]).to_string();

            let db_path = format!("{}/default.db", caller.data().db_path);
            let conn = match rusqlite::Connection::open(&db_path) {
                Ok(c) => c,
                Err(_) => return -1,
            };
            let _ = conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)",
                [],
            );
            match conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?1, ?2)",
                [&key, &val],
            ) {
                Ok(_) => 0,
                Err(_) => -1,
            }
        },
    )?;

    Ok(())
}

/// Run the trap recovery loop for one worker thread.
fn run_worker(
    thread_id: usize,
    listener: TcpListener,
    engine: &Engine,
    module: &Module,
    api_key: &[u8],
    db_path: &str,
    rate_limit_max: u64,
    module_cache: ModuleCache,
    child_engine: Arc<Engine>,
) {
    // Build a fresh linker for this thread
    let mut linker = Linker::<GatewayState>::new(engine);
    linker.allow_shadowing(true);
    register_ffi(&mut linker).expect("FFI registration");

    // Create store with the pre-bound listener already injected
    let mut store = Store::new(
        engine,
        GatewayState {
            listeners: vec![listener],
            connections: Vec::new(),
            stdout_buf: Vec::new(),
            epoch: Instant::now(),
            child_engine,
            api_key: api_key.to_vec(),
            db_path: db_path.to_string(),
            active_conn: None,
            rate_limits: HashMap::new(),
            rate_limit_max,
            conn_ips: Vec::new(),
            module_cache,
            p2p_streams: Vec::new(),
        },
    );

    eprintln!("[synapse-gateway] Thread {} started", thread_id);

    // Trap recovery loop
    loop {
        let instance = linker.instantiate(&mut store, module).expect("instantiate");
        let main_fn = instance
            .get_typed_func::<(), i64>(&mut store, "main")
            .expect("main fn");

        match main_fn.call(&mut store, ()) {
            Ok(_) => {
                eprintln!(
                    "[synapse-gateway] Thread {} main() returned normally",
                    thread_id
                );
                break;
            }
            Err(e) => {
                eprintln!(
                    "[synapse-gateway] Thread {} TRAP (recovering): {:#}",
                    thread_id, e
                );
                // Send HTTP 500 to the client that caused the trap
                if let Some(conn_idx) = store.data().active_conn {
                    if let Some(Some(ref mut stream)) =
                        store.data_mut().connections.get_mut(conn_idx)
                    {
                        let _ =
                            stream.set_write_timeout(Some(std::time::Duration::from_millis(50)));
                        let _ = stream.write_all(
                            b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: 42\r\n\r\n{\"status\":\"error\",\"error\":\"compile_trap\"}"
                        );
                        let _ = stream.flush();
                    }
                }
                // Clear connections — re-instantiated Wasm expects fresh indices
                // listeners[0] persists — that's our pre-bound listener
                store.data_mut().connections.clear();
                store.data_mut().active_conn = None;
                store.data_mut().stdout_buf.clear();
                store.data_mut().conn_ips.clear();
                store.data_mut().p2p_streams.clear();
            }
        }
    }
}

/// Embedded gateway.wasm — compiled into the binary at build time.
/// TODO: the actual payload should be generated or provided via a proper release process
static EMBEDDED_GATEWAY_WASM: &[u8] = include_bytes!("../gateway.wasm");

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let wasm_override = std::env::args()
        .nth(1)
        .or_else(|| std::env::var("SYNAPSE_GATEWAY_WASM").ok());
    let api_key = std::env::var("SYNAPSE_API_KEY").unwrap_or_else(|_| {
        eprintln!("[synapse-gateway] Error: SYNAPSE_API_KEY environment variable is required");
        eprintln!("[synapse-gateway] Usage: SYNAPSE_API_KEY=your_key synapse-gateway");
        std::process::exit(1);
    });
    let db_path = std::env::var("SYNAPSE_DB_PATH").unwrap_or_else(|_| "data/state".into());
    let listen_port: u16 = std::env::var("SYNAPSE_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8000);
    let rate_limit_max: u64 = std::env::var("SYNAPSE_RATE_LIMIT")
        .ok()
        .and_then(|r| r.parse().ok())
        .unwrap_or(200);

    // Load gateway.wasm: embedded by default, override with file path
    let wasm_bytes: Vec<u8> = if let Some(ref path) = wasm_override {
        eprintln!("[synapse-gateway] Loading gateway from file: {}", path);
        std::fs::read(path)?
    } else {
        eprintln!(
            "[synapse-gateway] Using embedded gateway.wasm ({} bytes)",
            EMBEDDED_GATEWAY_WASM.len()
        );
        EMBEDDED_GATEWAY_WASM.to_vec()
    };
    eprintln!(
        "[synapse-gateway] API key: {}...",
        &api_key[..std::cmp::min(12, api_key.len())]
    );

    // Engine with optimizations
    let mut config = Config::new();
    config.cranelift_opt_level(OptLevel::Speed);
    let engine = Engine::new(&config)?;

    // Compile gateway module — Module is Send + Sync, shared across all threads
    let module = Module::new(&engine, &wasm_bytes)?;

    // Pre-bind the TCP listener — all threads share via try_clone()
    let main_listener = TcpListener::bind(format!("0.0.0.0:{}", listen_port))?;

    // Cap at 16 threads: the 7950X3D has asymmetric CCDs (8 V-Cache + 8 standard).
    // 32 hyperthreads cause cache-line bouncing during JIT compilation.
    let num_threads = std::thread::available_parallelism()
        .map(|n| n.get().min(16))
        .unwrap_or(4);

    // Shared compiled-module cache (thread-safe, LRU, max 10,000 entries)
    let module_cache: ModuleCache = Cache::new(10_000);

    // Single shared child engine for spawn_wasm/compile_and_exec
    // All cached modules must be compiled against the SAME engine to avoid cross-Engine errors.
    let mut child_config = Config::new();
    child_config.cranelift_opt_level(OptLevel::Speed);
    child_config.consume_fuel(true);
    let child_engine = Arc::new(Engine::new(&child_config)?);

    eprintln!(
        "[synapse-gateway] Self-hosting .syn gateway running on port {}",
        listen_port
    );
    eprintln!("[synapse-gateway] Thread-per-core: {} threads", num_threads);
    eprintln!("[synapse-gateway] Module cache: 10,000 entries (shared across threads)");
    eprintln!("[synapse-gateway] Written in .syn, compiled to Wasm, executed on wasmtime");

    // All thread handles (cell API + gateway workers)
    let mut handles = Vec::new();

    // ─── .cell API ──────────────────────────────────────────────
    let cell_port: u16 = std::env::var("CELL_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8002);
    let cell_api_key = Arc::new(std::env::var("CELL_API_KEY").unwrap_or_else(|_| String::new()));
    if cell_api_key.is_empty() {
        eprintln!("[.cell] ⚠ No CELL_API_KEY set — API is UNAUTHENTICATED");
    } else {
        eprintln!("[.cell] ✓ API key authentication enabled");
    }
    let cells_root = std::env::var("CELL_DATA_DIR").unwrap_or_else(|_| "/tmp/synapse-cells".into());
    let template_dir = std::env::var("CELL_TEMPLATE_DIR")
        .unwrap_or_else(|_| format!("{}/templates", resolve_synapse_root().display()));

    let cert_env = std::env::var("SYNAPSE_LICENSE_CERT").ok();
    let license_status = license::validate_license(cert_env);

    match &license_status {
        license::LicenseStatus::Valid(info) => {
            eprintln!(
                "[synapse-gateway] 🔑 License Valid: {} ({}) - Expires: {}",
                info.company_name, info.tier, info.expires_at
            );
        }
        license::LicenseStatus::EdgeCell => {
            eprintln!("[synapse-gateway] ⚠ No Commercial License Found. Operating in EdgeCell (Free) mode.");
            eprintln!("[synapse-gateway] ⚠ EdgeCell Limits: 10 concurrent sandboxes, no persistent sessions, no custom templates.");
        }
        license::LicenseStatus::Expired(info) => {
            eprintln!("[synapse-gateway] ❌ License Expired: {} ({}) - Expired at: {}. Operating in EdgeCell mode.", info.company_name, info.tier, info.expires_at);
        }
        license::LicenseStatus::Invalid(err) => {
            eprintln!(
                "[synapse-gateway] ❌ Invalid License: {}. Operating in EdgeCell mode.",
                err
            );
        }
    }

    let cell_manager = match cell::CellManager::new(
        std::path::PathBuf::from(&cells_root),
        std::path::PathBuf::from(&template_dir),
        license_status.clone(),
    ) {
        Ok(cm) => Arc::new(cm),
        Err(e) => {
            eprintln!("[.cell] Failed to initialize CellManager: {}", e);
            eprintln!("[.cell] Cell API will not be available");
            // Continue without cell API — gateway still works
            Arc::new(
                cell::CellManager::new(
                    std::path::PathBuf::from("/tmp/synapse-cells-fallback"),
                    std::path::PathBuf::from(&template_dir),
                    license_status,
                )
                .unwrap(),
            )
        }
    };

    // Load Wasm templates from disk if available
    for (name, filename) in [("python3", "python3.wasm"), ("javascript", "quickjs.wasm")] {
        let path = std::path::Path::new(&template_dir).join(filename);
        if path.exists() {
            match std::fs::read(&path) {
                Ok(bytes) => {
                    let _ = cell_manager.register_template(name, bytes);
                }
                Err(e) => eprintln!("[.cell] Failed to load {}: {}", filename, e),
            }
        } else {
            eprintln!(
                "[.cell] Template not found: {} (will be available after compilation)",
                path.display()
            );
        }
    }

    // Sprint C Phase C2: Load custom template metadata from templates_root
    if cell_manager.templates_root.exists() {
        if let Ok(entries) = std::fs::read_dir(&cell_manager.templates_root) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().map(|e| e == "json").unwrap_or(false) {
                    if let Ok(data) = std::fs::read_to_string(&path) {
                        if let Ok(info) = serde_json::from_str::<cell::TemplateInfo>(&data) {
                            // Don't overwrite built-ins
                            if !["python3", "javascript", "synapse"].contains(&info.name.as_str()) {
                                let name = info.name.clone();
                                let _ = cell_manager.register_custom_template(info, None);
                                eprintln!("[.cell] Loaded custom template: {}", name);
                            }
                        }
                    }
                }
            }
        }
    }
    // Also load from cell/templates/ directory for template specs
    {
        let cell_templates_dir = std::path::Path::new(&template_dir)
            .parent()
            .unwrap_or(std::path::Path::new(&template_dir))
            .join("cell")
            .join("templates");
        if cell_templates_dir.exists() {
            if let Ok(entries) = std::fs::read_dir(&cell_templates_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().map(|e| e == "json").unwrap_or(false) {
                        if let Ok(data) = std::fs::read_to_string(&path) {
                            if let Ok(info) = serde_json::from_str::<cell::TemplateInfo>(&data) {
                                if !["python3", "javascript", "synapse"]
                                    .contains(&info.name.as_str())
                                {
                                    let name = info.name.clone();
                                    let _ = cell_manager.register_custom_template(info, None);
                                    eprintln!("[.cell] Loaded template spec: {}", name);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Start the persistent session reaper thread (cleans up idle sessions every 30s)
    cell_manager.start_reaper();
    eprintln!("[.cell] ✓ Persistent sessions enabled (pickle-based state, 1hr default timeout)");

    // Bind Cell API listener
    let cell_listener = match TcpListener::bind(format!("0.0.0.0:{}", cell_port)) {
        Ok(l) => {
            eprintln!("[.cell] ✓ Cell API running on port {}", cell_port);
            eprintln!("[.cell] ✓ Data directory: {}", cells_root);
            Some(l)
        }
        Err(e) => {
            eprintln!("[.cell] Failed to bind port {}: {}", cell_port, e);
            None
        }
    };

    // Load static pages for the Cell API
    let static_pages = {
        let mut pages = std::collections::HashMap::new();
        let preview_dir = std::path::Path::new(&resolve_synapse_root()).join("website/preview");
        let page_map = [
            ("/", "cell.html"),
            ("/cell", "cell.html"),
            ("/blog/250x-faster", "blog-250x-faster.html"),
            ("/verify", "verify.html"),
            ("/dashboard", "dashboard.html"),
            ("/myles", "myles.html"),
            ("/docs", "docs.html"),
        ];
        for (route, filename) in &page_map {
            let path = preview_dir.join(filename);
            match std::fs::read_to_string(&path) {
                Ok(html) => {
                    eprintln!(
                        "[.cell] ✓ Static page {} → {} ({} bytes)",
                        route,
                        filename,
                        html.len()
                    );
                    pages.insert(route.to_string(), html);
                }
                Err(_) => {
                    eprintln!("[.cell] Page not found: {} ({})", route, path.display());
                }
            }
        }
        Arc::new(pages)
    };

    // Spawn Cell API worker threads (4 threads for cell API)
    let cell_threads = 4;
    if let Some(ref cell_listener) = cell_listener {
        for cell_thread_id in 0..cell_threads {
            let listener_clone = cell_listener.try_clone().expect("cell listener clone");
            let cm = Arc::clone(&cell_manager);
            let key = Arc::clone(&cell_api_key);
            let pages = Arc::clone(&static_pages);
            handles.push(
                std::thread::Builder::new()
                    .name(format!("cell-api-{}", cell_thread_id))
                    .spawn(move || {
                        cell_api::run_cell_api(listener_clone, cm, cell_thread_id, key, pages);
                    })?,
            );
        }
    }

    // Spawn WebSocket Terminals (Task 8) on port 8003
    let ws_port = std::env::var("SYNAPSE_WS_PORT").unwrap_or_else(|_| "8003".to_string());
    if let Ok(ws_listener) = std::net::TcpListener::bind(format!("0.0.0.0:{}", ws_port)) {
        eprintln!(
            "[.cell] WebSocket Terminal Proxy listening on port {}",
            ws_port
        );
        let cm_ws = Arc::clone(&cell_manager);
        handles.push(
            std::thread::Builder::new()
                .name("cell-ws-api".into())
                .spawn(move || {
                    ws_api::run_ws_api(ws_listener, cm_ws);
                })?,
        );
    } else {
        eprintln!("[.cell] Failed to bind WebSocket Terminal on port 8003.");
    }

    // Share immutable data across threads
    let engine = Arc::new(engine);
    let module = Arc::new(module);
    let api_key_bytes = Arc::new(api_key.into_bytes());
    let db_path = Arc::new(db_path);

    // Spawn N-1 gateway worker threads
    for thread_id in 1..num_threads {
        let listener_clone = main_listener.try_clone()?;
        let engine = Arc::clone(&engine);
        let module = Arc::clone(&module);
        let api_key_bytes = Arc::clone(&api_key_bytes);
        let db_path = Arc::clone(&db_path);
        let cache = module_cache.clone();
        let child_eng = Arc::clone(&child_engine);

        handles.push(
            std::thread::Builder::new()
                .name(format!("synapse-worker-{}", thread_id))
                .spawn(move || {
                    run_worker(
                        thread_id,
                        listener_clone,
                        &engine,
                        &module,
                        &api_key_bytes,
                        &db_path,
                        rate_limit_max,
                        cache,
                        child_eng,
                    );
                })?,
        );
    }

    // Main thread runs worker 0
    let listener_clone = main_listener.try_clone()?;
    run_worker(
        0,
        listener_clone,
        &engine,
        &module,
        &api_key_bytes,
        &db_path,
        rate_limit_max,
        module_cache,
        child_engine,
    );

    // Wait for worker threads
    for handle in handles {
        let _ = handle.join();
    }

    Ok(())
}
