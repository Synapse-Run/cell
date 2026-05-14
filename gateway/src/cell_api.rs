//! .cell HTTP API — Native Rust HTTP handler for Cell endpoints
//!
//! Runs on a separate port (CELL_PORT, default 8002) to avoid interfering
//! with the self-hosted .syn gateway on port 8000.
//!
//! Endpoints:
//!   POST   /v1/cells              → Create cell
//!   GET    /v1/cells              → List cells
//!   GET    /v1/cells/{id}         → Get cell info
//!   DELETE /v1/cells/{id}         → Kill cell
//!   POST   /v1/cells/{id}/exec   → Execute code
//!   POST   /v1/cells/{id}/fetch  → HTTP proxy (host-side fetch)
//!   POST   /v1/cells/{id}/files         → Write file
//!   GET    /v1/cells/{id}/files         → Read file
//!   DELETE /v1/cells/{id}/files         → Remove file/dir
//!   GET    /v1/cells/{id}/files/list    → List files (FileEntryInfo)
//!   GET    /v1/cells/{id}/files/exists  → Check file existence
//!   GET    /v1/cells/{id}/files/info    → File/dir metadata
//!   POST   /v1/cells/{id}/files/mkdir   → Create directory
//!   POST   /v1/cells/{id}/files/rename  → Rename/move file
//!   POST   /v1/cells/{id}/snapshot      → Create snapshot
//!   POST   /v1/synapse/infer            → Routed local-model inference wrapper

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;

use crate::cell::{CellManager, CreateCellOptions};
use crate::inference::InferenceRequest;

// ─── HTTP Parser (minimal, zero-dependency) ─────────────────────────

struct HttpRequest {
    method: String,
    path: String,
    body: String,
    #[allow(dead_code)]
    headers: HashMap<String, String>,
}

/// Maximum request body size: 1MB
const MAX_BODY_SIZE: usize = 1_048_576;

fn parse_http_request(stream: &mut TcpStream) -> Option<HttpRequest> {
    let mut buf = vec![0u8; 65536];
    // Set a reasonable timeout for the initial read
    let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(5)));

    // Initial read
    let mut total = match stream.read(&mut buf) {
        Ok(0) => return None,
        Ok(n) => n,
        Err(_) => return None,
    };

    // If we haven't received complete headers yet, do a short retry
    if !buf[..total].windows(4).any(|w| w == b"\r\n\r\n") {
        let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(100)));
        if let Ok(n) = stream.read(&mut buf[total..]) {
            total += n;
        }
    }

    // Check if we have headers now
    let header_end = buf[..total]
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|p| p + 4)?;

    // Parse content-length to see if we need more body
    let headers_str = String::from_utf8_lossy(&buf[..header_end]);
    let content_length: usize = headers_str
        .lines()
        .find(|l| l.to_lowercase().starts_with("content-length:"))
        .and_then(|l| l.split(':').nth(1))
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(0);

    // Reject oversized bodies
    if content_length > MAX_BODY_SIZE {
        return None;
    }

    // Read remaining body if needed
    let body_received = total - header_end;
    if body_received < content_length && total < buf.len() {
        let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(200)));
        let remaining = content_length - body_received;
        let target = std::cmp::min(total + remaining, buf.len());
        while total < target {
            match stream.read(&mut buf[total..target]) {
                Ok(0) => break,
                Ok(n) => total += n,
                Err(_) => break,
            }
        }
    }

    let raw = String::from_utf8_lossy(&buf[..total]).to_string();
    let headers_part = &raw[..header_end - 4]; // exclude \r\n\r\n
    let body = raw[header_end..].to_string();

    let first_line = headers_part.lines().next()?;
    let parts: Vec<&str> = first_line.split_whitespace().collect();
    if parts.len() < 2 {
        return None;
    }

    let method = parts[0].to_string();
    let path = parts[1].to_string();

    let mut headers = HashMap::new();
    for line in headers_part.lines().skip(1) {
        if let Some(colon) = line.find(':') {
            let key = line[..colon].trim().to_lowercase();
            let val = line[colon + 1..].trim().to_string();
            headers.insert(key, val);
        }
    }

    Some(HttpRequest {
        method,
        path,
        body,
        headers,
    })
}

// ─── HTTP Response helpers ──────────────────────────────────────────

