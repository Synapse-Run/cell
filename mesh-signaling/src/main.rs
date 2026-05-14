//! Atlantic Handshake Signaling Server
//! =====================================
//!
//! WebSocket signaling server that pairs with the existing mesh client code in:
//!   - cell/sdk/js/mesh_sharding.js  (browser / WASM WebRTC mesh client)
//!   - consume/synapse-wasm/index.html  (Qwen Wasm inference engine + broadcastState)
//!
//! Responsibilities (intentionally minimal — signaling is a commodity):
//!   1. Accept WebSocket connections on a configurable port (default 9011).
//!   2. Assign each peer a UUID, track the live set.
//!   3. On join/leave, broadcast `{ type: "fleet_update", peers: [...] }`
//!      to every connected peer. This lets the existing JS mesh client
//!      enumerate reachable peers and initiate WebRTC handshakes.
//!   4. Relay WebRTC signaling envelopes (`webrtc_offer`, `webrtc_answer`,
//!      `ice_candidate`) from source peer to the `to` peer. Server never
//!      inspects SDP / ICE payloads — acts as a dumb relay. Peers do the
//!      actual WebRTC handshake and then exchange data over direct RTC
//!      DataChannels, never again touching the signaling server.
//!   5. Relay `state_handoff` control messages between peers for mesh
//!      compute coordination (a thin control plane — the actual hidden
//!      state chunks flow over WebRTC DataChannels, not here).
//!
//! Scope explicitly NOT covered by this binary:
//!   - Receipt chain verification (lives in the gateway alongside Cell exec)
//!   - Compute distribution logic (layer_start routing — lives in the JS
//!     client when it decides WHICH peer to hand off to)
//!   - Authentication / TLS (add a reverse proxy for TLS; for LAN-only
//!     deployments this is fine as-is)
//!   - Persistence (peer set is in-memory only — restart clears state,
//!     which is correct: peers rediscover via fresh fleet_update)
//!
//! Protocol summary:
//!
//!     ┌─────────┐   WebSocket                 ┌─────────┐
//!     │ peer A  │ ─────────────────────────▶  │ server  │
//!     │         │     "hello"                 │         │
//!     │         │  ◀── "assigned {uuid: A1}"  │         │
//!     │         │                             │         │
//!     │ (peer B connects) ─────────────────▶ │         │
//!     │         │  ◀── "fleet_update          │         │
//!     │         │        {peers: [A1, B1]}"   │         │
//!     │         │                             │         │
//!     │         │     {type: "webrtc_offer",  │         │
//!     │         │      to: "B1", sdp: ...}    │         │
//!     │         │  ───────────────────────▶   │         │
//!     │         │                             │   ──▶ B │
//!     │         │                             │         │
//!     └─────────┘                             └─────────┘
//!
//! After offer/answer/ICE exchange, A and B establish a direct WebRTC
//! DataChannel and the mesh state broadcast flows peer-to-peer
//! (no further server involvement).
//!
//! Run:
//!     cargo run -p mesh-signaling --release -- --port 9011
//!
//! Or systemd:
//!     [Service]
//!     ExecStart=/opt/synapse/bin/mesh-signaling-server --port 9011 --bind 0.0.0.0

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;

use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, RwLock};
use tokio_tungstenite::{accept_async, tungstenite::Message};
use tracing::{error, info, warn};
use uuid::Uuid;

/// Outgoing message to a connected peer.
type PeerSender = mpsc::UnboundedSender<Message>;

/// The live peer registry. Keyed by peer UUID (string form for easy JSON).
type PeerRegistry = Arc<RwLock<HashMap<String, PeerSender>>>;

/// Protocol envelopes. We use untagged + flexible payloads so the server
/// can relay without needing to understand every message type.
#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "type")]
enum PeerMessage {
    /// Relayed WebRTC offer/answer/ICE candidate: `{ to: "peer-uuid", ... }`
    #[serde(rename = "webrtc_offer")]
    WebrtcOffer { to: String, #[serde(flatten)] extra: Value },
    #[serde(rename = "webrtc_answer")]
    WebrtcAnswer { to: String, #[serde(flatten)] extra: Value },
    #[serde(rename = "ice_candidate")]
    IceCandidate { to: String, #[serde(flatten)] extra: Value },

    /// Mesh compute control-plane: relay to target peer.
    #[serde(rename = "state_handoff")]
    StateHandoff { to: String, #[serde(flatten)] extra: Value },
}

/// Server → peer announcements.
#[derive(Debug, Serialize)]
#[serde(tag = "type")]
enum ServerAnnouncement<'a> {
    #[serde(rename = "assigned")]
    Assigned { uuid: &'a str },
    #[serde(rename = "fleet_update")]
    FleetUpdate { peers: Vec<String> },
}

fn parse_args() -> (SocketAddr, String) {
    let mut port: u16 = 9011;
    let mut bind: String = "127.0.0.1".to_string();
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--port" => { port = args[i + 1].parse().unwrap_or(9011); i += 2; }
            "--bind" => { bind = args[i + 1].clone(); i += 2; }
            "-h" | "--help" => {
                println!("Usage: mesh-signaling-server [--port 9011] [--bind 127.0.0.1]");
                std::process::exit(0);
            }
            _ => { i += 1; }
        }
    }
    let addr: SocketAddr = format!("{}:{}", bind, port).parse().expect("bad bind addr");
    (addr, bind)
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env()
            .add_directive(tracing::Level::INFO.into()))
        .init();

    let (addr, bind) = parse_args();
    let listener = TcpListener::bind(addr).await?;
    info!(%addr, bind = %bind, "signaling server listening");

    let peers: PeerRegistry = Arc::new(RwLock::new(HashMap::new()));

    loop {
        let (stream, remote) = match listener.accept().await {
            Ok(v) => v,
            Err(e) => { warn!(?e, "accept failed"); continue; }
        };
        let peers = peers.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_peer(stream, remote, peers).await {
                warn!(?e, %remote, "peer handler exited with error");
            }
        });
    }
}

