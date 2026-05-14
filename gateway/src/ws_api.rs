use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use tungstenite::{accept_hdr, Message};
use tungstenite::handshake::server::{Request, Response};
use serde_json::json;

use crate::cell::CellManager;

pub fn run_ws_api(listener: TcpListener, cell_manager: Arc<CellManager>) {
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let cm = Arc::clone(&cell_manager);
                std::thread::spawn(move || {
                    handle_ws_connection(stream, cm);
                });
            }
            Err(e) => {
                eprintln!("[.cell] WebSocket accept error: {}", e);
            }
        }
    }
}

fn handle_ws_connection(stream: TcpStream, cell_manager: Arc<CellManager>) {
    // Extract cell_id and endpoint type from URL path during handshake.
    // REPL path: /v1/cells/{cell_id}/ws
    // PTY  path: /v1/cells/{cell_id}/pty
    let mut extracted_cell_id = String::new();
    let mut extracted_path = String::new();
    let callback = |req: &Request, response: Response| {
        let path = req.uri().path().to_string();
        if path.starts_with("/v1/cells/") {
            let parts: Vec<&str> = path.split('/').collect();
            if parts.len() == 5 {
                extracted_cell_id = parts[3].to_string();
                extracted_path = path;
            }
        }
        Ok(response)
    };

    let mut websocket = match accept_hdr(stream, callback) {
        Ok(ws) => ws,
        Err(e) => {
            eprintln!("[.cell] WS handshake failed: {}", e);
            return;
        }
    };

    let cell_id = extracted_cell_id;
    if cell_id.is_empty() {
        let _ = websocket.send(Message::Text(json!({"error": "Invalid WebSocket path"}).to_string().into()));
        let _ = websocket.close(None);
        return;
    }

    // Dispatch by path suffix
    if extracted_path.ends_with("/pty") {
        handle_pty_connection(websocket, cell_id, cell_manager);
    } else {
        handle_repl_connection(websocket, cell_id, cell_manager);
    }
}

// ─── REPL terminal (original behavior) ─────────────────────────────

fn handle_repl_connection(
    mut websocket: tungstenite::WebSocket<TcpStream>,
    cell_id: String,
    cell_manager: Arc<CellManager>,
) {
    if let Some(info) = cell_manager.get_cell(&cell_id) {
        if !info.persistent {
            let _ = websocket.send(Message::Text(json!({"error": "Cell must be persistent for WS terminal"}).to_string().into()));
            let _ = websocket.close(None);
            return;
        }
    } else {
        let _ = websocket.send(Message::Text(json!({"error": "Cell not found"}).to_string().into()));
        let _ = websocket.close(None);
        return;
    }

    if std::env::var("CELL_VERBOSE").is_ok() { eprintln!("[.cell] WebSocket REPL connected for cell {}", &cell_id[..8]); }

    loop {
        let msg = match websocket.read() {
            Ok(msg) => msg,
            Err(tungstenite::error::Error::ConnectionClosed) => break,
            Err(tungstenite::error::Error::AlreadyClosed) => break,
            Err(e) => {
                eprintln!("[.cell] WS read error for cell {}: {}", &cell_id[..8], e);
                break;
            }
        };

        if let Message::Text(text) = msg {
            let text_str = text.to_string();
            let code = if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&text_str) {
                if let Some(cmd) = json_val.get("code").and_then(|v| v.as_str()) {
                    cmd.to_string()
                } else if let Some(cmd) = json_val.get("command").and_then(|v| v.as_str()) {
                    cmd.to_string()
                } else {
                    text_str.clone()
                }
            } else {
                text_str
            };

            let exec_outcome = cell_manager.exec(&cell_id, &code, None);
            let response = match exec_outcome {
                Ok(r) => json!({
                    "stdout": r.stdout,
                    "stderr": r.stderr,
                    "exit_code": r.exit_code,
                    "latency_ms": r.latency_ms,
                    "receipt": r.receipt
                }),
                Err(err_msg) => json!({
                    "stdout": "",
                    "stderr": err_msg,
                    "exit_code": -1,
                    "latency_ms": 0.0,
                    "receipt": serde_json::Value::Null
                }),
            };

            if let Err(e) = websocket.send(Message::Text(response.to_string().into())) {
                eprintln!("[.cell] WS send error for cell {}: {}", &cell_id[..8], e);
                break;
            }
        }
    }

    if std::env::var("CELL_VERBOSE").is_ok() { eprintln!("[.cell] WebSocket REPL disconnected for cell {}", &cell_id[..8]); }
}

// ─── PTY terminal (Sprint A Batch 3) ───────────────────────────────
//
// Phase A: Subprocess bridge via std::process::Command.
// Binary WS frames = raw shell I/O bytes.
// JSON text frames = control: {"type":"resize","cols":N,"rows":N} or {"type":"kill"}
// Phase B (Horizon 3): real openpty via nix crate for full terminal emulation.

