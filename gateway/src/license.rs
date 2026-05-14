use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};

// Public Key matching the ones generated in license_generator.py
pub const SYNAPSE_PUBLIC_KEY: [u8; 32] = [
    85, 4, 65, 6, 13, 229, 76, 117, 254, 149, 60, 233, 89, 62, 130, 245, 215, 165, 104, 168, 196, 139, 176, 182, 146, 44, 238, 129, 158, 146, 180, 162
];

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct LicenseInfo {
    pub company_name: String,
    pub tier: String,
    pub expires_at: u64,
}

#[derive(Serialize, Deserialize, Debug)]
struct LicenseCert {
    payload: String,
    signature: String,
}

#[derive(Debug, Clone)]
pub enum LicenseStatus {
    Valid(LicenseInfo),
    Expired(LicenseInfo),
    EdgeCell, // Graceful degradation to Free tier
    Invalid(String),
}

impl LicenseStatus {
    pub fn is_pro(&self) -> bool {
        match self {
            LicenseStatus::Valid(info) if info.tier == "Pro" || info.tier == "Scale" || info.tier == "Enterprise" => true,
            _ => false,
        }
    }
}

pub fn validate_license(cert_env: Option<String>) -> LicenseStatus {
    let cert_str = match cert_env {
        Some(s) if !s.trim().is_empty() => s,
        _ => return LicenseStatus::EdgeCell, // No license provided, graceful degradation
    };

    let cert: LicenseCert = match serde_json::from_str(&cert_str) {
        Ok(c) => c,
        Err(_) => return LicenseStatus::Invalid("Malformed license JSON format".to_string()),
    };

    let sig_bytes = match base64::Engine::decode(&base64::engine::general_purpose::STANDARD, &cert.signature) {
        Ok(b) => b,
        Err(_) => return LicenseStatus::Invalid("Invalid base64 signature".to_string()),
    };

    let signature = match Signature::from_slice(&sig_bytes) {
        Ok(s) => s,
        Err(_) => return LicenseStatus::Invalid("Invalid signature length".to_string()),
    };

    let verifying_key = match VerifyingKey::from_bytes(&SYNAPSE_PUBLIC_KEY) {
        Ok(k) => k,
        Err(_) => return LicenseStatus::Invalid("System public key load failed".to_string()),
    };

    // Verify signature over the base64 payload string
    if let Err(_) = verifying_key.verify(cert.payload.as_bytes(), &signature) {
        return LicenseStatus::Invalid("Cryptographic signature validation failed".to_string());
    }

    // Decode Payload
    let payload_bytes = match base64::Engine::decode(&base64::engine::general_purpose::STANDARD, &cert.payload) {
        Ok(b) => b,
        Err(_) => return LicenseStatus::Invalid("Invalid base64 payload".to_string()),
    };

    let info: LicenseInfo = match serde_json::from_slice(&payload_bytes) {
        Ok(i) => i,
        Err(_) => return LicenseStatus::Invalid("Malformed payload JSON".to_string()),
    };

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    if now > info.expires_at {
        return LicenseStatus::Expired(info);
    }

    LicenseStatus::Valid(info)
}
