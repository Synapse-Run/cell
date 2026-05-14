/**
 * mesh_sharding.ts
 * 
 * Atlantic Shared Compute — Browser WebRTC Sharding Runtime.
 * Manages peer-to-peer data channels for securely handing off
 * Float32Array hidden states across node boundaries.
 * Features strict 16KB SCTP payload chunking to support multi-trillion
 * parameter dense networks gracefully over UDP.
 */

import { LayerReceipt } from './atlantic_shared_compute.js';

const CHUNK_SIZE = 16384; // 16 KB safety bound for WebRTC

export interface MeshMessage {
    type: 'state_handoff';
    shard_id: string;
    token_idx: number;
    receipt: LayerReceipt;
    total_chunks: number;
    chunk_idx: number;
    payload: ArrayBuffer; // The physical Float32Array bytes for this chunk
}

export type MeshCallback = (token_idx: number, state: Float32Array, receipt: LayerReceipt) => void;

export class MeshNetwork {
    private ws: WebSocket;
    private peerConnections: Map<string, RTCPeerConnection> = new Map();
    private dataChannels: Map<string, RTCDataChannel> = new Map();
    private nodeId: string;
    private onStateReceived?: MeshCallback;
    
    // Chunk reassembly buffer: message_id -> array of ArrayBuffers
    private chunkBuffers: Map<string, ArrayBuffer[]> = new Map();

    constructor(signalingUrl: string, onState: MeshCallback) {
        this.nodeId = "mesh_" + Math.random().toString(36).substring(7);
        this.onStateReceived = onState;
        
        console.log(`[Mesh] Initializing Atlantic Node: ${this.nodeId}`);
        this.ws = new WebSocket(signalingUrl);
        
        this.ws.onmessage = async (event) => {
            const msg = JSON.parse(event.data);
            const sender = msg._sender;
            
            if (msg.type === 'fleet_update') {
                console.log(`[Mesh] Fleet Update: ${msg.count} nodes connected`);
                for (const node of msg.nodes) {
                    if (node !== this.nodeId && !this.peerConnections.has(node)) {
                        this.initiatePeer(node);
                    }
                }
            } else if (msg.offer && sender) {
                await this.handleOffer(sender, msg.offer);
            } else if (msg.answer && sender) {
                const pc = this.peerConnections.get(sender);
                if (pc) await pc.setRemoteDescription(new RTCSessionDescription(msg.answer));
            } else if (msg.candidate && sender) {
                const pc = this.peerConnections.get(sender);
                if (pc) await pc.addIceCandidate(new RTCIceCandidate(msg.candidate));
            }
        };
    }

    private createPC(peerId: string) {
        const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
        this.peerConnections.set(peerId, pc);

        pc.onicecandidate = (e) => {
            if (e.candidate) {
                this.ws.send(JSON.stringify({ target: peerId, candidate: e.candidate }));
            }
        };

        pc.ondatachannel = (e) => this.setupDataChannel(peerId, e.channel);
        return pc;
    }

    private async initiatePeer(peerId: string) {
        const pc = this.createPC(peerId);
        const dc = pc.createDataChannel('atlantic_handoff');
        this.setupDataChannel(peerId, dc);

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        this.ws.send(JSON.stringify({ target: peerId, offer }));
    }

    private async handleOffer(peerId: string, offer: any) {
        let pc = this.peerConnections.get(peerId);
        if (!pc) pc = this.createPC(peerId);

        await pc.setRemoteDescription(new RTCSessionDescription(offer));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        this.ws.send(JSON.stringify({ target: peerId, answer }));
    }

    private setupDataChannel(peerId: string, dc: RTCDataChannel) {
        dc.binaryType = 'arraybuffer';
        dc.onopen = () => {
            console.log(`[Mesh] DataChannel opened with Peer ${peerId}`);
            this.dataChannels.set(peerId, dc);
        };
        dc.onmessage = (e) => this.handleDataChannelMessage(peerId, e.data);
    }

    private handleDataChannelMessage(peerId: string, data: ArrayBuffer) {
        // Unpack chunked payload
        // Format: JSON metadata strings followed by binary buffer separator \0\xAA\0
        // Wait, transferring objects + binary data over datachannels purely
        
        // Let's decode custom packet format
        const view = new Uint8Array(data);
        let separatorIdx = -1;
        for (let i = 0; i < view.length - 2; i++) {
            if (view[i] === 0 && view[i+1] === 170 && view[i+2] === 0) {
                separatorIdx = i;
                break;
            }
        }
        
        if (separatorIdx === -1) return; // Invalid format
        
        const jsonStr = new TextDecoder().decode(view.subarray(0, separatorIdx));
        const meta = JSON.parse(jsonStr) as Omit<MeshMessage, 'payload'>;
        const binaryPayload = data.slice(separatorIdx + 3);

        const msgId = `${meta.receipt.shard_id}_t${meta.token_idx}`;

        if (!this.chunkBuffers.has(msgId)) {
            this.chunkBuffers.set(msgId, new Array(meta.total_chunks));
        }
        
        const chunks = this.chunkBuffers.get(msgId)!;
        chunks[meta.chunk_idx] = binaryPayload;

        // Check completion
        let complete = true;
        for (let i = 0; i < meta.total_chunks; i++) {
            if (!chunks[i]) complete = false;
        }

        if (complete) {
            // Reassemble
            const totalLength = chunks.reduce((acc, c) => acc + c.byteLength, 0);
            const combinedBuffer = new Uint8Array(totalLength);
            let offset = 0;
            for (const chunk of chunks) {
                combinedBuffer.set(new Uint8Array(chunk), offset);
                offset += chunk.byteLength;
            }
            
            const stateArray = new Float32Array(combinedBuffer.buffer);
            this.chunkBuffers.delete(msgId);
            
            if (this.onStateReceived) {
                this.onStateReceived(meta.token_idx, stateArray, meta.receipt);
            }
        }
    }

    /**
     * Broadcast a hidden state to the next shard, splitting it into 16KB UDP chunks.
     */
    public broadcastState(tokenIdx: number, state: Float32Array, receipt: LayerReceipt) {
        // We broadcast to all connected peers for simplicity.
        // A production run would specifically target the peer holding the next `layer_start`.
        
        const stateBytes = state.buffer.slice(state.byteOffset, state.byteOffset + state.byteLength);
        const totalChunks = Math.ceil(stateBytes.byteLength / CHUNK_SIZE);
        
        for (const [peerId, dc] of this.dataChannels.entries()) {
            if (dc.readyState !== 'open') continue;

            for (let i = 0; i < totalChunks; i++) {
                const chunkStart = i * CHUNK_SIZE;
                const chunkEnd = Math.min((i + 1) * CHUNK_SIZE, stateBytes.byteLength);
                const chunkData = stateBytes.slice(chunkStart, chunkEnd);

                const meta = {
                    type: 'state_handoff',
                    shard_id: receipt.shard_id,
                    token_idx: tokenIdx,
                    receipt,
                    total_chunks: totalChunks,
                    chunk_idx: i
                };

                const metaBytes = new TextEncoder().encode(JSON.stringify(meta));
                const separator = new Uint8Array([0, 170, 0]);
                
                // Pack: [JSON Metadata] + [Separator] + [Binary Payload]
                const packet = new Uint8Array(metaBytes.byteLength + 3 + chunkData.byteLength);
                packet.set(metaBytes, 0);
                packet.set(separator, metaBytes.byteLength);
                packet.set(new Uint8Array(chunkData), metaBytes.byteLength + 3);

                dc.send(packet.buffer);
            }
        }
    }
}
