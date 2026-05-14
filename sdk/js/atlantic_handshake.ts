/**
 * Atlantic Handshake — Ed25519 Lease Enforcement
 * 
 * This module implements the cryptographic license gate for Synapse Cell Tier 2+.
 * Without a valid lease, the compiler.wasm binary refuses to compile .syn programs
 * that use Heavy FFI imports (load_weights, gpu_matmul, etc).
 * 
 * Architecture:
 *   1. Developer installs @runsynapse/cell and sets SYNAPSE_LICENSE_KEY env var
 *   2. On first use, SDK calls home to fetch a signed lease (valid 30 days)
 *   3. Lease is Ed25519-signed JSON: { tier, expires, node_id, features[] }
 *   4. compiler.wasm validates the signature offline using the embedded public key
 *   5. If lease is expired/missing/tampered, Heavy FFI imports are blocked
 * 
 * Security properties:
 *   - Offline validation: no phone-home after initial lease fetch
 *   - Wasm binary is stripped: no readable strings, no debuggable code paths
 *   - Ed25519 is 128-bit security: forging a signature requires breaking discrete log
 *   - Lease is per-node (SHA256 of hardware fingerprint) to prevent sharing
 * 
 * The free tier (Tier 1) always works — basic math/logic .syn programs compile
 * without any lease. Only Heavy FFI features are gated.
 */

import * as crypto from 'crypto';

// ─── Synapse Licensing Authority Public Key ────────────────────────
// This key is embedded in compiler.wasm and the SDK.
// The private key is held ONLY on the Synapse licensing server.
// 
// To generate a new keypair (one-time, on your secure machine):
//   const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
//
const SYNAPSE_PUBLIC_KEY_HEX = 'MCowBQYDK2VwAyEA'; // Placeholder — generate real key at deploy time

// ─── Lease Structure ───────────────────────────────────────────────
export interface SynapseLease {
    version: 1;
    tier: 'free' | 'pro' | 'enterprise';
    node_id: string;       // SHA256(hostname + mac + cpu_model)
    issued_at: number;     // Unix timestamp
    expires_at: number;    // Unix timestamp (issued + 30 days)
    features: string[];    // ['load_weights', 'gpu_matmul', 'gpu_softmax', ...]
    signature: string;     // Ed25519 signature over the payload (hex)
}

// ─── Tier Feature Gates ────────────────────────────────────────────
const TIER_FEATURES: Record<string, string[]> = {
    free: [],  // No FFI features — basic sandbox only
    pro: [
        'load_weights', 'weight_info',
        'gpu_matmul', 'gpu_relu', 'gpu_softmax', 'gpu_silu', 'gpu_layernorm', 'gpu_add',
        'ffi_numpy_dot',
        'tcp_connect', 'tcp_send', 'tcp_recv', 'tcp_bind', 'tcp_accept',
    ],
    enterprise: [
        // All pro features plus:
        'load_weights', 'weight_info',
        'gpu_matmul', 'gpu_relu', 'gpu_softmax', 'gpu_silu', 'gpu_layernorm', 'gpu_add',
        'ffi_numpy_dot',
        'tcp_connect', 'tcp_send', 'tcp_recv', 'tcp_bind', 'tcp_accept',
        'crypto_sign', 'crypto_verify',
        'p2p_request_compute', 'p2p_receive_compute', 'p2p_respond_compute', 'p2p_receive',
    ],
};

// Heavy FFI imports that require a valid Tier 2+ lease
const GATED_IMPORTS = new Set([
    'load_weights', 'weight_info',
    'gpu_matmul', 'gpu_relu', 'gpu_softmax', 'gpu_silu', 'gpu_layernorm', 'gpu_add',
    'ffi_numpy_dot',
    'tcp_connect', 'tcp_send', 'tcp_recv', 'tcp_bind', 'tcp_accept',
    'crypto_sign', 'crypto_verify',
    'p2p_request_compute', 'p2p_receive_compute', 'p2p_respond_compute', 'p2p_receive',
]);

// ─── Node Fingerprint ──────────────────────────────────────────────
export function computeNodeId(): string {
    const os = require('os');
    const raw = [
        os.hostname(),
        os.cpus()[0]?.model || 'unknown',
        os.arch(),
        os.platform(),
        // MAC address from first non-internal interface
        ...Object.values(os.networkInterfaces() as Record<string, any[]>)
            .flat()
            .filter((iface: any) => !iface.internal && iface.mac !== '00:00:00:00:00:00')
            .map((iface: any) => iface.mac)
            .slice(0, 1)
    ].join('|');
    
    return crypto.createHash('sha256').update(raw).digest('hex').slice(0, 32);
}

