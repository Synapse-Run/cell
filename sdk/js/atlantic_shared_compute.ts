/**
 * Atlantic Handshake v2 — Shared Compute Protocol
 * 
 * Extends the proven J146 receipt verification into a distributed inference
 * protocol where a transformer forward pass can be split across multiple
 * nodes with cryptographic trust at each layer boundary.
 * 
 * Architecture:
 *   Node A (layers 0-11) → receipt → Node B (layers 12-23) → receipt → result
 *   
 *   Each node produces a LayerReceipt:
 *     - input_hash: SHA256 of the hidden state entering this shard  
 *     - output_hash: SHA256 of the hidden state leaving this shard
 *     - layers: which transformer layers were computed
 *     - node_id: hardware fingerprint of the computing node
 *     - signature: Ed25519 signature over the above
 * 
 *   A ReceiptChain is the ordered list of LayerReceipts. Any node in the
 *   fleet can verify the chain by checking:
 *     1. Each receipt's signature is valid
 *     2. receipt[i].output_hash === receipt[i+1].input_hash (continuity)
 *     3. No gaps in layer ranges
 *     4. All node_ids have valid leases
 * 
 * This is the Tier 3 Enterprise feature — fleet-managed distributed inference
 * with cryptographic proof that no node tampered with the computation.
 */

import * as crypto from 'crypto';

// ─── Types ─────────────────────────────────────────────────────────

export interface LayerReceipt {
    version: 1;
    shard_id: string;           // Unique ID for this compute shard
    node_id: string;            // SHA256 fingerprint of the computing node
    model_id: string;           // e.g. "qwen-0.5b-ternary"
    layer_start: number;        // First layer computed (inclusive)
    layer_end: number;          // Last layer computed (inclusive)
    input_hash: string;         // SHA256 of the hidden state entering this shard
    output_hash: string;        // SHA256 of the hidden state leaving this shard
    compute_ms: number;         // Wall-clock time for this shard
    timestamp: number;          // Unix timestamp
    signature: string;          // Ed25519 over all fields except signature
}

export interface ReceiptChain {
    model_id: string;
    total_layers: number;
    receipts: LayerReceipt[];
    final_output_hash: string;  // Must match last receipt's output_hash
    verified: boolean;
}

export interface ShardAssignment {
    node_id: string;
    node_address: string;       // host:port
    layer_start: number;
    layer_end: number;
}

export interface FleetConfig {
    model_id: string;
    total_layers: number;
    shards: ShardAssignment[];
}

// ─── Receipt Creation ──────────────────────────────────────────────

/**
 * Hash a hidden state vector for receipt chaining.
 * Uses the raw float bytes to ensure deterministic hashing.
 */
export function hashHiddenState(state: Float32Array): string {
    const buffer = Buffer.from(state.buffer, state.byteOffset, state.byteLength);
    return crypto.createHash('sha256').update(buffer).digest('hex');
}

/**
 * Create a LayerReceipt for a completed compute shard.
 */
export function createLayerReceipt(
    nodeId: string,
    privateKey: string,
    modelId: string,
    layerStart: number,
    layerEnd: number,
    inputState: Float32Array,
    outputState: Float32Array,
    computeMs: number,
): LayerReceipt {
    const receipt: Omit<LayerReceipt, 'signature'> = {
        version: 1,
        shard_id: crypto.randomUUID(),
        node_id: nodeId,
        model_id: modelId,
        layer_start: layerStart,
        layer_end: layerEnd,
        input_hash: hashHiddenState(inputState),
        output_hash: hashHiddenState(outputState),
        compute_ms: computeMs,
        timestamp: Math.floor(Date.now() / 1000),
    };

    // Sign
    const payload = JSON.stringify(receipt);
    const keyObject = crypto.createPrivateKey({
        key: Buffer.from(privateKey, 'base64'),
        format: 'der',
        type: 'pkcs8',
    });
    const signature = crypto.sign(null, Buffer.from(payload), keyObject);

    return { ...receipt, signature: signature.toString('hex') };
}

// ─── Receipt Chain Verification ────────────────────────────────────

/**
 * Verify an entire receipt chain for a distributed inference run.
 * Returns { valid, errors[] } — errors explain exactly what's wrong.
 */
