use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::cell::ExecutionReceipt;

pub const DEFAULT_LOCAL_MODEL_ALIAS: &str = "synapse-local-coder";
pub const DEFAULT_LOCAL_BACKEND_BASE_URL: &str = "http://127.0.0.1:8091";
pub const DEFAULT_LOCAL_TIMEOUT_SECS: u64 = 300;

fn default_model_alias() -> String {
    DEFAULT_LOCAL_MODEL_ALIAS.to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InferenceRequest {
    #[serde(default = "default_model_alias")]
    pub model: String,
    #[serde(default)]
    pub messages: Vec<ChatMessage>,
    #[serde(default)]
    pub prompt: Option<String>,
    #[serde(default)]
    pub max_tokens: Option<usize>,
    #[serde(default)]
    pub temperature: Option<f32>,
    #[serde(default)]
    pub enable_thinking: Option<bool>,
    #[serde(default)]
    pub route_hint: Option<String>,
    #[serde(default)]
    pub metadata: Option<serde_json::Value>,
}

impl Default for InferenceRequest {
    fn default() -> Self {
        Self {
            model: default_model_alias(),
            messages: Vec::new(),
            prompt: None,
            max_tokens: None,
            temperature: None,
            enable_thinking: Some(false),
            route_hint: None,
            metadata: None,
        }
    }
}

impl InferenceRequest {
    pub fn from_prompt(prompt: &str) -> Self {
        Self {
            prompt: Some(prompt.to_string()),
            messages: vec![ChatMessage {
                role: "user".to_string(),
                content: prompt.to_string(),
            }],
            ..Self::default()
        }
    }

    pub fn canonical_prompt(&self) -> String {
        if let Some(prompt) = self
            .prompt
            .as_ref()
            .filter(|prompt| !prompt.trim().is_empty())
        {
            return prompt.clone();
        }
        self.messages
            .iter()
            .map(|message| format!("{}: {}", message.role, message.content))
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn into_backend_json(&self, config: &InferenceConfig) -> serde_json::Value {
        let mut payload = serde_json::Map::new();
        payload.insert(
            "model".to_string(),
            serde_json::Value::String(config.backend_request_model()),
        );
        payload.insert(
            "messages".to_string(),
            serde_json::to_value(&self.messages)
                .unwrap_or_else(|_| serde_json::Value::Array(vec![])),
        );
        // Disable streaming so the synchronous ureq response flow works
        // against the native QES Rust engine and compatible local wrappers.
        payload.insert("stream".to_string(), serde_json::Value::Bool(false));

        if let Some(max_tokens) = self.max_tokens {
            payload.insert("max_tokens".to_string(), serde_json::json!(max_tokens));
        }
        if let Some(temperature) = self.temperature {
            payload.insert("temperature".to_string(), serde_json::json!(temperature));
        }
        if let Some(enable_thinking) = self.enable_thinking {
            payload.insert(
                "enable_thinking".to_string(),
                serde_json::json!(enable_thinking),
            );
        }
        serde_json::Value::Object(payload)
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct InferenceBackendMetadata {
    pub route: String,
    pub logical_model_alias: String,
    pub backend_model: String,
    pub backend_base_url: String,
    pub backend_completion_url: String,
    pub backend_health_url: String,
    pub fallback_used: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct InferenceResponse {
    pub text: String,
    pub model: String,
    pub backend: InferenceBackendMetadata,
    pub latency_ms: u64,
    pub receipt: ExecutionReceipt,
}

#[derive(Debug, Clone)]
pub struct InferenceConfig {
    pub logical_model_alias: String,
    pub backend_base_url: String,
    pub backend_model: Option<String>,
    pub timeout: Duration,
}

impl Default for InferenceConfig {
    fn default() -> Self {
        let logical_model_alias = std::env::var("SYNAPSE_LOCAL_MODEL_ALIAS")
            .unwrap_or_else(|_| DEFAULT_LOCAL_MODEL_ALIAS.to_string());
        let backend_base_url = std::env::var("SYNAPSE_LOCAL_BACKEND_BASE_URL")
            .unwrap_or_else(|_| DEFAULT_LOCAL_BACKEND_BASE_URL.to_string());
        let backend_model = std::env::var("SYNAPSE_LOCAL_BACKEND_MODEL").ok();
        let timeout_secs = std::env::var("SYNAPSE_LOCAL_BACKEND_TIMEOUT_SECS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(DEFAULT_LOCAL_TIMEOUT_SECS);

        Self {
            logical_model_alias,
            backend_base_url: backend_base_url.trim_end_matches('/').to_string(),
            backend_model,
            timeout: Duration::from_secs(timeout_secs.max(1)),
        }
    }
}

impl InferenceConfig {
    pub fn backend_completion_url(&self) -> String {
        format!("{}/v1/chat/completions", self.backend_base_url)
    }

    pub fn backend_health_url(&self) -> String {
        format!("{}/health", self.backend_base_url)
    }

    pub fn backend_request_model(&self) -> String {
        self.backend_model
            .clone()
            .unwrap_or_else(|| self.logical_model_alias.clone())
    }
}

pub fn infer_prompt(prompt: &str) -> Result<String, String> {
    infer_local(&InferenceRequest::from_prompt(prompt)).map(|response| response.text)
}

pub fn infer_local(request: &InferenceRequest) -> Result<InferenceResponse, String> {
    let config = InferenceConfig::default();
    let started = Instant::now();
    let prompt = request.canonical_prompt();
    let payload = request.into_backend_json(&config);
    let completion_url = config.backend_completion_url();

    let response = ureq::post(&completion_url)
        .timeout(config.timeout)
        .send_json(payload);

    match response {
        Ok(resp) => {
            let json: serde_json::Value = resp
                .into_json()
                .map_err(|err| format!("Local backend returned invalid JSON: {}", err))?;
            let text = extract_completion_text(&json).ok_or_else(|| {
                "Local backend response missing choices[0].message.content".to_string()
            })?;
            let latency_ms = started.elapsed().as_millis() as u64;
            let receipt = ExecutionReceipt::new(&prompt, &text, "", &config.logical_model_alias);
            Ok(InferenceResponse {
                text,
                model: config.logical_model_alias.clone(),
                backend: InferenceBackendMetadata {
                    route: "local".to_string(),
                    logical_model_alias: config.logical_model_alias.clone(),
                    backend_model: config.backend_request_model(),
                    backend_base_url: config.backend_base_url.clone(),
                    backend_completion_url: completion_url,
                    backend_health_url: config.backend_health_url(),
                    fallback_used: false,
                },
                latency_ms,
                receipt,
            })
        }
        Err(ureq::Error::Status(status, resp)) => {
            let body = resp.into_string().unwrap_or_default();
            Err(format!("Local backend returned HTTP {}: {}", status, body))
        }
        Err(err) => Err(format!("Local backend request failed: {}", err)),
    }
}

fn extract_completion_text(json: &serde_json::Value) -> Option<String> {
    json.get("choices")
        .and_then(|choices| choices.as_array())
        .and_then(|choices| choices.first())
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(|content| match content {
            serde_json::Value::String(text) => Some(text.clone()),
            serde_json::Value::Array(parts) => {
                let text = parts
                    .iter()
                    .filter_map(|part| part.get("text").and_then(|value| value.as_str()))
                    .collect::<Vec<_>>()
                    .join("");
                if text.is_empty() {
                    None
                } else {
                    Some(text)
                }
            }
            _ => None,
        })
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{
        extract_completion_text, InferenceConfig, InferenceRequest, DEFAULT_LOCAL_MODEL_ALIAS,
    };

    #[test]
    fn canonical_prompt_prefers_explicit_prompt() {
        let request = InferenceRequest {
            prompt: Some("hello".to_string()),
            messages: vec![],
            ..InferenceRequest::default()
        };

        assert_eq!(request.canonical_prompt(), "hello");
    }

    #[test]
    fn canonical_prompt_falls_back_to_messages() {
        let request = InferenceRequest {
            prompt: None,
            messages: vec![
                super::ChatMessage {
                    role: "system".to_string(),
                    content: "sys".to_string(),
                },
                super::ChatMessage {
                    role: "user".to_string(),
                    content: "ask".to_string(),
                },
            ],
            ..InferenceRequest::default()
        };

        assert_eq!(request.canonical_prompt(), "system: sys\nuser: ask");
    }

    #[test]
    fn backend_json_uses_logical_alias_by_default() {
        let config = InferenceConfig {
            logical_model_alias: DEFAULT_LOCAL_MODEL_ALIAS.to_string(),
            backend_base_url: "http://127.0.0.1:8091".to_string(),
            backend_model: None,
            timeout: Duration::from_secs(30),
        };
        let request = InferenceRequest::from_prompt("hello");
        let payload = request.into_backend_json(&config);

        assert_eq!(payload["model"], DEFAULT_LOCAL_MODEL_ALIAS);
        assert_eq!(payload["messages"][0]["content"], "hello");
    }

    #[test]
    fn extract_completion_text_handles_openai_shape() {
        let json = serde_json::json!({
            "choices": [{
                "message": {
                    "content": "answer"
                }
            }]
        });

        assert_eq!(extract_completion_text(&json).as_deref(), Some("answer"));
    }
}
