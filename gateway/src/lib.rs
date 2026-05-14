#![cfg(feature = "python-ext")]
// Library modules are consumed by external binaries — suppress false-positive
// dead_code warnings for structs/functions that are public API surface.
#![allow(dead_code)]
use pyo3::prelude::*;
use std::path::PathBuf;

mod cell;
mod cell_api;
mod compiler;
mod inference;
pub mod license;
mod transpiler;
mod util;
mod ws_api;

#[pyclass]
struct NativeCell {
    manager: std::sync::Arc<cell::CellManager>,
    cell_id: String,
}

#[pymethods]
impl NativeCell {
    #[new]
    fn new(template: String, cells_root: String, template_dir: String) -> PyResult<Self> {
        let template_dir_pb = PathBuf::from(&template_dir);
        let cert_env = std::env::var("SYNAPSE_LICENSE_CERT").ok();
        let license_status = license::validate_license(cert_env);
        let mgr_res = cell::CellManager::new(
            PathBuf::from(cells_root),
            template_dir_pb.clone(),
            license_status,
        );
        let manager = std::sync::Arc::new(
            mgr_res.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?,
        );

        // Sprint C: Load pre-compiled Wasm runtime binaries from template_dir
        // so PyO3 local mode can execute real CPython/QuickJS when the .syn
        // transpiler can't handle the code.
        for (name, filename) in [("python3", "python3.wasm"), ("javascript", "quickjs.wasm")] {
            let path = template_dir_pb.join(filename);
            if path.exists() {
                if let Ok(bytes) = std::fs::read(&path) {
                    // Skip stub files (< 100 bytes)
                    if bytes.len() > 100 {
                        let _ = manager.register_template(name, bytes);
                    }
                }
            }
        }

        // Disable reaping when initialized directly by the Python process to tie its lifecycle to Python
        // manager.start_reaper();

        let info = manager
            .create_cell(
                &template,
                300_000,
                std::collections::HashMap::new(),
                std::collections::HashMap::new(),
                None,
            )
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

        Ok(NativeCell {
            manager,
            cell_id: info.cell_id,
        })
    }

    #[getter]
    fn cell_id(&self) -> String {
        self.cell_id.clone()
    }

    fn get_info(&self, py: Python<'_>) -> PyResult<PyObject> {
        let info = self.manager.get_cell(&self.cell_id).ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("cell not found: {}", self.cell_id))
        })?;
        // Use the E2B-shaped view already on CellInfo (landed in commit 26551b7).
        let json = serde_json::to_string(&info.to_sandbox_info_json())
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        // Cross the FFI by round-tripping through json.loads() — simplest way to
        // produce a Python dict without hand-mapping every field.
        let json_mod = py.import_bound("json")?;
        let loads = json_mod.getattr("loads")?;
        Ok(loads.call1((json,))?.into())
    }

    fn run(
        &self,
        code: String,
    ) -> PyResult<(
        String,
        String,
        i32,
        f64,
        String,
        String,
        String,
        String,
        u64,
        String,
    )> {
        let result = self
            .manager
            .exec_persistent(&self.cell_id, &code, None)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

        // JC-014 (2026-04-28): Receipt fields plumbed through PyO3 so free-tier
        // local-mode cells produce real receipts. Previously result.receipt was
        // always None in local mode, contradicting the Show HN claim that
        // "every execution produces a SHA-256 hash chain". The chain hash
        // (receipt_hash) is now non-empty on every cell.run() return.
        Ok((
            result.stdout,
            result.stderr,
            result.exit_code,
            result.latency_ms,
            result.receipt.execution_id,
            result.receipt.code_hash,
            result.receipt.result_hash,
            result.receipt.template,
            result.receipt.timestamp,
            result.receipt.receipt_hash,
        ))
    }
}

#[pymodule]
fn synapse_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativeCell>()?;
    Ok(())
}
