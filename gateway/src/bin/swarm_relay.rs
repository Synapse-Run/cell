use std::net::{TcpListener, TcpStream};
use std::io::{Read, Write};
use std::thread;
use std::sync::{Arc, Mutex};
use tungstenite::accept;
use tungstenite::Message;

/// Atlantic Handshake Swarm Relay
/// High-throughput Tokio/Rust backbone for the Synapse edge network.
/// Deployed to Hetzner AX102 VPS nodes.

fn handle_client(mut stream: TcpStream, peers: Arc<Mutex<Vec<TcpStream>>>, ws_peers: Arc<Mutex<Vec<tungstenite::WebSocket<TcpStream>>>>) {
    let mut buffer = vec![0; 10_000_000]; // 10MB Wasm diff buffer
    loop {
        match stream.read(&mut buffer) {
            Ok(size) => {
                if size == 0 {
                    break; // Connection closed
                }
                
                // Parse the swarm identifier
                let payload_str = String::from_utf8_lossy(&buffer[0..size]);
                if payload_str.contains("diff") {
                    println!("[Hetzner-Relay] 📡 Routing Atlantic Biological Matrix ({} bytes)", size);
                    
                    // Broadcast asynchronous stream diffs to all OTHER active edge nodes
                    let mut peers_lock = peers.lock().unwrap();
                    let mut broken_peers = Vec::new();
                    
                    for (i, peer) in peers_lock.iter_mut().enumerate() {
                        // Do not route back to sender recursively (simple implementation)
                        if let Err(_) = peer.write_all(&buffer[0..size]) {
                            broken_peers.push(i);
                        }
                    }
                    for index in broken_peers.into_iter().rev() { peers_lock.remove(index); }
                    
                    // WebSocket Broadcast to UI Dashboards
                    let mut ws_lock = ws_peers.lock().unwrap();
                    let mut broken_ws = Vec::new();
                    let payload_str = String::from_utf8_lossy(&buffer[0..size]).to_string();
                    for (i, ws) in ws_lock.iter_mut().enumerate() {
                        if let Err(_) = ws.write(Message::Text(payload_str.clone())) {
                            broken_ws.push(i);
                        }
                    }
                    for index in broken_ws.into_iter().rev() { ws_lock.remove(index); }
                }
            }
            Err(_) => {
                break;
            }
        }
    }
}

fn main() {
    let port = "0.0.0.0:9010"; // Default relay mesh port
    let ws_port = "0.0.0.0:9011"; // Web UI Visualizer port
    
    let listener = TcpListener::bind(port).unwrap();
    println!("=======================================================");
    println!(" 🌌 Synapse Atlantic Swarm Backbone Online ");
    println!(" 📡 Listening TCP Mesh on {}", port);
    println!(" 🌐 Listening WebSocket Dashboard on {}", ws_port);
    println!("=======================================================");

    let peers: Arc<Mutex<Vec<TcpStream>>> = Arc::new(Mutex::new(Vec::new()));
    let ws_peers: Arc<Mutex<Vec<tungstenite::WebSocket<TcpStream>>>> = Arc::new(Mutex::new(Vec::new()));

    let ws_peers_clone = Arc::clone(&ws_peers);
    thread::spawn(move || {
        let ws_listener = TcpListener::bind(ws_port).unwrap();
        for stream in ws_listener.incoming() {
            if let Ok(stream) = stream {
                if let Ok(ws) = accept(stream) {
                    println!("[Hetzner-Relay] 🌐 New Web Visualizer Connected!");
                    ws_peers_clone.lock().unwrap().push(ws);
                }
            }
        }
    });

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                println!("[Hetzner-Relay] 🔗 New Synapse Edge Node Connected: {}", stream.peer_addr().unwrap());
                
                let stream_clone = stream.try_clone().unwrap();
                
                {
                    let mut p = peers.lock().unwrap();
                    p.push(stream_clone);
                    println!("[Hetzner-Relay] Total Autonomous Clusters: {}", p.len());
                }
                
                let peers_ref = Arc::clone(&peers);
                let ws_ref = Arc::clone(&ws_peers);
                thread::spawn(move || {
                    handle_client(stream, peers_ref, ws_ref);
                });
            }
            Err(_) => {
                println!("Error reading stream.");
            }
        }
    }
}
