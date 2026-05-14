"""
Synapse Cell — Moonshot Wasm Native Intelligence Shims
Provides Zero-Latency C-FFI bindings to bypass TCP layers inside the sandbox.
"""
import ctypes
import json
import os

class MoonshotLinkException(Exception):
    pass


DEFAULT_MODEL_ALIAS = os.environ.get("SYNAPSE_LOCAL_MODEL_ALIAS", "synapse-local-coder")
DEFAULT_FALLBACK_URL = os.environ.get(
    "SYNAPSE_INFER_URL",
    "http://127.0.0.1:8091/v1/chat/completions",
)

# Try to load the WASI environment host functions linked into the memory space by Wasmtime
def ask_native(prompt: str, max_tokens: int = 1024) -> str:
    """
    Query the natively mapped Synapse local coder synchronously without TCP bindings.
    """
    try:
        # Load the statically linked WASI env 
        # (This assumes the CPython environment is compiled allowing weak FFI linking logic)
        env = ctypes.CDLL(None) 
        
        # Pull the signature: (prompt_ptr: i32, prompt_len: i32, out_ptr: i32, out_max_len: i32) -> i32
        # FFI memory limits mapping
        synapse_infer = env.synapse_infer
        synapse_infer.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32]
        synapse_infer.restype = ctypes.c_uint32
        
        prompt_encoded = prompt.encode("utf-8")
        prompt_len = len(prompt_encoded)
        
        output_buffer = ctypes.create_string_buffer(4096)
        
        bytes_written = synapse_infer(
            prompt_encoded, 
            prompt_len, 
            output_buffer, 
            4096
        )
        
        if bytes_written == 0:
            raise MoonshotLinkException("Zero latency WASM bus returned 0 frames.")
            
        return output_buffer.value[:bytes_written].decode("utf-8")
        
    except AttributeError:
        # Fallback to HTTP for purely simulated cell layers or dev environments
        # using the local HTTP bridge
        return _ask_fallback(prompt)

def _ask_fallback(prompt: str) -> str:
    """Fallback if the CPython module was not correctly linked with --export-dynamic"""
    import urllib.request
    
    req_body = json.dumps({
        "model": DEFAULT_MODEL_ALIAS,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")
    
    req = urllib.request.Request(
        DEFAULT_FALLBACK_URL,
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            if "text" in data:
                return data["text"]
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Moonshot Binding Error: {e}"