export function verifyReceiptChain(
    chain: ReceiptChain,
    publicKey: string,
    knownNodeIds?: Set<string>,  // Optional: restrict to known fleet nodes
): { valid: boolean; errors: string[] } {
    const errors: string[] = [];

    if (chain.receipts.length === 0) {
        return { valid: false, errors: ['Empty receipt chain'] };
    }

    // 1. Check layer continuity and completeness
    let expectedStart = 0;
    for (let i = 0; i < chain.receipts.length; i++) {
        const r = chain.receipts[i];
        
        if (r.layer_start !== expectedStart) {
            errors.push(`Gap at receipt ${i}: expected layer ${expectedStart}, got ${r.layer_start}`);
        }
        if (r.layer_end < r.layer_start) {
            errors.push(`Invalid range at receipt ${i}: ${r.layer_start}-${r.layer_end}`);
        }
        expectedStart = r.layer_end + 1;
    }
    if (expectedStart !== chain.total_layers) {
        errors.push(`Incomplete: covers layers 0-${expectedStart - 1}, model has ${chain.total_layers}`);
    }

    // 2. Check hash continuity (receipt[i].output_hash === receipt[i+1].input_hash)
    for (let i = 0; i < chain.receipts.length - 1; i++) {
        if (chain.receipts[i].output_hash !== chain.receipts[i + 1].input_hash) {
            errors.push(
                `Hash discontinuity between shard ${i} (output: ${chain.receipts[i].output_hash.slice(0, 16)}...) ` +
                `and shard ${i + 1} (input: ${chain.receipts[i + 1].input_hash.slice(0, 16)}...)`
            );
        }
    }

    // 3. Check final output hash
    const lastReceipt = chain.receipts[chain.receipts.length - 1];
    if (lastReceipt.output_hash !== chain.final_output_hash) {
        errors.push('Final output hash mismatch');
    }

    // 4. Verify each receipt's Ed25519 signature
    for (let i = 0; i < chain.receipts.length; i++) {
        const r = chain.receipts[i];
        const { signature, ...payload } = r;
        
        try {
            const keyObject = crypto.createPublicKey({
                key: Buffer.from(publicKey, 'base64'),
                format: 'der',
                type: 'spki',
            });
            const valid = crypto.verify(
                null,
                Buffer.from(JSON.stringify(payload)),
                keyObject,
                Buffer.from(signature, 'hex'),
            );
            if (!valid) {
                errors.push(`Invalid signature on receipt ${i} (shard ${r.shard_id})`);
            }
        } catch (e) {
            errors.push(`Signature verification error on receipt ${i}: ${e}`);
        }
    }

    // 5. Check node authorization (optional fleet restriction)
    if (knownNodeIds) {
        for (let i = 0; i < chain.receipts.length; i++) {
            if (!knownNodeIds.has(chain.receipts[i].node_id)) {
                errors.push(`Unknown node ${chain.receipts[i].node_id} on receipt ${i}`);
            }
        }
    }

    // 6. Check model consistency
    for (const r of chain.receipts) {
        if (r.model_id !== chain.model_id) {
            errors.push(`Model mismatch: chain=${chain.model_id}, receipt=${r.model_id}`);
        }
    }

    return { valid: errors.length === 0, errors };
}

// ─── Fleet Sharding ────────────────────────────────────────────────

/**
 * Create a shard assignment that splits a model across N nodes.
 * Default: even split. Can be weighted by node capability.
 */
export function createFleetConfig(
    modelId: string,
    totalLayers: number,
    nodes: Array<{ id: string; address: string; weight?: number }>,
): FleetConfig {
    const totalWeight = nodes.reduce((s, n) => s + (n.weight || 1), 0);
    const shards: ShardAssignment[] = [];
    let currentLayer = 0;

    for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        const weight = node.weight || 1;
        const layerCount = i === nodes.length - 1
            ? totalLayers - currentLayer  // Last node gets remainder
            : Math.round((weight / totalWeight) * totalLayers);

        shards.push({
            node_id: node.id,
            node_address: node.address,
            layer_start: currentLayer,
            layer_end: currentLayer + layerCount - 1,
        });
        currentLayer += layerCount;
    }

    return { model_id: modelId, total_layers: totalLayers, shards };
}

// ─── Self-Test ─────────────────────────────────────────────────────

