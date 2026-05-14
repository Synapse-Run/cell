//! Shared utilities used by both the binary entry point (`main.rs`) and the
//! PyO3 library entry point (`lib.rs`).
//!
//! When the gateway crate is compiled as a binary, `cell.rs` could reach
//! these helpers via `super::*` (because `super` resolves to `main`). When
//! the same crate is compiled as a library (for the maturin/pyo3 wheel),
//! `super` resolves to `lib`, and helpers defined only in `main.rs` become
//! invisible. Pulling them into a shared `util` module that both `main.rs`
//! and `lib.rs` declare with `mod util;` restores visibility to all callers
//! via the absolute path `crate::util::*`.
//!
//! See `synapse/cell/journals/JC-002_wasmtime_upgrade.md` for the full
//! rationale.

use std::path::PathBuf;

/// Resolve the on-disk root of the Synapse repo / deployment.
///
/// Order of resolution:
///   1. The `SYNAPSE_ROOT` environment variable, if set.
///   2. The current working directory, if it contains an `sdk/` subdirectory.
///   3. The parent of the current working directory, if it contains an
///      `sdk/` subdirectory (handles being called from a subdir like
///      `cell/gateway/`).
///   4. Falls back to the current working directory.
pub fn resolve_synapse_root() -> PathBuf {
    if let Ok(root) = std::env::var("SYNAPSE_ROOT") {
        return PathBuf::from(root);
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if cwd.join("sdk").exists() {
        return cwd;
    }
    if let Some(parent) = cwd.parent() {
        if parent.join("sdk").exists() {
            return parent.to_path_buf();
        }
    }
    cwd
}