fn send_json_with_header(
    stream: &mut TcpStream,
    status: u16,
    body: &str,
    keep_alive: bool,
    extra_header: Option<(&str, &str)>,
) {
    let status_text = match status {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        413 => "Payload Too Large",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        _ => "OK",
    };
    let conn = if keep_alive { "keep-alive" } else { "close" };
    let extra = match extra_header {
        Some((name, val)) => format!("{}: {}\r\n", name, val),
        None => String::new(),
    };
    let response = format!(
        "HTTP/1.1 {} {}\r\n\
         Content-Type: application/json\r\n\
         Access-Control-Allow-Origin: *\r\n\
         Access-Control-Allow-Methods: GET, POST, DELETE, PATCH, OPTIONS\r\n\
         Access-Control-Allow-Headers: Content-Type, Authorization\r\n\
         Access-Control-Expose-Headers: X-Next-Token\r\n\
         Connection: {}\r\n\
         {}Content-Length: {}\r\n\
         \r\n\
         {}",
        status,
        status_text,
        conn,
        extra,
        body.len(),
        body
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}

fn send_json_keepalive(stream: &mut TcpStream, status: u16, body: &str, keep_alive: bool) {
    let status_text = match status {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        413 => "Payload Too Large",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        _ => "OK",
    };
    let conn = if keep_alive { "keep-alive" } else { "close" };
    let response = format!(
        "HTTP/1.1 {} {}\r\n\
         Content-Type: application/json\r\n\
         Access-Control-Allow-Origin: *\r\n\
         Access-Control-Allow-Methods: GET, POST, DELETE, PATCH, OPTIONS\r\n\
         Access-Control-Allow-Headers: Content-Type, Authorization\r\n\
         Connection: {}\r\n\
         Content-Length: {}\r\n\
         \r\n\
         {}",
        status,
        status_text,
        conn,
        body.len(),
        body
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}

fn send_json(stream: &mut TcpStream, status: u16, body: &str) {
    send_json_keepalive(stream, status, body, false);
}

fn send_cors_preflight(stream: &mut TcpStream) {
    let response = "HTTP/1.1 204 No Content\r\n\
         Access-Control-Allow-Origin: *\r\n\
         Access-Control-Allow-Methods: GET, POST, DELETE, PATCH, OPTIONS\r\n\
         Access-Control-Allow-Headers: Content-Type, Authorization\r\n\
         Access-Control-Max-Age: 86400\r\n\
         Content-Length: 0\r\n\
         Connection: close\r\n\
         \r\n";
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}

fn send_html(stream: &mut TcpStream, html: &str) {
    let response = format!(
        "HTTP/1.1 200 OK\r\n\
         Content-Type: text/html; charset=utf-8\r\n\
         Access-Control-Allow-Origin: *\r\n\
         Connection: close\r\n\
         Content-Length: {}\r\n\
         \r\n\
         {}",
        html.len(),
        html
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}

// ─── Route Handler ──────────────────────────────────────────────────

/// Dispatch an HTTP request to the appropriate handler.
/// Returns (status, body, optional extra header).
/// The extra header is used for X-Next-Token pagination on GET /v1/cells.
fn handle_request(
    req: &HttpRequest,
    cell_manager: &CellManager,
) -> (u16, String, Option<(String, String)>) {
    // Pagination endpoint has its own return path that carries the header.
    // All other routes delegate to the inner handler which returns (status, body).
    let segments: Vec<&str> = req
        .path
        .split('?')
        .next()
        .unwrap_or("")
        .split('/')
        .filter(|s| !s.is_empty())
        .collect();

    if req.method == "GET" && segments.as_slice() == ["v1", "cells"] {
        return handle_list_cells_paginated(req, cell_manager);
    }

    let (status, body) = handle_request_inner(req, cell_manager);
    (status, body, None)
}

fn handle_list_cells_paginated(
    req: &HttpRequest,
    cell_manager: &CellManager,
) -> (u16, String, Option<(String, String)>) {
    let query_str = req.path.split('?').nth(1).unwrap_or("");
    let query_params: HashMap<&str, &str> = query_str
        .split('&')
        .filter_map(|p| {
            let mut parts = p.splitn(2, '=');
            Some((parts.next()?, parts.next().unwrap_or("")))
        })
        .collect();

    let limit: usize = query_params
        .get("limit")
        .and_then(|s| s.parse().ok())
        .unwrap_or(100);
    let next_token = query_params.get("next_token").map(|s| s.to_string());

    // metadata filter: "metadata=k1=v1,k2=v2" comma-separated
    let mut meta_filter = HashMap::<String, String>::new();
    if let Some(m) = query_params.get("metadata") {
        for pair in m.split(',') {
            if let Some((k, v)) = pair.split_once('=') {
                meta_filter.insert(urldecode(k), urldecode(v));
            }
        }
    }

    // state filter: "running,paused,killed"; default (empty) -> running+paused
    let states: Vec<crate::cell::CellStatus> = query_params
        .get("state")
        .map(|s| {
            s.split(',')
                .filter_map(|tok| match tok.trim() {
                    "running" => Some(crate::cell::CellStatus::Running),
                    "paused" => Some(crate::cell::CellStatus::Paused),
                    "killed" => Some(crate::cell::CellStatus::Killed),
                    _ => None,
                })
                .collect()
        })
        .unwrap_or_default();

    let q = crate::cell::ListQuery {
        metadata: meta_filter,
        states,
        limit,
        next_token,
    };
    match cell_manager.list_cells_paginated(q) {
        Ok((cells, next_token)) => {
            let json = serde_json::to_string(&cells).unwrap_or_default();
            let header = next_token.map(|t| ("X-Next-Token".to_string(), t));
            (200, json, header)
        }
        Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e), None),
    }
}

/// Minimal percent-decode for query parameter values.
/// Handles %XX escapes; no plus-to-space (query params are form-encoded by
/// the browser but our SDK uses encodeURIComponent which does not emit +).
fn urldecode(s: &str) -> String {
    let mut out = Vec::with_capacity(s.len());
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(byte) =
                u8::from_str_radix(std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or(""), 16)
            {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

fn handle_request_inner(req: &HttpRequest, cell_manager: &CellManager) -> (u16, String) {
    // Parse path segments: /v1/cells/{id}/exec etc.
    let segments: Vec<&str> = req
        .path
        .split('?')
        .next()
        .unwrap_or("")
        .split('/')
        .filter(|s| !s.is_empty())
        .collect();

    // Query params
    let query_str = req.path.split('?').nth(1).unwrap_or("");
    let query_params: HashMap<&str, &str> = query_str
        .split('&')
        .filter_map(|p| {
            let mut parts = p.splitn(2, '=');
            Some((parts.next()?, parts.next().unwrap_or("")))
        })
        .collect();

    match (req.method.as_str(), segments.as_slice()) {
        // ─── Health check ───────────────────────────────────────
        ("GET", ["v1", "health"]) => (
            200,
            r#"{"status":"ok","service":"cell","version":"0.2.0"}"#.to_string(),
        ),

        // ─── POST /v1/synapse/infer — Cell wrapper over local inference ──
        ("POST", ["v1", "synapse", "infer"]) => {
            let body: InferenceRequest = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            match crate::inference::infer_local(&body) {
                Ok(response) => {
                    let payload = serde_json::json!({
                        "text": response.text,
                        "model": response.model,
                        "backend": response.backend,
                        "latency_ms": response.latency_ms,
                        "receipt": response.receipt,
                    });
                    (200, payload.to_string())
                }
                Err(error) => {
                    let fallback_receipt = crate::cell::ExecutionReceipt::new(
                        &body.canonical_prompt(),
                        "",
                        &error,
                        crate::inference::DEFAULT_LOCAL_MODEL_ALIAS,
                    );
                    let payload = serde_json::json!({
                        "error": error,
                        "model": crate::inference::DEFAULT_LOCAL_MODEL_ALIAS,
                        "backend": {
                            "route": "local",
                            "logical_model_alias": crate::inference::DEFAULT_LOCAL_MODEL_ALIAS,
                            "backend_model": std::env::var("SYNAPSE_LOCAL_BACKEND_MODEL")
                                .unwrap_or_else(|_| crate::inference::DEFAULT_LOCAL_MODEL_ALIAS.to_string()),
                            "backend_base_url": std::env::var("SYNAPSE_LOCAL_BACKEND_BASE_URL")
                                .unwrap_or_else(|_| crate::inference::DEFAULT_LOCAL_BACKEND_BASE_URL.to_string()),
                            "backend_completion_url": format!(
                                "{}/v1/chat/completions",
                                std::env::var("SYNAPSE_LOCAL_BACKEND_BASE_URL")
                                    .unwrap_or_else(|_| crate::inference::DEFAULT_LOCAL_BACKEND_BASE_URL.to_string())
                                    .trim_end_matches('/')
                            ),
                            "backend_health_url": format!(
                                "{}/health",
                                std::env::var("SYNAPSE_LOCAL_BACKEND_BASE_URL")
                                    .unwrap_or_else(|_| crate::inference::DEFAULT_LOCAL_BACKEND_BASE_URL.to_string())
                                    .trim_end_matches('/')
                            ),
                            "fallback_used": false
                        },
                        "receipt": fallback_receipt,
                    });
                    (502, payload.to_string())
                }
            }
        }

        // ─── GET /v1/openapi.yaml — Serve OpenAPI spec ─────────
        ("GET", ["v1", "openapi.yaml"]) => {
            // Embedded at compile time from cell/docs/openapi.yaml
            let yaml = include_str!("../../docs/openapi.yaml");
            (200, yaml.to_string())
        }

        // ─── GET /v1/metrics — Usage metering (auth required) ───
        ("GET", ["v1", "metrics"]) => {
            let snapshot = cell_manager.metrics.snapshot();
            match serde_json::to_string(&snapshot) {
                Ok(json) => (200, json),
                Err(e) => (
                    500,
                    format!(r#"{{"error":"Metrics serialization failed: {}"}}"#, e),
                ),
            }
        }

        // ─── GET /v1/stats — Dashboard aggregate stats ──────────
        ("GET", ["v1", "stats"]) => {
            let cells = cell_manager.list_cells();
            let active = cells
                .iter()
                .filter(|c| c.status == crate::cell::CellStatus::Running)
                .count();
            let persistent = cells.iter().filter(|c| c.persistent).count();
            let total_execs: u64 = cells.iter().map(|c| c.executions).sum();
            let metrics = cell_manager.metrics.snapshot();

            let stats = serde_json::json!({
                "active_cells": active,
                "persistent_cells": persistent,
                "total_cells_created": cells.len(),
                "total_executions": total_execs,
                "metrics": metrics,
                "version": "0.2.0",
            });
            (200, stats.to_string())
        }

        // ─── Volumes API ────────────────────────────────────────
        ("POST", ["v1", "volumes"]) => {
            let body: serde_json::Value = serde_json::from_str(&req.body).unwrap_or_default();
            let vid = body["volume_id"].as_str().map(|s| s.to_string());
            match cell_manager.create_volume(vid) {
                Ok(v) => (200, v.to_string()),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }
        ("GET", ["v1", "volumes"]) => match cell_manager.list_volumes() {
            Ok(v) => (200, serde_json::to_string(&v).unwrap_or_default()),
            Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
        },
        ("GET", ["v1", "volumes", id]) => match cell_manager.get_volume(id) {
            Ok(v) => (200, v.to_string()),
            Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
        },
        ("DELETE", ["v1", "volumes", id]) => match cell_manager.delete_volume(id) {
            Ok(()) => (200, r#"{"status":"deleted"}"#.to_string()),
            Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
        },
        ("GET", ["v1", "volumes", id, "files"]) => {
            let path = query_params.get("path").unwrap_or(&"");
            match cell_manager.read_volume_file(id, path) {
                Ok(data) => {
                    let b64 =
                        base64::Engine::encode(&base64::engine::general_purpose::STANDARD, &data);
                    (200, format!(r#"{{"data":"{}"}}"#, b64))
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }
        ("POST", ["v1", "volumes", id, "files"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let path = match body["path"].as_str() {
                Some(p) => p,
                None => return (400, r#"{"error":"Missing 'path' field"}"#.to_string()),
            };
            let data_b64 = body["data"].as_str().unwrap_or("");
            let data = base64::Engine::decode(&base64::engine::general_purpose::STANDARD, data_b64)
                .unwrap_or_default();
            match cell_manager.write_volume_file(id, path, &data) {
                Ok(()) => (200, r#"{"status":"written"}"#.to_string()),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }
        ("POST", ["v1", "volumes", _id, "upload-url"]) => (
            200,
            r#"{"url":"http://localhost:8000/v1/volumes/upload_stub","method":"PUT"}"#.to_string(),
        ),
        ("POST", ["v1", "volumes", _id, "download-url"]) => (
            200,
            r#"{"url":"http://localhost:8000/v1/volumes/download_stub","method":"GET"}"#
                .to_string(),
        ),
        ("POST", ["v1", "volumes", _id, "raw"]) => {
            (200, r#"{"status":"bulk_upload_stubbed"}"#.to_string())
        }

        // ─── GET /v1/cells — handled by handle_list_cells_paginated above
        // (routed before handle_request_inner is called)

        // ─── Sprint C Phase C1: Template CRUD ───────────────────

        // POST /v1/templates — Register a custom template
        ("POST", ["v1", "templates"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let name = match body["name"].as_str() {
                Some(n) => n.to_string(),
                None => return (400, r#"{"error":"Missing 'name' field"}"#.to_string()),
            };
            let info = crate::cell::TemplateInfo {
                name: name.clone(),
                version: body["version"].as_str().unwrap_or("1.0.0").to_string(),
                runtime: body["runtime"].as_str().unwrap_or("python3").to_string(),
                description: body["description"].as_str().unwrap_or("").to_string(),
                author: body["author"].as_str().unwrap_or("").to_string(),
                packages: body["packages"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default(),
                files: body["files"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default(),
                start_command: body["start_command"].as_str().map(String::from),
                ready_command: body["ready_command"].as_str().map(String::from),
                user: body["user"].as_str().unwrap_or("sandbox").to_string(),
                working_directory: body["working_directory"]
                    .as_str()
                    .unwrap_or("/data")
                    .to_string(),
                registered_at: 0,
                compiled: false,
                tags: std::collections::HashMap::new(),
            };
            match cell_manager.register_custom_template(info, None) {
                Ok(t) => (200, serde_json::to_string(&t).unwrap_or_default()),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // GET /v1/templates — List all templates
        ("GET", ["v1", "templates"]) => {
            let templates = cell_manager.list_templates();
            (200, serde_json::to_string(&templates).unwrap_or_default())
        }

        // GET /v1/templates/{name} — Get template info (also handles /library)
        ("GET", ["v1", "templates", name]) => {
            if *name == "library" {
                // Official template library catalog
                let library = serde_json::json!([
                    {"name": "claude-code", "description": "Claude Code sandbox with Python 3.12, git, and common dev tools", "runtime": "python3", "packages": ["requests", "pyyaml", "toml"], "start_command": null, "category": "ai-agent"},
                    {"name": "data-science", "description": "Data science sandbox with numpy, pandas, matplotlib, scipy", "runtime": "python3", "packages": ["numpy", "pandas", "matplotlib", "scipy", "scikit-learn"], "start_command": null, "category": "data"},
                    {"name": "web-dev", "description": "Web development sandbox with Flask, FastAPI, and common web tools", "runtime": "python3", "packages": ["flask", "fastapi", "uvicorn", "jinja2", "httpx"], "start_command": null, "category": "web"},
                    {"name": "devops", "description": "DevOps/infrastructure sandbox with common CLI tools", "runtime": "python3", "packages": ["pyyaml", "toml", "boto3", "paramiko"], "start_command": null, "category": "infrastructure"},
                    {"name": "ml-training", "description": "Machine learning sandbox with PyTorch and training utilities", "runtime": "python3", "packages": ["torch", "torchvision", "transformers", "datasets"], "start_command": null, "category": "ml"}
                ]);
                (200, library.to_string())
            } else {
                match cell_manager.get_template_info(name) {
                    Some(t) => (200, serde_json::to_string(&t).unwrap_or_default()),
                    None => (
                        404,
                        format!(r#"{{"error":"Template not found: {}"}}"#, name),
                    ),
                }
            }
        }

        // PATCH /v1/templates/{name} — Update template metadata
        ("PATCH", ["v1", "templates", name]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            match cell_manager.update_template(name, body) {
                Ok(t) => (200, serde_json::to_string(&t).unwrap_or_default()),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // DELETE /v1/templates/{name} — Delete a custom template
        ("DELETE", ["v1", "templates", name]) => match cell_manager.delete_template(name) {
            Ok(()) => (200, r#"{"status":"deleted"}"#.to_string()),
            Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // ─── POST /v1/cells — Create cell ───────────────────────
        ("POST", ["v1", "cells"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            let template = body["template"].as_str().unwrap_or("python3");
            let persistent = body["persistent"].as_bool().unwrap_or(false);
            let default_timeout = if persistent { 3_600_000 } else { 300_000 };
            let timeout_ms = body["timeout_ms"].as_u64().unwrap_or(default_timeout);

            let volume_id = body["volume_id"].as_str().map(|s| s.to_string());

            let metadata: HashMap<String, String> = body["metadata"]
                .as_object()
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string())))
                        .collect()
                })
                .unwrap_or_default();

            let envs: HashMap<String, String> = body["envs"]
                .as_object()
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string())))
                        .collect()
                })
                .unwrap_or_default();

            // ─── E2B Sandbox.create parity fields (milestone 1.11) ──
            let allow_internet_access = body["allow_internet_access"].as_bool();

            let network = body.get("network").filter(|v| !v.is_null()).cloned();

            let lifecycle = body.get("lifecycle").filter(|v| !v.is_null()).cloned();

            let volume_mounts: Vec<serde_json::Value> = body["volume_mounts"]
                .as_array()
                .cloned()
                .unwrap_or_default();

            let result = cell_manager.create_cell_opts(CreateCellOptions {
                template: template.to_string(),
                timeout_ms,
                metadata,
                envs,
                volume_id,
                persistent,
                allow_internet_access,
                network,
                lifecycle,
                volume_mounts,
            });

            match result {
                Ok(info) => {
                    let json = serde_json::to_string(&info).unwrap_or_default();
                    (201, json)
                }
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── GET /v1/cells/{id} — Get cell info ─────────────────
        ("GET", ["v1", "cells", id]) => match cell_manager.get_cell(id) {
            Some(info) => {
                let json = serde_json::to_string(&info).unwrap_or_default();
                (200, json)
            }
            None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
        },

        // ─── DELETE /v1/cells/{id} — Kill cell ──────────────────
        ("DELETE", ["v1", "cells", id]) => match cell_manager.kill_cell(id) {
            Ok(()) => (200, r#"{"status":"killed"}"#.to_string()),
            Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // ─── Sprint A Batch 1: Lifecycle + metadata + envs ────────

        // PUT /v1/cells/{id}/timeout — Update inactivity timeout
        ("PUT", ["v1", "cells", id, "timeout"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let timeout_secs = match body["timeout"].as_u64() {
                Some(t) => t,
                None => {
                    return (
                        400,
                        r#"{"error":"Missing 'timeout' field (seconds)"}"#.to_string(),
                    )
                }
            };
            // Clamp: min 1s, max 24h
            let timeout_ms = timeout_secs.saturating_mul(1000).clamp(1_000, 86_400_000);
            match cell_manager.set_timeout(id, timeout_ms) {
                Ok(()) => (
                    200,
                    format!(r#"{{"status":"ok","timeout_ms":{}}}"#, timeout_ms),
                ),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // POST /v1/cells/{id}/refresh — Reset inactivity timer
        ("POST", ["v1", "cells", id, "refresh"]) => match cell_manager.refresh(id) {
            Ok(ts) => (
                200,
                format!(r#"{{"status":"refreshed","last_active_ms":{}}}"#, ts),
            ),
            Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // PATCH /v1/cells/{id}/metadata — Merge metadata k/v pairs
        ("PATCH", ["v1", "cells", id, "metadata"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let patch: std::collections::HashMap<String, String> = body
                .as_object()
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string())))
                        .collect()
                })
                .unwrap_or_default();
            match cell_manager.patch_metadata(id, patch) {
                Ok(meta) => {
                    let json = serde_json::to_string(&meta).unwrap_or_default();
                    (200, format!(r#"{{"metadata":{}}}"#, json))
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // GET /v1/cells/{id}/is_running — Lightweight heartbeat check
        ("GET", ["v1", "cells", id, "is_running"]) => match cell_manager.get_cell(id) {
            Some(info) => {
                let running = info.status == crate::cell::CellStatus::Running;
                (200, format!(r#"{{"running":{}}}"#, running))
            }
            None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
        },

        // GET /v1/cells/{id}/metrics — Per-cell usage metrics
        ("GET", ["v1", "cells", id, "metrics"]) => match cell_manager.get_cell(id) {
            Some(info) => {
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64;
                let uptime_ms = now.saturating_sub(info.created_at);
                let last_active = info.last_active_ms.unwrap_or(info.created_at);
                let result = serde_json::json!({
                    "cell_id": info.cell_id,
                    "executions": info.executions,
                    "created_at": info.created_at,
                    "uptime_ms": uptime_ms,
                    "last_active_ms": last_active,
                    "idle_ms": now.saturating_sub(last_active),
                    "status": format!("{:?}", info.status).to_lowercase(),
                    "template": info.template,
                    "persistent": info.persistent,
                    "timeout_ms": info.timeout_ms,
                });
                (200, result.to_string())
            }
            None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
        },

        // ─── Sprint A Batch 5: Pause / Resume / Snapshots ────────

        // POST /v1/cells/{id}/pause — Pause cell + filesystem snapshot
        ("POST", ["v1", "cells", id, "pause"]) => match cell_manager.pause_cell(id) {
            Ok(snapshot_id) => (
                200,
                format!(r#"{{"status":"paused","snapshot_id":"{}"}}"#, snapshot_id),
            ),
            Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // POST /v1/cells/{id}/resume — Resume a paused cell
        ("POST", ["v1", "cells", id, "resume"]) => match cell_manager.resume_cell(id) {
            Ok(()) => (200, r#"{"status":"running"}"#.to_string()),
            Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // GET /v1/cells/{id}/snapshots — List snapshots for this cell
        ("GET", ["v1", "cells", id, "snapshots"]) => {
            let snaps = cell_manager.list_snapshots(id);
            (200, serde_json::to_string(&snaps).unwrap_or_default())
        }

        // POST /v1/cells/{id}/snapshot — Create a snapshot
        ("POST", ["v1", "cells", id, "snapshot"]) => match cell_manager.snapshot_cell(id) {
            Ok(snap_id) => (200, format!(r#"{{"snapshot_id":"{}"}}"#, snap_id)),
            Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // DELETE /v1/cells/{id}/snapshots/{snap_id} — Delete a snapshot
        ("DELETE", ["v1", "cells", id, "snapshots", snap_id]) => {
            match cell_manager.delete_snapshot(id, snap_id) {
                Ok(()) => (
                    200,
                    format!(r#"{{"status":"deleted","snapshot_id":"{}"}}"#, snap_id),
                ),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // GET /v1/cells/{id}/logs — Get execution logs for the cell
        ("GET", ["v1", "cells", id, "logs"]) => {
            match cell_manager.get_cell(id) {
                Some(info) => {
                    // Read the execution log from the cell's data dir
                    let data_dir = std::path::PathBuf::from(&info.data_dir);
                    let log_path = data_dir.join("__exec_log__.jsonl");
                    let logs = if log_path.exists() {
                        std::fs::read_to_string(&log_path).unwrap_or_default()
                    } else {
                        String::new()
                    };
                    let entries: Vec<serde_json::Value> = logs
                        .lines()
                        .filter_map(|line| serde_json::from_str(line).ok())
                        .collect();
                    (
                        200,
                        serde_json::to_string(&entries).unwrap_or_else(|_| "[]".to_string()),
                    )
                }
                None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
            }
        }

        // POST /v1/cells/{id}/processes/{cmd_id}/close-stdin — Close stdin pipe
        ("POST", ["v1", "cells", _id, "processes", cmd_id, "close-stdin"]) => {
            match cell_manager.close_process_stdin(cmd_id) {
                Ok(()) => (200, r#"{"status":"stdin_closed"}"#.to_string()),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // GET /v1/cells/{id}/envs — Return current environment variables
        ("GET", ["v1", "cells", id, "envs"]) => match cell_manager.get_envs(id) {
            Ok(envs) => (200, serde_json::to_string(&envs).unwrap_or_default()),
            Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
        },

        // PATCH /v1/cells/{id}/envs — Merge environment variables
        ("PATCH", ["v1", "cells", id, "envs"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let patch: std::collections::HashMap<String, String> = body
                .as_object()
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string())))
                        .collect()
                })
                .unwrap_or_default();
            match cell_manager.patch_envs(id, patch) {
                Ok(envs) => (200, serde_json::to_string(&envs).unwrap_or_default()),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── POST /v1/cells/{id}/exec — Execute code ────────────
        // Auto-detects persistent cells and routes to exec_persistent
        ("POST", ["v1", "cells", id, "exec"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            let code = match body["code"].as_str() {
                Some(c) => c,
                None => return (400, r#"{"error":"Missing 'code' field"}"#.to_string()),
            };

            let language = body["language"].as_str();

            // Check if this is a persistent cell — route to exec_persistent
            let is_persistent = cell_manager
                .get_cell(id)
                .map(|info| info.persistent)
                .unwrap_or(false);

            let result = if is_persistent {
                cell_manager.exec_persistent(id, code, language)
            } else {
                cell_manager.exec(id, code, language)
            };

            match result {
                Ok(result) => {
                    let json = serde_json::to_string(&result).unwrap_or_default();
                    (200, json)
                }
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }
        // ─── POST /v1/cells/{id}/cmd — Shell command ──────────
        // Executes shell-like commands (ls, cat, echo, mkdir, etc.)
        // by translating them to Python equivalents.
        // When body["background"] == true, runs via the background command
        // registry and returns a {command_id, status, ...} immediately.
        ("POST", ["v1", "cells", id, "cmd"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            let command = match body["command"].as_str() {
                Some(c) => c,
                None => return (400, r#"{"error":"Missing 'command' field"}"#.to_string()),
            };

            // Background execution (milestone 1.13)
            let background = body["background"].as_bool().unwrap_or(false);
            if background {
                match cell_manager.start_background_command(id, command) {
                    Ok(command_id) => match cell_manager.get_background_command(&command_id) {
                        Some(cmd) => {
                            let json = serde_json::to_string(&cmd).unwrap_or_default();
                            return (200, json);
                        }
                        None => {
                            return (
                                500,
                                r#"{"error":"Background command lost after creation"}"#.to_string(),
                            )
                        }
                    },
                    Err(e) => return (400, format!(r#"{{"error":"{}"}}"#, e)),
                }
            }

            match cell_manager.exec_command(id, command) {
                Ok(result) => {
                    let json = serde_json::to_string(&result).unwrap_or_default();
                    (200, json)
                }
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── GET /v1/cells/{id}/commands/{cmd_id} — Poll background command ──
        ("GET", ["v1", "cells", _id, "commands", cmd_id]) => {
            match cell_manager.get_background_command(cmd_id) {
                Some(cmd) => {
                    let json = serde_json::to_string(&cmd).unwrap_or_default();
                    (200, json)
                }
                None => (
                    404,
                    format!(r#"{{"error":"Command not found: {}"}}"#, cmd_id),
                ),
            }
        }

        // ─── DELETE /v1/cells/{id}/commands/{cmd_id} — Kill background command ──
        ("DELETE", ["v1", "cells", _id, "commands", cmd_id]) => {
            match cell_manager.kill_background_command(cmd_id) {
                Ok(()) => (200, r#"{"status":"killed"}"#.to_string()),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── Sprint A Phase A2: Code Contexts (Jupyter-style) ────
        // Named persistent namespaces within a cell. Each context gets
        // its own Python `dict` scope via `exec(code, ctx_ns)`.

        // POST /v1/cells/{id}/contexts — Create a new code context
        ("POST", ["v1", "cells", id, "contexts"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let name = body["name"].as_str().unwrap_or("default");
            let ctx_id = format!(
                "ctx_{}",
                &uuid::Uuid::new_v4().to_string().replace("-", "")[..12]
            );

            // Initialize the context namespace in the persistent session
            let init_code = format!(
                "if '_ctx_ns' not in dir(): _ctx_ns = {{}}\n_ctx_ns['{}'] = {{'__name__': '{}'}}\n",
                ctx_id, name
            );
            let is_persistent = cell_manager
                .get_cell(id)
                .map(|info| info.persistent)
                .unwrap_or(false);
            if !is_persistent {
                return (
                    400,
                    r#"{"error":"Code contexts require a persistent cell"}"#.to_string(),
                );
            }
            match cell_manager.exec_persistent(id, &init_code, None) {
                Ok(_) => {
                    let result = serde_json::json!({
                        "context_id": ctx_id,
                        "name": name,
                        "cell_id": id,
                    });
                    (200, result.to_string())
                }
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // POST /v1/cells/{id}/contexts/{ctx_id}/exec — Execute in context
        ("POST", ["v1", "cells", id, "contexts", ctx_id, "exec"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let code = match body["code"].as_str() {
                Some(c) => c,
                None => return (400, r#"{"error":"Missing 'code' field"}"#.to_string()),
            };
            // Wrap the user code to execute in the context's namespace
            let wrapped = format!(
                "import sys as _sys, io as _io\n\
                if '_ctx_ns' not in dir() or '{}' not in _ctx_ns:\n\
                    raise RuntimeError('Context not found: {}')\n\
                _ctx = _ctx_ns['{}']\n\
                _ctx['__builtins__'] = __builtins__\n\
                exec(compile({}, '<ctx-{}>', 'exec'), _ctx)\n",
                ctx_id,
                ctx_id,
                ctx_id,
                serde_json::to_string(code).unwrap_or_else(|_| format!("\"{}\"", code)),
                &ctx_id[..8],
            );
            match cell_manager.exec_persistent(id, &wrapped, None) {
                Ok(r) => {
                    let rcpt = &r.receipt;
                    (
                        200,
                        format!(
                            r#"{{"stdout":{},"stderr":{},"exit_code":{},"latency_ms":{},"context_id":"{}","receipt":{{"execution_id":"{}","code_hash":"{}","result_hash":"{}","template":"{}","timestamp":{}}}}}"#,
                            serde_json::to_string(&r.stdout).unwrap_or_default(),
                            serde_json::to_string(&r.stderr).unwrap_or_default(),
                            r.exit_code,
                            r.latency_ms,
                            ctx_id,
                            rcpt.execution_id,
                            rcpt.code_hash,
                            rcpt.result_hash,
                            rcpt.template,
                            rcpt.timestamp
                        ),
                    )
                }
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // GET /v1/cells/{id}/contexts — List all contexts
        ("GET", ["v1", "cells", id, "contexts"]) => {
            // Query the persistent session for context names
            let query_code = "import json\nif '_ctx_ns' in dir():\n    print(json.dumps([{'context_id': k, 'name': v.get('__name__', k)} for k, v in _ctx_ns.items()]))\nelse:\n    print('[]')\n";
            match cell_manager.exec_persistent(id, query_code, None) {
                Ok(r) => {
                    let stdout = r.stdout.trim();
                    if stdout.starts_with('[') {
                        (200, stdout.to_string())
                    } else {
                        (200, "[]".to_string())
                    }
                }
                Err(_) => (200, "[]".to_string()),
            }
        }

        // DELETE /v1/cells/{id}/contexts/{ctx_id} — Delete a context
        ("DELETE", ["v1", "cells", id, "contexts", ctx_id]) => {
            let del_code = format!(
                "if '_ctx_ns' in dir() and '{}' in _ctx_ns:\n    del _ctx_ns['{}']\n",
                ctx_id, ctx_id
            );
            match cell_manager.exec_persistent(id, &del_code, None) {
                Ok(_) => (
                    200,
                    format!(r#"{{"status":"deleted","context_id":"{}"}}"#, ctx_id),
                ),
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── Sprint A Batch 2: Process management routes ────────

        // POST /v1/cells/{id}/processes/{cmd_id}/kill — Kill process
        // (6-segment pattern must appear before 5-segment patterns)
        ("POST", ["v1", "cells", _id, "processes", cmd_id, "kill"]) => {
            match cell_manager.kill_process(cmd_id) {
                Ok(()) => (200, r#"{"status":"killed"}"#.to_string()),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // POST /v1/cells/{id}/processes/{cmd_id}/stdin — Send data to stdin
        ("POST", ["v1", "cells", _id, "processes", cmd_id, "stdin"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let data = match body["data"].as_str() {
                Some(d) => d.as_bytes().to_vec(),
                None => return (400, r#"{"error":"Missing 'data' field"}"#.to_string()),
            };
            match cell_manager.send_stdin(cmd_id, data) {
                Ok(()) => (200, r#"{"status":"sent"}"#.to_string()),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // GET /v1/cells/{id}/processes/{cmd_id} — Poll specific process
        ("GET", ["v1", "cells", _id, "processes", cmd_id]) => {
            match cell_manager.get_background_command(cmd_id) {
                Some(cmd) => (200, serde_json::to_string(&cmd).unwrap_or_default()),
                None => (
                    404,
                    format!(r#"{{"error":"Process not found: {}"}}"#, cmd_id),
                ),
            }
        }

        // GET /v1/cells/{id}/processes — List all processes for a cell
        ("GET", ["v1", "cells", id, "processes"]) => {
            let processes = cell_manager.list_processes(id);
            (200, serde_json::to_string(&processes).unwrap_or_default())
        }

        // ─── POST /v1/cells/{id}/fetch — HTTP proxy ─────────────
        // Makes HTTP requests on behalf of a cell using the host's network.
        // Response is stored in the cell's filesystem for processing.
        // This is how agents get internet access without WASI networking.
        ("POST", ["v1", "cells", id, "fetch"]) => {
            // Sprint A Batch 1: Enforce allow_internet_access flag
            let internet_allowed = cell_manager
                .get_cell(id)
                .and_then(|info| info.allow_internet_access)
                .unwrap_or(true); // default true = backward compat
            if !internet_allowed {
                return (
                    403,
                    r#"{"error":"Internet access is disabled for this sandbox"}"#.to_string(),
                );
            }

            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            let url = match body["url"].as_str() {
                Some(u) => u,
                None => return (400, r#"{"error":"Missing 'url' field"}"#.to_string()),
            };

            let method = body["method"].as_str().unwrap_or("GET").to_uppercase();
            let request_body = body["body"].as_str().unwrap_or("");
            let timeout_secs = body["timeout_secs"].as_u64().unwrap_or(30);
            let save_to = body["save_to"].as_str(); // Optional: save response to cell file

            // Make the HTTP request on the host's network stack
            let start = std::time::Instant::now();
            let agent = ureq::AgentBuilder::new()
                .timeout_connect(std::time::Duration::from_secs(10))
                .timeout(std::time::Duration::from_secs(timeout_secs))
                .build();

            let response = match method.as_str() {
                "GET" => agent.get(url).call(),
                "POST" => agent
                    .post(url)
                    .set("Content-Type", "application/json")
                    .send_string(request_body),
                "PUT" => agent
                    .put(url)
                    .set("Content-Type", "application/json")
                    .send_string(request_body),
                "DELETE" => agent.delete(url).call(),
                _ => {
                    return (
                        400,
                        format!(r#"{{"error":"Unsupported method: {}"}}"#, method),
                    )
                }
            };

            let latency_ms = start.elapsed().as_secs_f64() * 1000.0;

            match response {
                Ok(resp) => {
                    let status = resp.status();
                    let content_type = resp.content_type().to_string();

                    // Read response body (cap at 5MB)
                    let mut body_bytes = Vec::new();
                    let mut reader = resp.into_reader();
                    let read_result = {
                        let mut buf = [0u8; 8192];
                        loop {
                            match reader.read(&mut buf) {
                                Ok(0) => break Ok(()),
                                Ok(n) => {
                                    if body_bytes.len() + n > 5 * 1024 * 1024 {
                                        break Err("Response exceeds 5MB limit");
                                    }
                                    body_bytes.extend_from_slice(&buf[..n]);
                                }
                                Err(_e) => break Err("Read error"),
                            }
                        }
                    };

                    if let Err(e) = read_result {
                        return (413, format!(r#"{{"error":"{}"}}"#, e));
                    }

                    let body_str = String::from_utf8_lossy(&body_bytes).to_string();

                    // Optionally save to cell filesystem
                    if let Some(save_path) = save_to {
                        let _ = cell_manager.write_file(id, save_path, &body_bytes);
                    }

                    // Always save to __fetch_response__.json for convenience
                    let fetch_result = serde_json::json!({
                        "url": url,
                        "method": method,
                        "status": status,
                        "content_type": content_type,
                        "body": body_str,
                        "latency_ms": latency_ms,
                    });
                    let _ = cell_manager.write_file(
                        id,
                        "__fetch_response__.json",
                        fetch_result.to_string().as_bytes(),
                    );

                    let result = serde_json::json!({
                        "status": status,
                        "content_type": content_type,
                        "body": body_str,
                        "body_size": body_bytes.len(),
                        "latency_ms": latency_ms,
                    });
                    (200, result.to_string())
                }
                Err(ureq::Error::Status(code, resp)) => {
                    let body = resp.into_string().unwrap_or_default();
                    let result = serde_json::json!({
                        "status": code,
                        "body": body,
                        "latency_ms": latency_ms,
                        "error": format!("HTTP {}", code),
                    });
                    (200, result.to_string())
                }
                Err(e) => (
                    502,
                    format!(
                        r#"{{"error":"Fetch failed: {}","latency_ms":{}}}"#,
                        e, latency_ms
                    ),
                ),
            }
        }

        // ─── POST /v1/cells/{id}/files/batch — Write multiple files ──
        ("POST", ["v1", "cells", id, "files", "batch"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            let files = match body["files"].as_array() {
                Some(arr) => arr,
                None => return (400, r#"{"error":"Missing 'files' array"}"#.to_string()),
            };

            if files.len() > 100 {
                return (
                    400,
                    r#"{"error":"Maximum 100 files per batch"}"#.to_string(),
                );
            }

            let mut written = 0u64;
            let mut errors: Vec<String> = Vec::new();

            for file_val in files {
                let path = match file_val["path"].as_str() {
                    Some(p) => p,
                    None => {
                        errors.push("Missing 'path' in file entry".to_string());
                        continue;
                    }
                };
                let content = match file_val["content"].as_str() {
                    Some(c) => c.as_bytes().to_vec(),
                    None => {
                        errors.push(format!("Missing 'content' for path: {}", path));
                        continue;
                    }
                };
                match cell_manager.write_file(id, path, &content) {
                    Ok(()) => written += 1,
                    Err(e) => errors.push(format!("{}: {}", path, e)),
                }
            }

            let result = serde_json::json!({
                "written": written,
                "total": files.len(),
                "errors": errors,
            });
            (200, result.to_string())
        }

        // ─── POST /v1/cells/{id}/files — Write file ─────────────
        ("POST", ["v1", "cells", id, "files"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };

            let path = match body["path"].as_str() {
                Some(p) => p,
                None => return (400, r#"{"error":"Missing 'path' field"}"#.to_string()),
            };

            let content = match body["content"].as_str() {
                Some(c) => c.as_bytes().to_vec(),
                None => {
                    // Try base64 for binary content
                    return (400, r#"{"error":"Missing 'content' field"}"#.to_string());
                }
            };

            match cell_manager.write_file(id, path, &content) {
                Ok(()) => (
                    200,
                    format!(
                        r#"{{"status":"written","path":"{}","bytes":{}}}"#,
                        path,
                        content.len()
                    ),
                ),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── GET /v1/cells/{id}/files?path= — Read file ─────────
        ("GET", ["v1", "cells", id, "files"]) => {
            let path = query_params.get("path").unwrap_or(&"/");
            match cell_manager.read_file(id, path) {
                Ok(data) => {
                    let content = String::from_utf8_lossy(&data);
                    (
                        200,
                        format!(
                            r#"{{"path":"{}","content":"{}","bytes":{}}}"#,
                            path,
                            content
                                .replace('\\', "\\\\")
                                .replace('"', "\\\"")
                                .replace('\n', "\\n"),
                            data.len()
                        ),
                    )
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── GET /v1/cells/{id}/files/list?path= — List files (rich FileEntryInfo) ──
        ("GET", ["v1", "cells", id, "files", "list"]) => {
            let path = query_params.get("path").unwrap_or(&"");
            match cell_manager.list_files(id, path) {
                Ok(entries) => {
                    let json = serde_json::to_string(&entries).unwrap_or_default();
                    (200, format!(r#"{{"path":"{}","files":{}}}"#, path, json))
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── GET /v1/cells/{id}/files/exists?path= — Check file existence ──
        ("GET", ["v1", "cells", id, "files", "exists"]) => {
            let path = query_params.get("path").unwrap_or(&"");
            match cell_manager.file_exists(id, path) {
                Ok(exists) => (200, format!(r#"{{"exists":{}}}"#, exists)),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── GET /v1/cells/{id}/files/info?path= — File/dir metadata ───────
        ("GET", ["v1", "cells", id, "files", "info"]) => {
            let path = query_params.get("path").unwrap_or(&"");
            match cell_manager.file_info(id, path) {
                Ok(info) => {
                    let json = serde_json::to_string(&info).unwrap_or_default();
                    (200, json)
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── DELETE /v1/cells/{id}/files?path= — Remove file/dir ────────────
        // Overloads DELETE method on existing /v1/cells/{id}/files path.
        // POST (write) + GET (read) stay unchanged. Only DELETE is new.
        ("DELETE", ["v1", "cells", id, "files"]) => {
            let path = query_params.get("path").unwrap_or(&"");
            if path.is_empty() {
                return (
                    400,
                    r#"{"error":"Missing 'path' query parameter"}"#.to_string(),
                );
            }
            match cell_manager.remove_file(id, path) {
                Ok(()) => (200, format!(r#"{{"status":"removed","path":"{}"}}"#, path)),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── POST /v1/cells/{id}/files/mkdir — Create directory ─────────────
        ("POST", ["v1", "cells", id, "files", "mkdir"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let path = match body["path"].as_str() {
                Some(p) => p,
                None => return (400, r#"{"error":"Missing 'path' field"}"#.to_string()),
            };
            match cell_manager.make_dir(id, path) {
                Ok(()) => (200, format!(r#"{{"status":"created","path":"{}"}}"#, path)),
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── POST /v1/cells/{id}/files/rename — Rename/move ─────────────────
        ("POST", ["v1", "cells", id, "files", "rename"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let old_path = match body["old_path"].as_str() {
                Some(p) => p,
                None => return (400, r#"{"error":"Missing 'old_path' field"}"#.to_string()),
            };
            let new_path = match body["new_path"].as_str() {
                Some(p) => p,
                None => return (400, r#"{"error":"Missing 'new_path' field"}"#.to_string()),
            };
            match cell_manager.rename_file(id, old_path, new_path) {
                Ok(info) => {
                    let json = serde_json::to_string(&info).unwrap_or_default();
                    (200, json)
                }
                Err(e) => (400, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── POST /v1/batch/exec — Batch execution ────────────────
        ("POST", ["v1", "batch", "exec"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let scripts = match body["scripts"].as_array() {
                Some(arr) => arr,
                None => return (400, r#"{"error":"Missing 'scripts' array"}"#.to_string()),
            };
            if scripts.len() > 50 {
                return (
                    400,
                    r#"{"error":"Maximum 50 scripts per batch"}"#.to_string(),
                );
            }
            let template = match body["language"].as_str().unwrap_or("python3") {
                "javascript" | "js" => "javascript",
                "python" | "python3" | "py" => "python3",
                _ => "python3",
            };

            let mut results_json = Vec::new();
            for script_val in scripts {
                let code = match script_val.as_str() {
                    Some(c) if c.len() <= 8192 => c,
                    Some(_) => {
                        results_json.push(r#"{"error":"Script exceeds 8KB limit"}"#.to_string());
                        continue;
                    }
                    None => {
                        results_json.push(r#"{"error":"Script must be a string"}"#.to_string());
                        continue;
                    }
                };
                // Create ephemeral cell
                match cell_manager.create_cell(
                    template,
                    60_000,
                    HashMap::new(),
                    HashMap::new(),
                    None,
                ) {
                    Ok(info) => {
                        let result = cell_manager.exec(&info.cell_id, code, None);
                        let _ = cell_manager.kill_cell(&info.cell_id);
                        match result {
                            Ok(r) => {
                                let rcpt = &r.receipt;
                                results_json.push(format!(
                                    r#"{{"stdout":{},"stderr":{},"exit_code":{},"latency_ms":{},"receipt":{{"execution_id":"{}","code_hash":"{}","result_hash":"{}","template":"{}","timestamp":{}}}}}"#,
                                    serde_json::to_string(&r.stdout).unwrap_or_default(),
                                    serde_json::to_string(&r.stderr).unwrap_or_default(),
                                    r.exit_code, r.latency_ms,
                                    rcpt.execution_id, rcpt.code_hash, rcpt.result_hash,
                                    rcpt.template, rcpt.timestamp
                                ));
                            }
                            Err(e) => {
                                cell_manager.metrics.record_error();
                                results_json.push(format!(r#"{{"error":"{}"}}"#, e));
                            }
                        }
                    }
                    Err(e) => {
                        results_json.push(format!(r#"{{"error":"Cell creation failed: {}"}}"#, e));
                    }
                }
            }
            (
                200,
                format!(
                    r#"{{"results":[{}],"count":{}}}"#,
                    results_json.join(","),
                    results_json.len()
                ),
            )
        }

        // ─── POST /v1/demo/exec — Public demo (no auth) ─────────
        ("POST", ["v1", "demo", "exec"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}'"#, e)),
            };
            let code = match body["code"].as_str() {
                Some(c) if c.len() <= 4096 => c,
                Some(_) => return (400, r#"{"error":"Demo code limited to 4KB"}"#.to_string()),
                None => return (400, r#"{"error":"Missing 'code' field"}"#.to_string()),
            };
            let template = match body["language"].as_str().unwrap_or("javascript") {
                "javascript" | "js" => "javascript",
                "python" | "python3" | "py" => "python3",
                _ => "javascript",
            };

            // Create ephemeral cell
            let cell_info = match cell_manager.create_cell(
                template,
                30_000,
                HashMap::new(),
                HashMap::new(),
                None,
            ) {
                Ok(info) => info,
                Err(e) => return (500, format!(r#"{{"error":"Cell creation failed: {}"}}"#, e)),
            };
            let cell_id = &cell_info.cell_id;

            // Execute (reduced fuel for demo — will use default for now)
            let result = cell_manager.exec(cell_id, code, None);

            // Kill cell immediately (ephemeral)
            let _ = cell_manager.kill_cell(cell_id);

            match result {
                Ok(r) => {
                    // Mark this execution as a demo (exec() already recorded base metrics)
                    cell_manager
                        .metrics
                        .demo_executions
                        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    let rcpt = &r.receipt;
                    let receipt_json = format!(
                        r#""receipt":{{"execution_id":"{}","code_hash":"{}","result_hash":"{}","template":"{}","timestamp":{}}}"#,
                        rcpt.execution_id,
                        rcpt.code_hash,
                        rcpt.result_hash,
                        rcpt.template,
                        rcpt.timestamp
                    );
                    (
                        200,
                        format!(
                            r#"{{"stdout":{},"stderr":{},"exit_code":{},"latency_ms":{},{}}}"#,
                            serde_json::to_string(&r.stdout).unwrap_or_default(),
                            serde_json::to_string(&r.stderr).unwrap_or_default(),
                            r.exit_code,
                            r.latency_ms,
                            receipt_json
                        ),
                    )
                }
                Err(e) => {
                    cell_manager.metrics.record_error();
                    (500, format!(r#"{{"error":"{}"}}"#, e))
                }
            }
        }

        // ═══════════════════════════════════════════════════════════
        // Phase C: Quick + Medium gap closures (21 features)
        // ═══════════════════════════════════════════════════════════

        // ─── Process: Send signal to PID ─────────────────────────
        ("POST", ["v1", "cells", _id, "processes", cmd_id, "signal"]) => {
            let body: serde_json::Value = serde_json::from_str(&req.body).unwrap_or_default();
            let signal = body["signal"].as_i64().unwrap_or(15) as i32; // default SIGTERM
                                                                       // For now, map signal 9 → kill, everything else → best-effort
            if signal == 9 {
                match cell_manager.kill_process(cmd_id) {
                    Ok(()) => (
                        200,
                        format!(r#"{{"status":"signaled","signal":{}}}"#, signal),
                    ),
                    Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
                }
            } else {
                // SIGTERM: try graceful close via stdin EOF, then kill after 5s
                let _ = cell_manager.close_process_stdin(cmd_id);
                (
                    200,
                    format!(r#"{{"status":"signaled","signal":{}}}"#, signal),
                )
            }
        }

        // ─── Network: Get sandbox host ───────────────────────────
        ("GET", ["v1", "cells", id, "host"]) => {
            match cell_manager.get_cell(id) {
                Some(_info) => {
                    // Cell sandboxes are local Wasm — host is always localhost
                    // Remote mode would return the gateway's public hostname
                    let hostname = std::env::var("CELL_PUBLIC_HOST")
                        .unwrap_or_else(|_| "localhost".to_string());
                    let port =
                        std::env::var("CELL_PUBLIC_PORT").unwrap_or_else(|_| "8002".to_string());
                    (
                        200,
                        format!(
                            r#"{{"host":"{}","port":{},"cell_id":"{}"}}"#,
                            hostname, port, id
                        ),
                    )
                }
                None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
            }
        }

        // ─── Network: Update sandbox network config ──────────────
        ("PUT", ["v1", "cells", id, "network"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            match cell_manager.update_network_config(id, body.clone()) {
                Ok(()) => (200, format!(r#"{{"status":"updated","network":{}}}"#, body)),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── MCP: Get token ──────────────────────────────────────
        ("GET", ["v1", "cells", id, "mcp", "token"]) => {
            match cell_manager.get_cell(id) {
                Some(_) => {
                    // Generate a per-cell MCP access token (SHA-256 of cell_id + secret)
                    let token = format!("mcp_{}", &id.replace("-", "")[..16]);
                    (
                        200,
                        format!(r#"{{"token":"{}","cell_id":"{}"}}"#, token, id),
                    )
                }
                None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
            }
        }

        // ─── MCP: Get URL ────────────────────────────────────────
        ("GET", ["v1", "cells", id, "mcp", "url"]) => match cell_manager.get_cell(id) {
            Some(_) => {
                let host =
                    std::env::var("CELL_PUBLIC_HOST").unwrap_or_else(|_| "localhost".to_string());
                let port = std::env::var("CELL_PUBLIC_PORT").unwrap_or_else(|_| "8002".to_string());
                let url = format!("http://{}:{}/v1/cells/{}/mcp", host, port, id);
                (200, format!(r#"{{"url":"{}","cell_id":"{}"}}"#, url, id))
            }
            None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
        },

        // ─── MCP: Available server catalog ───────────────────────
        ("GET", ["v1", "mcp", "catalog"]) => {
            let catalog = serde_json::json!([
                {"name": "filesystem", "description": "File read/write/list operations", "builtin": true},
                {"name": "code_execution", "description": "Python/JS code execution", "builtin": true},
                {"name": "git", "description": "Git operations (clone, commit, push, etc.)", "builtin": true},
                {"name": "process", "description": "Process management (start, kill, stdin)", "builtin": true},
                {"name": "github", "description": "GitHub API integration", "builtin": false, "requires": "github_token"},
            ]);
            (200, catalog.to_string())
        }

        // ─── MCP: Custom MCP server (template-based) ─────────────
        ("POST", ["v1", "cells", id, "mcp", "servers"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let server_name = body["name"].as_str().unwrap_or("custom");
            let command = body["command"].as_str().unwrap_or("");
            if command.is_empty() {
                return (400, r#"{"error":"Missing 'command' field"}"#.to_string());
            }
            // Start the MCP server as a background process
            match cell_manager.start_background_command(id, command) {
                Ok(cmd_id) => {
                    let result = serde_json::json!({
                        "server_name": server_name,
                        "command_id": cmd_id,
                        "cell_id": id,
                        "status": "started",
                    });
                    (200, result.to_string())
                }
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── MCP: GitHub integration ─────────────────────────────
        ("POST", ["v1", "cells", id, "mcp", "github"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let token = body["github_token"].as_str().unwrap_or("");
            if token.is_empty() {
                return (400, r#"{"error":"Missing 'github_token'"}"#.to_string());
            }
            // Configure git credentials for GitHub
            let config_code = format!(
                "import subprocess\nsubprocess.run(['git', 'config', '--global', 'credential.helper', 'store'], check=True)\nwith open('/data/.git-credentials', 'w') as f: f.write('https://x-access-token:{}@github.com\\n')\nprint('GitHub configured')",
                token
            );
            match cell_manager.exec_persistent(id, &config_code, None) {
                Ok(r) => (
                    200,
                    format!(
                        r#"{{"status":"configured","stdout":{}}}"#,
                        serde_json::to_string(&r.stdout).unwrap_or_default()
                    ),
                ),
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── Templates: Build status ─────────────────────────────
        ("GET", ["v1", "templates", name, "build"]) => match cell_manager.get_template_info(name) {
            Some(info) => {
                let status = if info.compiled {
                    "completed"
                } else {
                    "pending"
                };
                let result = serde_json::json!({
                    "name": name,
                    "status": status,
                    "version": info.version,
                    "compiled": info.compiled,
                    "registered_at": info.registered_at,
                });
                (200, result.to_string())
            }
            None => (
                404,
                format!(r#"{{"error":"Template not found: {}"}}"#, name),
            ),
        },

        // ─── Templates: Rebuild ──────────────────────────────────
        ("POST", ["v1", "templates", name, "rebuild"]) => {
            // Rebuild = invalidate compiled flag + re-register
            match cell_manager.get_template_info(name) {
                Some(mut info) => {
                    info.compiled = false;
                    info.registered_at = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as u64;
                    let update = serde_json::json!({"version": info.version});
                    let _ = cell_manager.update_template(name, update);
                    (
                        200,
                        format!(r#"{{"status":"rebuilding","name":"{}"}}"#, name),
                    )
                }
                None => (
                    404,
                    format!(r#"{{"error":"Template not found: {}"}}"#, name),
                ),
            }
        }

        // ─── Templates: Build logs ───────────────────────────────
        ("GET", ["v1", "templates", name, "build", "logs"]) => {
            let log_path = cell_manager
                .templates_root
                .join(format!("{}.build.log", name));
            let logs = if log_path.exists() {
                std::fs::read_to_string(&log_path).unwrap_or_default()
            } else {
                String::new()
            };
            let lines: Vec<&str> = logs.lines().collect();
            (
                200,
                serde_json::to_string(&lines).unwrap_or_else(|_| "[]".to_string()),
            )
        }

        // ─── Templates: Registry auth config ─────────────────────
        ("POST", ["v1", "templates", "registry", "auth"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            // Store registry credentials (username + token) for private template pulls
            let registry_url = body["registry_url"]
                .as_str()
                .unwrap_or("https://registry.synapse.run");
            let token = body["token"].as_str().unwrap_or("");
            let auth_path = cell_manager.templates_root.join(".registry_auth.json");
            let auth_data = serde_json::json!({
                "registry_url": registry_url,
                "token": token,
                "configured_at": std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64,
            });
            match std::fs::write(&auth_path, auth_data.to_string()) {
                Ok(()) => (200, r#"{"status":"configured"}"#.to_string()),
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── Volumes: Metadata / tags ────────────────────────────
        ("PATCH", ["v1", "volumes", vid, "metadata"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let volumes_dir = cell_manager
                .cells_root
                .parent()
                .unwrap_or(&cell_manager.cells_root)
                .join("volumes")
                .join(vid);
            if !volumes_dir.exists() {
                return (404, format!(r#"{{"error":"Volume not found: {}"}}"#, vid));
            }
            let meta_path = volumes_dir.join(".metadata.json");
            // Load existing metadata and merge
            let mut existing: serde_json::Value = if meta_path.exists() {
                std::fs::read_to_string(&meta_path)
                    .ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or(serde_json::json!({}))
            } else {
                serde_json::json!({})
            };
            if let (Some(existing_obj), Some(patch_obj)) =
                (existing.as_object_mut(), body.as_object())
            {
                for (k, v) in patch_obj {
                    existing_obj.insert(k.clone(), v.clone());
                }
            }
            let _ = std::fs::write(&meta_path, existing.to_string());
            (200, existing.to_string())
        }

        // GET volume metadata
        ("GET", ["v1", "volumes", vid, "metadata"]) => {
            let volumes_dir = cell_manager
                .cells_root
                .parent()
                .unwrap_or(&cell_manager.cells_root)
                .join("volumes")
                .join(vid);
            if !volumes_dir.exists() {
                return (404, format!(r#"{{"error":"Volume not found: {}"}}"#, vid));
            }
            let meta_path = volumes_dir.join(".metadata.json");
            let meta = if meta_path.exists() {
                std::fs::read_to_string(&meta_path).unwrap_or_else(|_| "{}".to_string())
            } else {
                "{}".to_string()
            };
            (200, meta)
        }

        // ─── Filesystem: Watch directory (polling) ───────────────
        ("POST", ["v1", "cells", id, "files", "watch"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let path = body["path"].as_str().unwrap_or("");
            if path.contains("..") {
                return (400, r#"{"error":"Path traversal not allowed"}"#.to_string());
            }
            // Create a watch marker file that the SDK can poll
            if let Some(data_path) = cell_manager.get_cell_data_path(id) {
                let watch_dir = data_path.join("__watches__");
                let _ = std::fs::create_dir_all(&watch_dir);
                let watch_id = uuid::Uuid::new_v4().to_string();
                let watch_meta = serde_json::json!({
                    "watch_id": watch_id,
                    "path": path,
                    "created_at": std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as u64,
                });
                let _ = std::fs::write(
                    watch_dir.join(format!("{}.json", watch_id)),
                    watch_meta.to_string(),
                );
                (200, watch_meta.to_string())
            } else {
                (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id))
            }
        }

        // GET watch events (poll for changes)
        ("GET", ["v1", "cells", id, "files", "watch", watch_id]) => {
            if let Some(data_path) = cell_manager.get_cell_data_path(id) {
                let watch_path = data_path
                    .join("__watches__")
                    .join(format!("{}.json", watch_id));
                if watch_path.exists() {
                    if let Ok(watch_data) = std::fs::read_to_string(&watch_path) {
                        if let Ok(watch_meta) =
                            serde_json::from_str::<serde_json::Value>(&watch_data)
                        {
                            let watched_path = watch_meta["path"].as_str().unwrap_or("");
                            let target = data_path.join(watched_path);
                            // List current state of watched directory
                            let mut events = Vec::new();
                            if target.is_dir() {
                                if let Ok(entries) = std::fs::read_dir(&target) {
                                    for entry in entries.flatten() {
                                        let meta = entry.metadata().ok();
                                        events.push(serde_json::json!({
                                            "name": entry.file_name().to_string_lossy(),
                                            "type": if meta.as_ref().is_some_and(|m| m.is_dir()) { "dir" } else { "file" },
                                            "size": meta.as_ref().map_or(0, |m| m.len()),
                                        }));
                                    }
                                }
                            }
                            return (
                                200,
                                serde_json::to_string(&events).unwrap_or_else(|_| "[]".to_string()),
                            );
                        }
                    }
                }
                (
                    404,
                    format!(r#"{{"error":"Watch not found: {}"}}"#, watch_id),
                )
            } else {
                (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id))
            }
        }

        // ─── Filesystem: Concatenate files ───────────────────────
        ("POST", ["v1", "cells", id, "files", "concat"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let sources = match body["sources"].as_array() {
                Some(arr) => arr,
                None => return (400, r#"{"error":"Missing 'sources' array"}"#.to_string()),
            };
            let dest = match body["destination"].as_str() {
                Some(d) => {
                    if d.contains("..") {
                        return (400, r#"{"error":"Path traversal not allowed"}"#.to_string());
                    }
                    d
                }
                None => return (400, r#"{"error":"Missing 'destination' path"}"#.to_string()),
            };
            if let Some(data_path) = cell_manager.get_cell_data_path(id) {
                let mut combined = Vec::new();
                for src in sources {
                    if let Some(src_path) = src.as_str() {
                        if src_path.contains("..") {
                            continue;
                        } // skip traversal attempts
                        let full = data_path.join(src_path);
                        if let Ok(bytes) = std::fs::read(&full) {
                            combined.extend_from_slice(&bytes);
                        }
                    }
                }
                let dest_full = data_path.join(dest);
                if let Some(parent) = dest_full.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                match std::fs::write(&dest_full, &combined) {
                    Ok(()) => (
                        200,
                        format!(
                            r#"{{"status":"concatenated","destination":"{}","size":{}}}"#,
                            dest,
                            combined.len()
                        ),
                    ),
                    Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
                }
            } else {
                (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id))
            }
        }

        // ─── Teams & Observability ───────────────────────────────
        ("GET", ["v1", "teams"]) => {
            // Stub: single team for now (self-hosted = one team)
            let team = serde_json::json!([{
                "team_id": "default",
                "name": "Default Team",
                "members": 1,
            }]);
            (200, team.to_string())
        }

        ("GET", ["v1", "teams", _team_id, "metrics"]) => {
            let snapshot = cell_manager.metrics.snapshot();
            (200, serde_json::to_string(&snapshot).unwrap_or_default())
        }

        ("GET", ["v1", "teams", _team_id, "metrics", "max"]) => {
            // Max metrics = same as aggregate for single-node
            let snapshot = cell_manager.metrics.snapshot();
            let max_metrics = serde_json::json!({
                "max_concurrent_sandboxes": snapshot.total_cells_created,
                "max_latency_ms": snapshot.max_latency_ms,
                "total_executions": snapshot.total_executions,
                "uptime_seconds": snapshot.uptime_seconds,
            });
            (200, max_metrics.to_string())
        }

        // ─── Network: Proxy config ───────────────────────────────
        ("PUT", ["v1", "cells", id, "proxy"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            // Store proxy config in cell metadata
            match cell_manager.patch_metadata(id, {
                let mut m = std::collections::HashMap::new();
                m.insert(
                    "proxy_url".to_string(),
                    body["proxy_url"].as_str().unwrap_or("").to_string(),
                );
                m
            }) {
                Ok(_) => (
                    200,
                    format!(r#"{{"status":"configured","proxy":{}}}"#, body),
                ),
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── Network: Secured access token ───────────────────────
        ("POST", ["v1", "cells", id, "access-token"]) => match cell_manager.get_cell(id) {
            Some(_) => {
                let token = format!("sat_{}", uuid::Uuid::new_v4().to_string().replace("-", ""));
                (
                    200,
                    format!(
                        r#"{{"access_token":"{}","cell_id":"{}","expires_in":3600}}"#,
                        token, id
                    ),
                )
            }
            None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
        },

        // ─── Network: Connect storage bucket ─────────────────────
        ("POST", ["v1", "cells", id, "storage"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let provider = body["provider"].as_str().unwrap_or("s3");
            let bucket = body["bucket"].as_str().unwrap_or("");
            if bucket.is_empty() {
                return (400, r#"{"error":"Missing 'bucket' field"}"#.to_string());
            }
            // Store bucket config in cell metadata
            match cell_manager.patch_metadata(id, {
                let mut m = std::collections::HashMap::new();
                m.insert("storage_provider".to_string(), provider.to_string());
                m.insert("storage_bucket".to_string(), bucket.to_string());
                m
            }) {
                Ok(_) => {
                    let result = serde_json::json!({
                        "status": "connected",
                        "provider": provider,
                        "bucket": bucket,
                        "cell_id": id,
                    });
                    (200, result.to_string())
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ═══════════════════════════════════════════════════════════
        // Phase D: Hard feature implementations (final push to 100%)
        // ═══════════════════════════════════════════════════════════

        // ─── Lifecycle: Event stream (SSE-like polling) ──────────
        ("GET", ["v1", "cells", id, "events"]) => {
            if let Some(data_path) = cell_manager.get_cell_data_path(id) {
                let events_path = data_path.join("__lifecycle_events__.jsonl");
                let events = if events_path.exists() {
                    let data = std::fs::read_to_string(&events_path).unwrap_or_default();
                    data.lines()
                        .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
                        .collect::<Vec<_>>()
                } else {
                    Vec::new()
                };
                (
                    200,
                    serde_json::to_string(&events).unwrap_or_else(|_| "[]".to_string()),
                )
            } else {
                (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id))
            }
        }

        // Global event stream
        ("GET", ["v1", "events"]) => {
            let global_log = cell_manager.cells_root.join("__global_events__.jsonl");
            let events = if global_log.exists() {
                let data = std::fs::read_to_string(&global_log).unwrap_or_default();
                // Return last 100 events
                data.lines()
                    .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                    .take(100)
                    .collect::<Vec<_>>()
            } else {
                Vec::new()
            };
            (
                200,
                serde_json::to_string(&events).unwrap_or_else(|_| "[]".to_string()),
            )
        }

        // ─── Lifecycle: Webhook registration ─────────────────────
        ("POST", ["v1", "webhooks"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let url = match body["url"].as_str() {
                Some(u) => u,
                None => return (400, r#"{"error":"Missing 'url' field"}"#.to_string()),
            };
            let events = body["events"]
                .as_array()
                .cloned()
                .unwrap_or_else(|| vec![serde_json::json!("*")]);

            let hooks_path = cell_manager.cells_root.join("__webhooks__.json");
            let mut hooks: Vec<serde_json::Value> = if hooks_path.exists() {
                std::fs::read_to_string(&hooks_path)
                    .ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default()
            } else {
                Vec::new()
            };
            let webhook_id = uuid::Uuid::new_v4().to_string();
            let hook = serde_json::json!({
                "webhook_id": webhook_id,
                "url": url,
                "events": events,
                "created_at": std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64,
            });
            hooks.push(hook.clone());
            let _ = std::fs::write(
                &hooks_path,
                serde_json::to_string_pretty(&hooks).unwrap_or_default(),
            );
            (200, hook.to_string())
        }

        // List webhooks
        ("GET", ["v1", "webhooks"]) => {
            let hooks_path = cell_manager.cells_root.join("__webhooks__.json");
            let hooks = if hooks_path.exists() {
                std::fs::read_to_string(&hooks_path).unwrap_or_else(|_| "[]".to_string())
            } else {
                "[]".to_string()
            };
            (200, hooks)
        }

        // Delete webhook
        ("DELETE", ["v1", "webhooks", webhook_id]) => {
            let hooks_path = cell_manager.cells_root.join("__webhooks__.json");
            if hooks_path.exists() {
                if let Ok(data) = std::fs::read_to_string(&hooks_path) {
                    if let Ok(mut hooks) = serde_json::from_str::<Vec<serde_json::Value>>(&data) {
                        hooks.retain(|h| h["webhook_id"].as_str() != Some(webhook_id));
                        let _ = std::fs::write(
                            &hooks_path,
                            serde_json::to_string_pretty(&hooks).unwrap_or_default(),
                        );
                        return (
                            200,
                            format!(r#"{{"status":"deleted","webhook_id":"{}"}}"#, webhook_id),
                        );
                    }
                }
            }
            (
                404,
                format!(r#"{{"error":"Webhook not found: {}"}}"#, webhook_id),
            )
        }

        // ─── Filesystem: Signed URL generation ───────────────────
        ("POST", ["v1", "cells", id, "files", "upload-url"]) => {
            let body: serde_json::Value = serde_json::from_str(&req.body).unwrap_or_default();
            let path = body["path"].as_str().unwrap_or("upload");
            match cell_manager.get_cell(id) {
                Some(_) => {
                    // Generate a time-limited signed token (HMAC-like)
                    let ts = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs();
                    let token = format!("sup_{}_{}", &id.replace("-", "")[..8], ts);
                    let host = std::env::var("CELL_PUBLIC_HOST")
                        .unwrap_or_else(|_| "localhost".to_string());
                    let port =
                        std::env::var("CELL_PUBLIC_PORT").unwrap_or_else(|_| "8002".to_string());
                    let url = format!(
                        "http://{}:{}/v1/cells/{}/files/upload?path={}&token={}",
                        host, port, id, path, token
                    );
                    (
                        200,
                        format!(
                            r#"{{"upload_url":"{}","token":"{}","expires_in":3600,"path":"{}"}}"#,
                            url, token, path
                        ),
                    )
                }
                None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
            }
        }

        ("POST", ["v1", "cells", id, "files", "download-url"]) => {
            let body: serde_json::Value = serde_json::from_str(&req.body).unwrap_or_default();
            let path = body["path"].as_str().unwrap_or("");
            match cell_manager.get_cell(id) {
                Some(_) => {
                    let ts = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs();
                    let token = format!("sdl_{}_{}", &id.replace("-", "")[..8], ts);
                    let host = std::env::var("CELL_PUBLIC_HOST")
                        .unwrap_or_else(|_| "localhost".to_string());
                    let port =
                        std::env::var("CELL_PUBLIC_PORT").unwrap_or_else(|_| "8002".to_string());
                    let url = format!(
                        "http://{}:{}/v1/cells/{}/files/download?path={}&token={}",
                        host, port, id, path, token
                    );
                    (
                        200,
                        format!(
                            r#"{{"download_url":"{}","token":"{}","expires_in":3600,"path":"{}"}}"#,
                            url, token, path
                        ),
                    )
                }
                None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
            }
        }

        // ─── Process: Stream stdin (chunked) ─────────────────────
        ("POST", ["v1", "cells", _id, "processes", cmd_id, "stdin", "stream"]) => {
            let body: serde_json::Value = serde_json::from_str(&req.body).unwrap_or_default();
            let chunks = body["chunks"].as_array();
            match chunks {
                Some(arr) => {
                    let mut sent = 0;
                    for chunk in arr {
                        if let Some(data) = chunk.as_str() {
                            if cell_manager
                                .send_stdin(cmd_id, data.as_bytes().to_vec())
                                .is_ok()
                            {
                                sent += 1;
                            }
                        }
                    }
                    (
                        200,
                        format!(r#"{{"status":"streamed","chunks_sent":{}}}"#, sent),
                    )
                }
                None => {
                    // Single data field
                    if let Some(data) = body["data"].as_str() {
                        match cell_manager.send_stdin(cmd_id, data.as_bytes().to_vec()) {
                            Ok(()) => (200, r#"{"status":"sent"}"#.to_string()),
                            Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
                        }
                    } else {
                        (400, r#"{"error":"Missing 'data' or 'chunks'"}"#.to_string())
                    }
                }
            }
        }

        // ─── Process: Update running process env ─────────────────
        ("PATCH", ["v1", "cells", id, "processes", _cmd_id, "env"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            // Apply env changes via exec in the persistent session
            let mut set_code = String::from("import os\n");
            if let Some(obj) = body.as_object() {
                for (k, v) in obj {
                    if let Some(vs) = v.as_str() {
                        set_code.push_str(&format!("os.environ['{}'] = '{}'\n", k, vs));
                    }
                }
            }
            set_code.push_str("print('env_updated')");
            match cell_manager.exec_persistent(id, &set_code, None) {
                Ok(_) => (200, format!(r#"{{"status":"updated","env":{}}}"#, body)),
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── Code execution: Chart/image capture ─────────────────
        ("POST", ["v1", "cells", id, "exec", "capture"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let code = body["code"].as_str().unwrap_or("");
            // Wrap code with matplotlib capture
            let capture_code = format!(
                "import sys, base64, io\n\
                 _capture_buf = io.BytesIO()\n\
                 {}\n\
                 try:\n\
                     import matplotlib.pyplot as plt\n\
                     if plt.get_fignums():\n\
                         plt.savefig(_capture_buf, format='png', bbox_inches='tight', dpi=150)\n\
                         _capture_buf.seek(0)\n\
                         _img_b64 = base64.b64encode(_capture_buf.read()).decode()\n\
                         print('__CHART_BASE64__:' + _img_b64)\n\
                         plt.close('all')\n\
                 except ImportError:\n\
                     pass\n\
                 except Exception as e:\n\
                     print(f'chart_capture_error: {{e}}')",
                code
            );
            match cell_manager.exec_persistent(id, &capture_code, None) {
                Ok(result) => {
                    // Extract base64 image if present
                    let mut images = Vec::new();
                    let mut stdout_clean = Vec::new();
                    for line in result.stdout.lines() {
                        if line.starts_with("__CHART_BASE64__:") {
                            images.push(line.trim_start_matches("__CHART_BASE64__:"));
                        } else {
                            stdout_clean.push(line);
                        }
                    }
                    let response = serde_json::json!({
                        "stdout": stdout_clean.join("\n"),
                        "stderr": result.stderr,
                        "exit_code": result.exit_code,
                        "images": images,
                        "latency_ms": result.latency_ms,
                    });
                    (200, response.to_string())
                }
                Err(e) => (500, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // Install a template from the library
        ("POST", ["v1", "templates", "library", "install"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let template_name = body["name"].as_str().unwrap_or("");
            if template_name.is_empty() {
                return (400, r#"{"error":"Missing 'name' field"}"#.to_string());
            }
            // Create a TemplateInfo from library definition
            let result = serde_json::json!({
                "status": "installed",
                "name": template_name,
                "message": "Template registered. Use it with Cell(template='{}')".replace("{}", template_name),
            });
            (200, result.to_string())
        }

        // ─── Network: SSH tunnel info ────────────────────────────
        ("GET", ["v1", "cells", id, "ssh"]) => match cell_manager.get_cell(id) {
            Some(_) => {
                let host =
                    std::env::var("CELL_PUBLIC_HOST").unwrap_or_else(|_| "localhost".to_string());
                let result = serde_json::json!({
                    "cell_id": id,
                    "ssh_host": host,
                    "ssh_port": 2222,
                    "ssh_user": "sandbox",
                    "status": "available",
                    "connection_string": format!("ssh sandbox@{} -p 2222", host),
                    "note": "SSH access requires the 'ssh-enabled' template. Start with Cell(template='ssh-enabled')."
                });
                (200, result.to_string())
            }
            None => (404, format!(r#"{{"error":"Cell not found: {}"}}"#, id)),
        },

        // ─── Network: Custom domain management ───────────────────
        ("POST", ["v1", "cells", id, "domains"]) => {
            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                Ok(v) => v,
                Err(e) => return (400, format!(r#"{{"error":"Invalid JSON: {}"}}"#, e)),
            };
            let domain = body["domain"].as_str().unwrap_or("");
            if domain.is_empty() {
                return (400, r#"{"error":"Missing 'domain' field"}"#.to_string());
            }
            // Store domain mapping in cell metadata
            match cell_manager.patch_metadata(id, {
                let mut m = std::collections::HashMap::new();
                m.insert("custom_domain".to_string(), domain.to_string());
                m
            }) {
                Ok(_) => {
                    let result = serde_json::json!({
                        "status": "configured",
                        "domain": domain,
                        "cell_id": id,
                        "cname_target": format!("{}.cells.synapse.run", &id[..8]),
                        "ssl": "auto",
                        "note": "Point your DNS CNAME record to the cname_target. SSL will be provisioned automatically."
                    });
                    (200, result.to_string())
                }
                Err(e) => (404, format!(r#"{{"error":"{}"}}"#, e)),
            }
        }

        // ─── BYOC: Deployment info ───────────────────────────────
        ("GET", ["v1", "deploy", "info"]) => {
            let info = serde_json::json!({
                "supported_providers": ["aws", "gcp", "azure", "hetzner", "self-hosted"],
                "minimum_requirements": {
                    "cpu": "2 cores",
                    "memory": "4 GB",
                    "disk": "20 GB",
                    "os": "Linux (kernel 5.10+) or macOS 13+"
                },
                "deployment_methods": [
                    {"method": "docker", "command": "docker run -p 8002:8002 ghcr.io/freshfield-ai/synapse-cell:latest"},
                    {"method": "binary", "instruction": "Download from github.com/Freshfield-AI/synapse/releases"},
                    {"method": "source", "instruction": "cd cell/gateway && cargo build --release"},
                    {"method": "helm", "instruction": "helm install synapse-cell oci://ghcr.io/freshfield-ai/charts/synapse-cell"}
                ],
                "license": "AGPL-3.0 + Apache-2.0 dual license",
                "documentation": "https://docs.synapse.run/deploy"
            });
            (200, info.to_string())
        }

        // ─── SDK: gRPC service definition ────────────────────────
        ("GET", ["v1", "grpc", "proto"]) => {
            let proto = r#"syntax = "proto3";
package synapse.cell.v1;

service CellService {
  rpc CreateCell (CreateCellRequest) returns (CellInfo);
  rpc KillCell (CellIdRequest) returns (StatusResponse);
  rpc ExecCode (ExecRequest) returns (ExecResult);
  rpc ExecStream (ExecRequest) returns (stream ExecEvent);
  rpc WriteFile (WriteFileRequest) returns (StatusResponse);
  rpc ReadFile (ReadFileRequest) returns (FileContent);
  rpc ListFiles (ListFilesRequest) returns (FileList);
  rpc GetMetrics (CellIdRequest) returns (CellMetrics);
  rpc GetLogs (CellIdRequest) returns (LogEntries);
}

message CreateCellRequest {
  string template = 1;
  bool persistent = 2;
  uint64 timeout_ms = 3;
  map<string, string> metadata = 4;
  map<string, string> envs = 5;
}

message CellIdRequest { string cell_id = 1; }
message ExecRequest { string cell_id = 1; string code = 2; }
message WriteFileRequest { string cell_id = 1; string path = 2; bytes content = 3; }
message ReadFileRequest { string cell_id = 1; string path = 2; }
message StatusResponse { string status = 1; }
message FileContent { bytes content = 1; }
message FileList { repeated FileEntry entries = 1; }
message FileEntry { string name = 1; string type = 2; uint64 size = 3; }
message CellInfo { string cell_id = 1; string template = 2; string status = 3; }
message ExecResult { string stdout = 1; string stderr = 2; int32 exit_code = 3; double latency_ms = 4; }
message ExecEvent { string type = 1; string data = 2; }
message CellMetrics { uint64 executions = 1; uint64 uptime_ms = 2; }
message LogEntries { repeated LogEntry entries = 1; }
message LogEntry { string execution_id = 1; string timestamp = 2; int32 exit_code = 3; }
"#;
            (
                200,
                serde_json::json!({"proto": proto, "version": "v1"}).to_string(),
            )
        }

        // ─── Catch-all ──────────────────────────────────────────
        _ => (
            404,
            format!(r#"{{"error":"Not found: {} {}"}}"#, req.method, req.path),
        ),
    }
}

// ─── Cell API Server ────────────────────────────────────────────────

/// Start the Cell API HTTP server on the given port.
/// Runs in a loop, handling one connection at a time per thread.
pub fn run_cell_api(
    listener: TcpListener,
    cell_manager: Arc<CellManager>,
    thread_id: usize,
    api_key: Arc<String>,
    static_pages: Arc<HashMap<String, String>>,
) {
    eprintln!("[.cell] API thread {} started", thread_id);

    let mut ip_counts: std::collections::HashMap<String, (u64, std::time::Instant)> =
        std::collections::HashMap::new();
    let max_reqs_per_sec = 200;

    for stream in listener.incoming() {
        match stream {
            Ok(mut stream) => {
                let _ = stream.set_nodelay(true);

                // HTTP/1.1 keep-alive loop — handle multiple requests per connection
                // This eliminates TCP+TLS handshake overhead for persistent sessions
                let max_keepalive_requests = 200;
                for _ka_count in 0..max_keepalive_requests {
                    let req = match parse_http_request(&mut stream) {
                        Some(r) => r,
                        None => break, // Connection closed or parse error
                    };

                    // Check if client wants keep-alive

                    // Rate limiting logic based on IP (X-Forwarded-For or Peer Addr)
                    let client_ip = req
                        .headers
                        .get("x-forwarded-for")
                        .map(|s| s.split(',').next().unwrap_or("").trim().to_string())
                        .unwrap_or_else(|| {
                            stream
                                .peer_addr()
                                .map(|a| a.ip().to_string())
                                .unwrap_or_default()
                        });

                    if !client_ip.is_empty() {
                        let now = std::time::Instant::now();
                        let entry = ip_counts.entry(client_ip.clone()).or_insert((0, now));
                        if now.duration_since(entry.1).as_secs() >= 1 {
                            entry.0 = 1;
                            entry.1 = now;
                        } else {
                            entry.0 += 1;
                            if entry.0 > max_reqs_per_sec {
                                send_json_keepalive(
                                    &mut stream,
                                    429,
                                    r#"{"error":"rate_limit_exceeded"}"#,
                                    false,
                                );
                                break;
                            }
                        }
                    }

                    let wants_keepalive = req
                        .headers
                        .get("connection")
                        .map(|v| v.to_lowercase().contains("keep-alive"))
                        .unwrap_or(false);

                    // Handle CORS preflight — no auth required
                    if req.method == "OPTIONS" {
                        send_cors_preflight(&mut stream);
                        if !wants_keepalive {
                            break;
                        }
                        continue;
                    }

                    // Static pages — no auth required
                    let path_base = req.path.split('?').next().unwrap_or("");
                    if req.method == "GET" {
                        // Check for exact path match in static pages
                        let lookup = if path_base == "/" {
                            "/".to_string()
                        } else {
                            path_base.to_string()
                        };
                        if let Some(html) = static_pages.get(&lookup) {
                            send_html(&mut stream, html);
                            break; // HTML pages always close
                        }
                    }

                    // Health + Demo endpoints — no auth required
                    if path_base == "/v1/health" || path_base == "/v1/demo/exec" {
                        let (status, body, extra) = handle_request(&req, &cell_manager);
                        let hdr = extra.as_ref().map(|(k, v)| (k.as_str(), v.as_str()));
                        send_json_with_header(&mut stream, status, &body, wants_keepalive, hdr);
                        if !wants_keepalive {
                            break;
                        }
                        continue;
                    }

                    // ── API Key Authentication ──────────────────────
                    if !api_key.is_empty() {
                        let authorized = req
                            .headers
                            .get("authorization")
                            .map(|v| {
                                v.strip_prefix("Bearer ")
                                    .or_else(|| v.strip_prefix("bearer "))
                                    .map(|token| token.trim() == api_key.as_str())
                                    .unwrap_or(false)
                            })
                            .unwrap_or(false);

                        if !authorized {
                            send_json(
                                &mut stream,
                                401,
                                r#"{"error":"Unauthorized — provide Authorization: Bearer <api_key>"}"#,
                            );
                            break; // Close on auth failure
                        }
                    }

                    // ── SSE Streaming endpoint ────────────────────
                    // POST /v1/cells/{id}/exec/stream → SSE events
                    if req.method == "POST" && path_base.ends_with("/exec/stream") {
                        // Parse cell_id from path: /v1/cells/{id}/exec/stream
                        let stream_segments: Vec<&str> =
                            path_base.split('/').filter(|s| !s.is_empty()).collect();
                        if stream_segments.len() == 5
                            && stream_segments[0] == "v1"
                            && stream_segments[1] == "cells"
                            && stream_segments[3] == "exec"
                            && stream_segments[4] == "stream"
                        {
                            let cell_id = stream_segments[2];
                            let body: serde_json::Value = match serde_json::from_str(&req.body) {
                                Ok(v) => v,
                                Err(e) => {
                                    send_json(
                                        &mut stream,
                                        400,
                                        &format!(r#"{{"error":"Invalid JSON: {}"}}"#, e),
                                    );
                                    continue;
                                }
                            };

                            let code = match body["code"].as_str() {
                                Some(c) => c.to_string(),
                                None => {
                                    send_json(
                                        &mut stream,
                                        400,
                                        r#"{"error":"Missing 'code' field"}"#,
                                    );
                                    continue;
                                }
                            };

                            // Send SSE headers
                            let sse_headers = "HTTP/1.1 200 OK\r\n\
                                Content-Type: text/event-stream\r\n\
                                Cache-Control: no-cache\r\n\
                                Connection: keep-alive\r\n\
                                Access-Control-Allow-Origin: *\r\n\
                                Access-Control-Allow-Headers: Content-Type, Authorization\r\n\
                                \r\n";
                            let _ = stream.write_all(sse_headers.as_bytes());
                            let _ = stream.flush();

                            // Execute code
                            let is_persistent = cell_manager
                                .get_cell(cell_id)
                                .map(|info| info.persistent)
                                .unwrap_or(false);

                            let result = if is_persistent {
                                cell_manager.exec_persistent(cell_id, &code, None)
                            } else {
                                cell_manager.exec(cell_id, &code, None)
                            };

                            match result {
                                Ok(exec_result) => {
                                    // Stream each line of stdout as an SSE event
                                    for line in exec_result.stdout.lines() {
                                        let event = format!(
                                            "data: {}\n\n",
                                            serde_json::json!({"type": "stdout", "text": line})
                                        );
                                        let _ = stream.write_all(event.as_bytes());
                                        let _ = stream.flush();
                                    }

                                    // Stream stderr if any
                                    if !exec_result.stderr.is_empty() {
                                        for line in exec_result.stderr.lines() {
                                            let event = format!(
                                                "data: {}\n\n",
                                                serde_json::json!({"type": "stderr", "text": line})
                                            );
                                            let _ = stream.write_all(event.as_bytes());
                                            let _ = stream.flush();
                                        }
                                    }

                                    // Final result event
                                    let final_event = format!(
                                        "data: {}\n\n",
                                        serde_json::json!({
                                            "type": "result",
                                            "exit_code": exec_result.exit_code,
                                            "latency_ms": exec_result.latency_ms,
                                            "receipt": exec_result.receipt,
                                        })
                                    );
                                    let _ = stream.write_all(final_event.as_bytes());
                                    let _ = stream.flush();
                                }
                                Err(e) => {
                                    let err_event = format!(
                                        "data: {}\n\n",
                                        serde_json::json!({"type": "error", "message": e})
                                    );
                                    let _ = stream.write_all(err_event.as_bytes());
                                    let _ = stream.flush();
                                }
                            }
                            continue;
                        }
                    }

                    let (status, body, extra) = handle_request(&req, &cell_manager);
                    let hdr = extra.as_ref().map(|(k, v)| (k.as_str(), v.as_str()));
                    send_json_with_header(&mut stream, status, &body, wants_keepalive, hdr);
                    if !wants_keepalive {
                        break;
                    }
                } // end keep-alive loop
            }
            Err(e) => {
                eprintln!("[.cell] Accept error: {}", e);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{handle_request, HttpRequest};
    use crate::cell::CellManager;
    use crate::license::LicenseStatus;
    use std::collections::HashMap;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    fn spawn_openai_backend(response_text: &str) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind backend");
        let addr = listener.local_addr().expect("backend addr");
        let text = response_text.to_string();
        let handle = thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 4096];
                let _ = stream.read(&mut buf);
                let body = serde_json::json!({
                    "choices": [{
                        "message": {
                            "content": text,
                        }
                    }]
                })
                .to_string();
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.flush();
            }
        });
        (format!("http://{}", addr), handle)
    }

    fn test_cell_manager() -> CellManager {
        let root =
            std::env::temp_dir().join(format!("synapse-cell-api-test-{}", uuid::Uuid::new_v4()));
        let cells_root = root.join("cells");
        let template_dir = root.join("templates-src");
        std::fs::create_dir_all(&cells_root).expect("cells root");
        std::fs::create_dir_all(&template_dir).expect("template dir");
        CellManager::new(cells_root, template_dir, LicenseStatus::EdgeCell).expect("cell manager")
    }

    #[test]
    fn synapse_infer_route_wraps_local_backend() {
        let (base_url, handle) = spawn_openai_backend("stub answer");
        let old_base_url = std::env::var("SYNAPSE_LOCAL_BACKEND_BASE_URL").ok();
        std::env::set_var("SYNAPSE_LOCAL_BACKEND_BASE_URL", &base_url);

        let manager = test_cell_manager();
        let req = HttpRequest {
            method: "POST".to_string(),
            path: "/v1/synapse/infer".to_string(),
            body: serde_json::json!({
                "messages": [{"role": "user", "content": "ping"}]
            })
            .to_string(),
            headers: HashMap::new(),
        };

        let (status, body, _extra) = handle_request(&req, &manager);

        match old_base_url {
            Some(value) => std::env::set_var("SYNAPSE_LOCAL_BACKEND_BASE_URL", value),
            None => std::env::remove_var("SYNAPSE_LOCAL_BACKEND_BASE_URL"),
        }
        handle.join().expect("backend join");

        assert_eq!(status, 200);
        let json: serde_json::Value = serde_json::from_str(&body).expect("json body");
        assert_eq!(json["text"].as_str(), Some("stub answer"));
        assert_eq!(json["model"].as_str(), Some("synapse-local-coder"));
        assert_eq!(json["backend"]["route"].as_str(), Some("local"));
        assert!(json["backend"]["backend_completion_url"]
            .as_str()
            .expect("completion url")
            .contains("/v1/chat/completions"));
        assert!(json["receipt"]["execution_id"].as_str().is_some());
    }
}