fn handle_pty_connection(
    mut websocket: tungstenite::WebSocket<TcpStream>,
    cell_id: String,
    cell_manager: Arc<CellManager>,
) {
    use std::io::{Read, Write};

    // Verify cell exists and get working directory
    let data_path = match cell_manager.get_cell_data_path(&cell_id) {
        Some(p) => p,
        None => {
            let _ = websocket.send(Message::Text(
                json!({"type":"error","message":"Cell not found"}).to_string().into()
            ));
            return;
        }
    };

    // Spawn shell process
    let shell = if std::path::Path::new("/bin/bash").exists() {
        "/bin/bash"
    } else {
        "/bin/sh"
    };

    let mut child = match std::process::Command::new(shell)
        .current_dir(&data_path)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            let _ = websocket.send(Message::Text(
                json!({"type":"error","message": format!("Shell spawn failed: {}", e)}).to_string().into()
            ));
            return;
        }
    };

    let pid = child.id();
    if std::env::var("CELL_VERBOSE").is_ok() { eprintln!("[.cell] PTY connected for cell {} (pid {})", &cell_id[..8], pid); }

    let _ = websocket.send(Message::Text(
        json!({"type":"connected","pid":pid}).to_string().into()
    ));

    let mut child_stdin = child.stdin.take().unwrap();
    let child_stdout = child.stdout.take().unwrap();
    let child_stderr = child.stderr.take().unwrap();

    // Channel: shell output -> WS write loop
    let (out_tx, out_rx) = std::sync::mpsc::channel::<Vec<u8>>();

    // Stdout reader thread
    let tx1 = out_tx.clone();
    std::thread::Builder::new()
        .name(format!("pty-out-{}", &cell_id[..8]))
        .spawn(move || {
            let mut buf = [0u8; 4096];
            let mut r = child_stdout;
            loop {
                match r.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => { let _ = tx1.send(buf[..n].to_vec()); }
                }
            }
        })
        .ok();

    // Stderr reader thread (merged into same output channel)
    let tx2 = out_tx;
    std::thread::Builder::new()
        .name(format!("pty-err-{}", &cell_id[..8]))
        .spawn(move || {
            let mut buf = [0u8; 4096];
            let mut r = child_stderr;
            loop {
                match r.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => { let _ = tx2.send(buf[..n].to_vec()); }
                }
            }
        })
        .ok();

    // Main WS event loop
    loop {
        // Drain shell output -> WS binary frames
        while let Ok(data) = out_rx.try_recv() {
            if websocket.send(Message::Binary(data.into())).is_err() {
                let _ = child.kill();
                let _ = child.wait();
                if std::env::var("CELL_VERBOSE").is_ok() { eprintln!("[.cell] PTY disconnected for cell {} (WS send err)", &cell_id[..8]); }
                return;
            }
        }

        // Read incoming WS frame (short timeout to allow output drain)
        let _ = websocket.get_ref().set_read_timeout(
            Some(std::time::Duration::from_millis(10))
        );
        match websocket.read() {
            Ok(Message::Binary(data)) => {
                // Raw bytes -> shell stdin
                let _ = child_stdin.write_all(&data);
                let _ = child_stdin.flush();
            }
            Ok(Message::Text(text)) => {
                let text_str = text.to_string();
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text_str) {
                    match v["type"].as_str() {
                        Some("resize") => {
                            // Phase A: acknowledge only (no TIOCSWINSZ without real PTY)
                            let _ = websocket.send(Message::Text(
                                json!({"type":"resized"}).to_string().into()
                            ));
                        }
                        Some("kill") => break,
                        Some("stdin") => {
                            if let Some(data) = v["data"].as_str() {
                                let _ = child_stdin.write_all(data.as_bytes());
                                let _ = child_stdin.flush();
                            }
                        }
                        _ => {
                            let _ = child_stdin.write_all(text_str.as_bytes());
                            let _ = child_stdin.flush();
                        }
                    }
                } else {
                    let _ = child_stdin.write_all(text_str.as_bytes());
                    let _ = child_stdin.flush();
                }
            }
            Ok(Message::Close(_)) => break,
            Err(tungstenite::error::Error::Io(ref e))
                if e.kind() == std::io::ErrorKind::WouldBlock => {
                // Timeout on read — normal, continue drain loop
            }
            Err(tungstenite::error::Error::ConnectionClosed)
            | Err(tungstenite::error::Error::AlreadyClosed) => break,
            Err(_) => break,
            _ => {}
        }
    }

    // Cleanup
    let _ = child.kill();
    let _ = child.wait();
    if std::env::var("CELL_VERBOSE").is_ok() { eprintln!("[.cell] PTY disconnected for cell {} (pid {})", &cell_id[..8], pid); }
}