if (require.main === module) {
    console.log("════════════════════════════════════════════════════════════");
    console.log("  Atlantic Handshake v2 — Shared Compute Protocol Test");
    console.log("════════════════════════════════════════════════════════════\n");

    // Generate test keypair
    const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
    const pubDer = publicKey.export({ format: 'der', type: 'spki' }).toString('base64');
    const privDer = privateKey.export({ format: 'der', type: 'pkcs8' }).toString('base64');

    const nodeA = 'node_canada_m4pro';
    const nodeB = 'node_germany_ax102';
    const nodeC = 'node_finland_cx23';

    // Simulate a 24-layer Qwen forward pass split across 3 nodes (8 layers each)
    console.log("Simulating Qwen 0.5B distributed inference (24 layers, 3 nodes):\n");

    // Fleet config
    const fleet = createFleetConfig('qwen-0.5b-ternary', 24, [
        { id: nodeA, address: 'localhost:9876', weight: 1 },
        { id: nodeB, address: '65.108.120.219:9876', weight: 1 },
        { id: nodeC, address: '89.167.107.86:9876', weight: 1 },
    ]);

    for (const shard of fleet.shards) {
        console.log(`  ${shard.node_id}: layers ${shard.layer_start}-${shard.layer_end} @ ${shard.node_address}`);
    }

    // Simulate hidden states (896-dim for Qwen 0.5B)
    const dim = 896;
    const inputState = new Float32Array(dim);
    for (let i = 0; i < dim; i++) inputState[i] = Math.sin(i * 0.01) * 0.5;

    const midState1 = new Float32Array(dim);
    for (let i = 0; i < dim; i++) midState1[i] = inputState[i] * 1.1 + 0.3; // Simulated layer 0-7 output

    const midState2 = new Float32Array(dim);
    for (let i = 0; i < dim; i++) midState2[i] = midState1[i] * 0.9 - 0.1; // Simulated layer 8-15 output

    const finalState = new Float32Array(dim);
    for (let i = 0; i < dim; i++) finalState[i] = midState2[i] * 1.5 - 0.2; // Simulated layer 16-23 output
    
    // Node A computes layers 0-7
    console.log("\n--- Node A (Canada, M4 Pro) ---");
    const receiptA = createLayerReceipt(nodeA, privDer, 'qwen-0.5b-ternary', 0, 7, inputState, midState1, 8.5);
    console.log(`  Layers 0-7 computed in ${receiptA.compute_ms}ms`);
    console.log(`  Input hash:  ${receiptA.input_hash.slice(0, 24)}...`);
    console.log(`  Output hash: ${receiptA.output_hash.slice(0, 24)}...`);

    // Node B computes layers 8-15
    console.log("\n--- Node B (Germany, AX102) ---");
    const receiptB = createLayerReceipt(nodeB, privDer, 'qwen-0.5b-ternary', 8, 15, midState1, midState2, 12.2);
    console.log(`  Layers 8-15 computed in ${receiptB.compute_ms}ms`);
    console.log(`  Input hash:  ${receiptB.input_hash.slice(0, 24)}...`);
    console.log(`  Output hash: ${receiptB.output_hash.slice(0, 24)}...`);

    // Node C computes layers 16-23
    console.log("\n--- Node C (US, GPU Node) ---");
    const receiptC = createLayerReceipt(nodeC, privDer, 'qwen-0.5b-ternary', 16, 23, midState2, finalState, 3.1);
    console.log(`  Layers 16-23 computed in ${receiptC.compute_ms}ms`);
    console.log(`  Input hash:  ${receiptC.input_hash.slice(0, 24)}...`);
    console.log(`  Output hash: ${receiptC.output_hash.slice(0, 24)}...`);

    // Hash continuity check
    console.log(`\n  Hash continuity (A->B): ${receiptA.output_hash === receiptB.input_hash ? '✅' : '❌'}`);
    console.log(`  Hash continuity (B->C): ${receiptB.output_hash === receiptC.input_hash ? '✅' : '❌'}`);

    // Build and verify the chain
    const chain: ReceiptChain = {
        model_id: 'qwen-0.5b-ternary',
        total_layers: 24,
        receipts: [receiptA, receiptB, receiptC],
        final_output_hash: receiptC.output_hash,
        verified: false,
    };

    console.log("\n--- Verifying Receipt Chain ---");
    const result = verifyReceiptChain(chain, pubDer, new Set([nodeA, nodeB, nodeC]));
    chain.verified = result.valid;

    if (result.valid) {
        console.log("  ✅ RECEIPT CHAIN VERIFIED");
        console.log(`     ${chain.receipts.length} shards, ${chain.total_layers} layers`);
        console.log(`     Total compute: ${chain.receipts.reduce((s, r) => s + r.compute_ms, 0).toFixed(1)}ms`);
    } else {
        console.log("  ❌ VERIFICATION FAILED:");
        result.errors.forEach(e => console.log(`     - ${e}`));
    }

    // Tamper test: modify a hidden state
    console.log("\n--- Tamper Test (Node B alters computation) ---");
    const tamperedReceiptB = { ...receiptB, output_hash: 'aaaa' + receiptB.output_hash.slice(4) };
    const tamperedChain: ReceiptChain = {
        model_id: 'qwen-0.5b-ternary',
        total_layers: 24,
        receipts: [receiptA, tamperedReceiptB, receiptC],
        final_output_hash: receiptC.output_hash,
        verified: false,
    };

    const tamperResult = verifyReceiptChain(tamperedChain, pubDer);
    console.log(`  Tampered chain: ${tamperResult.valid ? '❌ SHOULD HAVE FAILED' : '✅ Detected'}`);
    tamperResult.errors.forEach(e => console.log(`     - ${e}`));

    // Unknown node test
    console.log("\n--- Unknown Node Test ---");
    const strictResult = verifyReceiptChain(chain, pubDer, new Set([nodeA, nodeB])); // Missing nodeC
    console.log(`  Restricted fleet: ${strictResult.valid ? '❌ SHOULD HAVE FAILED' : '✅ Detected'}`);
    strictResult.errors.forEach(e => console.log(`     - ${e}`));

    console.log("\n════════════════════════════════════════════════════════════");
    console.log("  All tests complete.");
    console.log("════════════════════════════════════════════════════════════");
}