// ─── Lease Validation ──────────────────────────────────────────────
export function validateLease(lease: SynapseLease, publicKeyPem: string): { valid: boolean; reason?: string } {
    // 1. Check expiry
    const now = Math.floor(Date.now() / 1000);
    if (now > lease.expires_at) {
        return { valid: false, reason: `Lease expired ${Math.floor((now - lease.expires_at) / 86400)} days ago` };
    }

    // 2. Check node binding
    const currentNode = computeNodeId();
    if (lease.node_id !== currentNode) {
        return { valid: false, reason: 'Lease bound to different node' };
    }

    // 3. Verify Ed25519 signature
    const payload = JSON.stringify({
        version: lease.version,
        tier: lease.tier,
        node_id: lease.node_id,
        issued_at: lease.issued_at,
        expires_at: lease.expires_at,
        features: lease.features,
    });

    try {
        const keyObject = crypto.createPublicKey({
            key: Buffer.from(publicKeyPem, 'base64'),
            format: 'der',
            type: 'spki',
        });

        const signatureBuffer = Buffer.from(lease.signature, 'hex');
        const valid = crypto.verify(null, Buffer.from(payload), keyObject, signatureBuffer);
        
        if (!valid) {
            return { valid: false, reason: 'Invalid signature — lease has been tampered with' };
        }
    } catch (e) {
        return { valid: false, reason: `Signature verification failed: ${e}` };
    }

    return { valid: true };
}

// ─── FFI Gate Check ────────────────────────────────────────────────
// Called by the compiler/runtime before linking each FFI import
export function checkFeatureAccess(ffiName: string, lease: SynapseLease | null): { allowed: boolean; reason?: string } {
    // Free-tier features are always allowed
    if (!GATED_IMPORTS.has(ffiName)) {
        return { allowed: true };
    }

    // Gated feature — need a valid lease
    if (!lease) {
        return { 
            allowed: false, 
            reason: `'${ffiName}' requires Synapse Pro. Set SYNAPSE_LICENSE_KEY or visit https://synapserun.dev/pricing` 
        };
    }

    // Check if the lease's tier includes this feature
    if (!lease.features.includes(ffiName)) {
        return { 
            allowed: false, 
            reason: `'${ffiName}' not included in your ${lease.tier} tier. Upgrade at https://synapserun.dev/pricing` 
        };
    }

    return { allowed: true };
}

// ─── Lease Signing (SERVER-SIDE ONLY — never ship this) ────────────
// This function runs on the Synapse licensing server when a developer
// activates their license key. It generates a 30-day offline lease.
export function signLease(
    privateKeyPem: string, 
    tier: 'free' | 'pro' | 'enterprise',
    nodeId: string
): SynapseLease {
    const now = Math.floor(Date.now() / 1000);
    const lease: Omit<SynapseLease, 'signature'> = {
        version: 1,
        tier,
        node_id: nodeId,
        issued_at: now,
        expires_at: now + (30 * 24 * 60 * 60), // 30 days
        features: TIER_FEATURES[tier] || [],
    };

    const payload = JSON.stringify(lease);
    
    const keyObject = crypto.createPrivateKey({
        key: Buffer.from(privateKeyPem, 'base64'),
        format: 'der',
        type: 'pkcs8',
    });

    const signature = crypto.sign(null, Buffer.from(payload), keyObject);

    return {
        ...lease,
        signature: signature.toString('hex'),
    };
}

// ─── Self-Test ─────────────────────────────────────────────────────
if (require.main === module) {
    console.log("═══════════════════════════════════════════════════");
    console.log("  Atlantic Handshake — License Enforcement Test");
    console.log("═══════════════════════════════════════════════════\n");

    // Generate a test keypair
    const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
    const pubDer = publicKey.export({ format: 'der', type: 'spki' }).toString('base64');
    const privDer = privateKey.export({ format: 'der', type: 'pkcs8' }).toString('base64');

    const nodeId = computeNodeId();
    console.log(`Node ID: ${nodeId}`);
    
    // Sign a Pro lease
    const lease = signLease(privDer, 'pro', nodeId);
    console.log(`Lease signed: tier=${lease.tier}, expires=${new Date(lease.expires_at * 1000).toISOString()}`);
    console.log(`Features: [${lease.features.join(', ')}]`);
    console.log(`Signature: ${lease.signature.slice(0, 32)}...`);

    // Validate it
    const result = validateLease(lease, pubDer);
    console.log(`\nValidation: ${result.valid ? '✅ VALID' : '❌ ' + result.reason}`);

    // Test feature gates
    console.log("\nFeature Gate Tests:");
    const tests = ['print', 'load_weights', 'gpu_matmul', 'tcp_connect', 'crypto_sign'];
    for (const feat of tests) {
        const access = checkFeatureAccess(feat, lease);
        console.log(`  ${feat}: ${access.allowed ? '✅ allowed' : '❌ ' + access.reason}`);
    }

    // Test with no lease (free tier)
    console.log("\nFree Tier (no lease):");
    for (const feat of tests) {
        const access = checkFeatureAccess(feat, null);
        console.log(`  ${feat}: ${access.allowed ? '✅ allowed' : '🔒 ' + access.reason}`);
    }

    // Test tampered lease
    console.log("\nTamper Test:");
    const tampered = { ...lease, tier: 'enterprise' as const };
    const tamperResult = validateLease(tampered, pubDer);
    console.log(`  Tampered tier: ${tamperResult.valid ? '❌ SHOULD HAVE FAILED' : '✅ Detected: ' + tamperResult.reason}`);
}