async fn handle_peer(
    stream: TcpStream,
    remote: SocketAddr,
    peers: PeerRegistry,
) -> anyhow::Result<()> {
    let ws = accept_async(stream).await?;
    let peer_id = Uuid::new_v4().to_string();
    info!(%peer_id, %remote, "peer connected");

    let (mut ws_sink, mut ws_stream) = ws.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<Message>();

    // Register this peer.
    {
        let mut reg = peers.write().await;
        reg.insert(peer_id.clone(), tx.clone());
    }

    // Tell this peer its UUID.
    let assigned = serde_json::to_string(&ServerAnnouncement::Assigned { uuid: &peer_id })?;
    tx.send(Message::Text(assigned)).ok();

    // Broadcast updated fleet to everyone (including this new peer).
    broadcast_fleet(&peers).await;

    // Spawn outbound writer (drains rx, writes to WebSocket).
    let peer_id_writer = peer_id.clone();
    let writer = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if ws_sink.send(msg).await.is_err() {
                break;
            }
        }
        tracing::debug!(peer_id = %peer_id_writer, "writer closed");
    });

    // Inbound loop: parse client messages and relay.
    while let Some(incoming) = ws_stream.next().await {
        let msg = match incoming {
            Ok(m) => m,
            Err(e) => { warn!(?e, %peer_id, "ws recv error"); break; }
        };
        match msg {
            Message::Text(text) => relay_or_log(&text, &peer_id, &peers).await,
            Message::Binary(_) => { /* signaling is text-only by convention */ }
            Message::Ping(data) => { tx.send(Message::Pong(data)).ok(); }
            Message::Close(_) => { info!(%peer_id, "peer closed cleanly"); break; }
            _ => {}
        }
    }

    // Unregister + announce updated fleet.
    {
        let mut reg = peers.write().await;
        reg.remove(&peer_id);
    }
    info!(%peer_id, "peer disconnected");
    broadcast_fleet(&peers).await;

    let _ = writer.await;
    Ok(())
}

/// Parse a text message, route it to the target peer. If the message doesn't
/// match any known envelope, it's silently ignored (future-proof: new
/// message types can be added without updating this server).
async fn relay_or_log(text: &str, from_peer: &str, peers: &PeerRegistry) {
    let parsed: Result<PeerMessage, _> = serde_json::from_str(text);
    match parsed {
        Ok(PeerMessage::WebrtcOffer { to, extra })
        | Ok(PeerMessage::WebrtcAnswer { to, extra })
        | Ok(PeerMessage::IceCandidate { to, extra })
        | Ok(PeerMessage::StateHandoff { to, extra }) => {
            // Re-inject the "from" field so the receiver knows which peer sent it.
            let mut payload = extra;
            if let Value::Object(ref mut map) = payload {
                map.insert("from".to_string(), Value::String(from_peer.to_string()));
            }
            // Preserve the original type tag by round-tripping through the parsed form.
            let out = match serde_json::from_str::<Value>(text) {
                Ok(mut v) => {
                    if let Value::Object(ref mut m) = v {
                        m.insert("from".to_string(), Value::String(from_peer.to_string()));
                    }
                    v
                }
                Err(_) => payload,
            };
            let serialized = out.to_string();
            let reg = peers.read().await;
            if let Some(target_tx) = reg.get(&to) {
                target_tx.send(Message::Text(serialized)).ok();
            } else {
                warn!(%from_peer, %to, "target peer not in registry");
            }
        }
        Err(e) => {
            tracing::debug!(?e, text = %text, "unparseable signaling message (ignored)");
        }
    }
}

/// Build the current fleet list and push to every connected peer.
async fn broadcast_fleet(peers: &PeerRegistry) {
    let reg = peers.read().await;
    let ids: Vec<String> = reg.keys().cloned().collect();
    let ann = match serde_json::to_string(&ServerAnnouncement::FleetUpdate { peers: ids.clone() }) {
        Ok(s) => s,
        Err(e) => { error!(?e, "serialize fleet_update failed"); return; }
    };
    for (_peer_id, tx) in reg.iter() {
        tx.send(Message::Text(ann.clone())).ok();
    }
}
