// ============================================================================
// Synapse Compiler — Rust-native .syn → Wasm bytecode compiler
// ============================================================================
// Ports the core compilation pipeline from Python (sync.py) to Rust.
// This eliminates the ~40ms Python subprocess overhead.
//
// Focus: APC-critical subset — string operations, integers, floats,
// control flow, print, and FFI imports.
// ============================================================================

use std::collections::HashMap;

// ── LEB128 Encoding ─────────────────────────────────────────

fn encode_uleb128(mut val: u64) -> Vec<u8> {
    let mut res = Vec::new();
    loop {
        let b = (val & 0x7F) as u8;
        val >>= 7;
        if val == 0 {
            res.push(b);
            break;
        }
        res.push(b | 0x80);
    }
    res
}

fn encode_sleb128(mut val: i64) -> Vec<u8> {
    let mut res = Vec::new();
    loop {
        let b = (val & 0x7f) as u8;
        val >>= 7;
        if (val == 0 && (b & 0x40) == 0) || (val == -1 && (b & 0x40) != 0) {
            res.push(b);
            break;
        }
        res.push(b | 0x80);
    }
    res
}

fn encode_f32(val: f32) -> Vec<u8> {
    val.to_le_bytes().to_vec()
}

fn write_sec(sec_id: u8, payload: &[u8]) -> Vec<u8> {
    let mut out = vec![sec_id];
    out.extend(encode_uleb128(payload.len() as u64));
    out.extend(payload);
    out
}

fn encode_string(s: &str) -> Vec<u8> {
    let bytes = s.as_bytes();
    let mut out = encode_uleb128(bytes.len() as u64);
    out.extend(bytes);
    out
}

// ── AST Node Types ──────────────────────────────────────────

#[derive(Debug, Clone)]
enum Node {
    Literal(Value),
    Identifier(String),
    BinaryExpr { op: String, left: Box<Node>, right: Box<Node> },
    IfStmt { test: Box<Node>, consequent: Box<Node>, alternate: Option<Box<Node>> },
    WhileStmt { test: Box<Node>, body: Box<Node> },
    Block(Vec<Node>),
    VarDecl { name: String, init: Box<Node> },
    VarAssign { name: String, value: Box<Node> },
    Call { callee: String, args: Vec<Node> },
    Convert { target: String, arg: Box<Node> },
    DataSegment { offset: u64, data: Vec<u8> },
}

#[derive(Debug, Clone)]
enum Value {
    Int(i64),
    Float(f32),
    Str(String),
}

// ── Tokenizer ───────────────────────────────────────────────

fn tokenize(source: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    
    for line in source.lines() {
        // Strip // comments (not inside strings)
        let mut comment_idx = None;
        let mut in_string = false;
        let chars: Vec<char> = line.chars().collect();
        for i in 0..chars.len() {
            if chars[i] == '"' { in_string = !in_string; }
            else if !in_string && i + 1 < chars.len() && chars[i] == '/' && chars[i+1] == '/' {
                comment_idx = Some(i);
                break;
            }
        }
        let line = if let Some(idx) = comment_idx { &line[..idx] } else { line };
        let line = line.trim();
        if line.is_empty() { continue; }
        
        // Check for (data offset "string") directive
        if line.starts_with("(data ") && line.ends_with(')') {
            let inner = &line[6..line.len()-1]; // strip "(data " and ")"
            if let Some(quote_start) = inner.find('"') {
                let offset_str = inner[..quote_start].trim();
                let str_content = &inner[quote_start+1..inner.len()-1]; // strip quotes
                tokens.push("(data".to_string());
                tokens.push(offset_str.to_string());
                tokens.push(format!("\"{}\"", str_content));
                tokens.push(")".to_string());
                continue;
            }
        }
        
        // Tokenize respecting quoted strings and brackets
        let mut i = 0;
        let bytes = line.as_bytes();
        while i < bytes.len() {
            if bytes[i] == b' ' || bytes[i] == b'\t' {
                i += 1;
                continue;
            }
            if bytes[i] == b'[' || bytes[i] == b']' {
                tokens.push(String::from(bytes[i] as char));
                i += 1;
                continue;
            }
            if bytes[i] == b'"' {
                // Scan to closing quote
                let mut j = i + 1;
                while j < bytes.len() && bytes[j] != b'"' {
                    if bytes[j] == b'\\' { j += 1; } // skip escaped char
                    j += 1;
                }
                let s = std::str::from_utf8(&bytes[i..=j.min(bytes.len()-1)]).unwrap_or("\"\"");
                tokens.push(s.to_string());
                i = j + 1;
            } else {
                let mut j = i;
                while j < bytes.len() && !matches!(bytes[j], b' ' | b'\t' | b'[' | b']' | b';') {
                    j += 1;
                }
                if j > i {
                    let s = std::str::from_utf8(&bytes[i..j]).unwrap_or("");
                    tokens.push(s.to_string());
                }
                i = j;
            }
        }
    }
    
    tokens
}

// ── Parser ──────────────────────────────────────────────────

const BINARY_OPS: &[&str] = &[
    "+", "-", "*", "/", "%", "==", "!=", ">", "<", "^", "&", "|",
    "<<", ">>", ">=", "<=", "==="
];

struct Parser {
    tokens: Vec<String>,
    pos: usize,
    arities: HashMap<String, usize>, // function name → arity
}

impl Parser {
    fn new(tokens: Vec<String>, arities: HashMap<String, usize>) -> Self {
        Parser { tokens, pos: 0, arities }
    }
    
    fn peek(&self) -> Option<&str> {
        self.tokens.get(self.pos).map(|s| s.as_str())
    }
    
    fn advance(&mut self) -> &str {
        let tok = &self.tokens[self.pos];
        self.pos += 1;
        tok
    }
    
    fn parse_expr(&mut self) -> Node {
        let tok = self.advance().to_string();
        
        // Float literal
        if tok.contains('.') && tok.replace('.', "").trim_start_matches('-').chars().all(|c| c.is_ascii_digit()) {
            if let Ok(v) = tok.parse::<f32>() {
                return Node::Literal(Value::Float(v));
            }
        }
        
        // Integer literal
        if tok.trim_start_matches('-').chars().all(|c| c.is_ascii_digit()) && !tok.is_empty() {
            if let Ok(v) = tok.parse::<i64>() {
                return Node::Literal(Value::Int(v));
            }
        }
        
        // String literal
        if tok.starts_with('"') && tok.ends_with('"') && tok.len() >= 2 {
            let raw = &tok[1..tok.len()-1];
            let processed = process_escapes(raw);
            return Node::Literal(Value::Str(processed));
        }
        
        // Variable reference $N
        if tok.starts_with('$') {
            return Node::Identifier(tok);
        }
        
        // Type conversions
        if tok == "to_f32" || tok == "to_i64" {
            let arg = self.parse_expr();
            return Node::Convert { target: tok, arg: Box::new(arg) };
        }
        
        // Binary operators
        if BINARY_OPS.contains(&tok.as_str()) {
            let left = self.parse_expr();
            let right = self.parse_expr();
            return Node::BinaryExpr { op: tok, left: Box::new(left), right: Box::new(right) };
        }
        
        // Block [ ... ]
        if tok == "[" {
            let mut body = Vec::new();
            loop {
                if self.peek() == Some("]") {
                    self.advance();
                    break;
                }
                if self.peek().is_none() { break; }
                body.push(self.parse_expr());
            }
            return Node::Block(body);
        }
        
        // seq N (legacy)
        if tok == "seq" {
            let count: usize = self.advance().parse().unwrap_or(0);
            let body: Vec<Node> = (0..count).map(|_| self.parse_expr()).collect();
            return Node::Block(body);
        }
        
        // ret <expr>
        if tok == "ret" {
            return self.parse_expr();
        }
        
        // let <var> <expr>
        if tok == "let" {
            let name = self.advance().to_string();
            let init = self.parse_expr();
            return Node::VarDecl { name, init: Box::new(init) };
        }
        
        // set <var> <expr>
        if tok == "set" {
            let name = self.advance().to_string();
            let val = self.parse_expr();
            return Node::VarAssign { name, value: Box::new(val) };
        }
        
        // if <test> <then> <else>
        if tok == "if" {
            let test = self.parse_expr();
            let cons = self.parse_expr();
            let alt = self.parse_expr();
            return Node::IfStmt {
                test: Box::new(test),
                consequent: Box::new(cons),
                alternate: Some(Box::new(alt)),
            };
        }
        
        // while <test> <body>
        if tok == "while" {
            let test = self.parse_expr();
            let body = self.parse_expr();
            return Node::WhileStmt { test: Box::new(test), body: Box::new(body) };
        }
        
        // Memory operations
        if tok == "read" {
            let addr = self.parse_expr();
            return Node::Call { callee: "__load".into(), args: vec![addr] };
        }
        if tok == "write" {
            let addr = self.parse_expr();
            let val = self.parse_expr();
            return Node::Call { callee: "__store".into(), args: vec![addr, val] };
        }
        if tok == "read8" {
            let addr = self.parse_expr();
            return Node::Call { callee: "__load8".into(), args: vec![addr] };
        }
        if tok == "write8" {
            let addr = self.parse_expr();
            let val = self.parse_expr();
            return Node::Call { callee: "__store8".into(), args: vec![addr, val] };
        }
        if tok == "memory_size" {
            return Node::Call { callee: "__memory_size".into(), args: vec![] };
        }
        if tok == "memory_grow" {
            let pages = self.parse_expr();
            return Node::Call { callee: "__memory_grow".into(), args: vec![pages] };
        }
        if tok == "alloc" {
            let count = self.parse_expr();
            return Node::Call { callee: "__alloc".into(), args: vec![count] };
        }
        if tok == "store" {
            let ptr = self.parse_expr();
            let idx = self.parse_expr();
            let val = self.parse_expr();
            return Node::Call { callee: "__f32_store".into(), args: vec![ptr, idx, val] };
        }
        if tok == "load" {
            let ptr = self.parse_expr();
            let idx = self.parse_expr();
            return Node::Call { callee: "__f32_load".into(), args: vec![ptr, idx] };
        }
        
        // call <func_name> <args...>
        if tok == "call" {
            let func_name = self.advance().to_string();
            let arity = self.arities.get(&func_name).copied().unwrap_or(0);
            let args: Vec<Node> = (0..arity).map(|_| self.parse_expr()).collect();
            return Node::Call { callee: func_name, args };
        }
        
        // Unknown token — treat as identifier
        Node::Identifier(tok)
    }
}

fn process_escapes(raw: &str) -> String {
    let mut result = String::new();
    let chars: Vec<char> = raw.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() {
            match chars[i+1] {
                'n' => result.push('\n'),
                'r' => result.push('\r'),
                't' => result.push('\t'),
                '0' => result.push('\0'),
                '\\' => result.push('\\'),
                '"' => result.push('"'),
                c => { result.push('\\'); result.push(c); }
            }
            i += 2;
        } else {
            result.push(chars[i]);
            i += 1;
        }
    }
    result
}

// ── Program Structure ───────────────────────────────────────

#[derive(Clone)]
struct FfiImport {
    module: String,
    field: String,
    arity: usize,
    param_types: Vec<u8>,
    result_types: Vec<u8>,
}

struct Function {
    arity: usize,
    body: Node,
}

struct Program {
    functions: Vec<(String, Function)>,  // ordered: helpers first, main last
    ffi_imports: Vec<(String, FfiImport)>,
    data_segments: Vec<(u64, Vec<u8>)>,  // (offset, bytes)
}

// ── Full Parse ──────────────────────────────────────────────

fn parse_program(source: &str) -> Result<Program, String> {
    let tokens = tokenize(source);
    
    // Pass 1: Register arities and FFI imports
    let mut arities: HashMap<String, usize> = HashMap::new();
    let mut ffi_imports_map: HashMap<String, FfiImport> = HashMap::new();
    let mut data_segments: Vec<(u64, Vec<u8>)> = Vec::new();
    
    // Add built-in callables
    arities.insert("print_i64".into(), 1);
    arities.insert("print_f32".into(), 1);
    arities.insert("console_log".into(), 1);
    
    let mut i = 0;
    while i < tokens.len() {
        let tok = &tokens[i];
        
        if tok == "@f" && i + 2 < tokens.len() {
            let arity: usize = tokens[i+1].parse().unwrap_or(0);
            let name = tokens[i+2].clone();
            arities.insert(name, arity);
            i += 3;
        } else if tok == "@import_ffi" && i + 4 < tokens.len() {
            let module = tokens[i+1].trim_matches('"').to_string();
            let field = tokens[i+2].trim_matches('"').to_string();
            let arity: usize = tokens[i+3].parse().unwrap_or(0);
            let alias = tokens[i+4].clone();
            arities.insert(alias.clone(), arity);
            ffi_imports_map.insert(alias, FfiImport {
                module,
                field,
                arity,
                param_types: vec![0x7E; arity], // default i64
                result_types: vec![0x7E],        // always returns i64
            });
            i += 5;
        } else if tok == "(data" && i + 3 < tokens.len() {
            let offset: u64 = tokens[i+1].parse().unwrap_or(0);
            let str_tok = &tokens[i+2];
            if str_tok.starts_with('"') && str_tok.ends_with('"') {
                let raw = &str_tok[1..str_tok.len()-1];
                let processed = process_escapes(raw);
                data_segments.push((offset, processed.into_bytes()));
            }
            i += 4; // (data, offset, "string", )
        } else if tok == "@schema" || tok == "@doc" || tok == "@assert" 
               || tok == "@requires" || tok == "@ensures" || tok == "@proof" 
               || tok == "@inv" || tok == "@import" {
            // Skip directives — consume tokens until next @ or end
            i += 1;
            while i < tokens.len() && !tokens[i].starts_with('@') && tokens[i] != "(data" {
                if tokens[i] == "[" {
                    // Skip bracket-enclosed body
                    let mut depth = 1;
                    i += 1;
                    while i < tokens.len() && depth > 0 {
                        if tokens[i] == "[" { depth += 1; }
                        if tokens[i] == "]" { depth -= 1; }
                        i += 1;
                    }
                } else {
                    i += 1;
                }
            }
        } else {
            i += 1;
        }
    }
    
    // Pass 2: Parse function bodies
    let mut functions = Vec::new();
    let mut parser = Parser::new(tokens.clone(), arities.clone());
    
    while parser.pos < parser.tokens.len() {
        let tok = parser.peek().unwrap_or("").to_string();
        
        if tok == "@f" {
            parser.advance(); // @f
            let arity: usize = parser.advance().parse().unwrap_or(0);
            let name = parser.advance().to_string();
            let body = parser.parse_expr();
            functions.push((name, Function { arity, body }));
        } else if tok == "@import_ffi" {
            // Already processed in pass 1, skip 5 tokens
            for _ in 0..5 { parser.advance(); }
        } else if tok == "(data" {
            // Already processed in pass 1, skip 4 tokens
            for _ in 0..4 { parser.advance(); }
        } else if tok.starts_with('@') {
            // Skip other directives
            parser.advance();
            while parser.pos < parser.tokens.len() {
                let next = parser.peek().unwrap_or("");
                if next.starts_with('@') || next == "(data" { break; }
                if next == "[" {
                    let mut depth = 0;
                    loop {
                        let t = parser.peek().unwrap_or("");
                        if t == "[" { depth += 1; }
                        if t == "]" { depth -= 1; }
                        parser.advance();
                        if depth <= 0 { break; }
                        if parser.pos >= parser.tokens.len() { break; }
                    }
                    break;
                }
                parser.advance();
            }
        } else {
            parser.advance();
        }
    }
    
    // Build ordered FFI import list
    let mut standard_ffi = build_standard_ffi();
    
    // Merge custom FFI imports
    for (alias, info) in &ffi_imports_map {
        if !standard_ffi.contains_key(alias) {
            standard_ffi.insert(alias.clone(), FfiImport {
                module: info.module.clone(),
                field: info.field.clone(),
                arity: info.arity,
                param_types: vec![0x7E; info.arity],
                result_types: vec![0x7E],
            });
        }
    }
    
    let import_order: Vec<String> = standard_ffi.keys().cloned().collect();
    let ordered_imports: Vec<(String, FfiImport)> = import_order.into_iter()
        .map(|k| {
            let v = standard_ffi.remove(&k).unwrap();
            (k, v)
        })
        .collect();
    
    Ok(Program {
        functions,
        ffi_imports: ordered_imports,
        data_segments,
    })
}

fn build_standard_ffi() -> HashMap<String, FfiImport> {
    let mut m = HashMap::new();
    let ffi = |module: &str, field: &str, params: Vec<u8>, results: Vec<u8>| -> FfiImport {
        FfiImport {
            module: module.into(),
            field: field.into(),
            arity: params.len(),
            param_types: params,
            result_types: results,
        }
    };
    
    m.insert("print".into(), ffi("env", "print", vec![0x7F, 0x7F], vec![]));
    m.insert("print_i64".into(), ffi("env", "print_i64", vec![0x7E], vec![]));
    m.insert("print_f32".into(), ffi("env", "print_f32", vec![0x7D], vec![]));
    m.insert("tcp_connect".into(), ffi("env", "tcp_connect", vec![0x7E, 0x7E], vec![0x7E]));
    m.insert("tcp_send_raw".into(), ffi("env", "tcp_send_raw", vec![0x7E; 3], vec![0x7E]));
    m.insert("p2p_receive_compute".into(), ffi("env", "p2p_receive_compute", vec![0x7E; 2], vec![0x7E]));
    m.insert("tcp_recv_raw".into(), ffi("env", "tcp_recv_raw", vec![0x7E; 3], vec![0x7E]));
    m.insert("tcp_bind".into(), ffi("env", "tcp_bind", vec![0x7E], vec![0x7E]));
    m.insert("crypto_sign".into(), ffi("env", "crypto_sign", vec![0x7F; 4], vec![]));
    m.insert("crypto_verify".into(), ffi("env", "crypto_verify", vec![0x7F; 4], vec![0x7F]));
    m.insert("tcp_accept".into(), ffi("env", "tcp_accept", vec![0x7E], vec![0x7E]));
    m.insert("tcp_close".into(), ffi("env", "tcp_close", vec![0x7E], vec![0x7E]));
    m.insert("p2p_receive".into(), ffi("env", "p2p_receive", vec![0x7E; 3], vec![0x7E]));
    m.insert("p2p_respond_compute".into(), ffi("env", "p2p_respond_compute", vec![0x7E; 3], vec![0x7E]));
    m.insert("p2p_request_compute".into(), ffi("env", "p2p_request_compute", vec![0x7E; 3], vec![0x7E]));
    m.insert("gpu_matmul".into(), ffi("env", "gpu_matmul", vec![0x7E; 6], vec![0x7E]));
    m.insert("gpu_relu".into(), ffi("env", "gpu_relu", vec![0x7E; 2], vec![0x7E]));
    m.insert("gpu_softmax".into(), ffi("env", "gpu_softmax", vec![0x7E; 3], vec![0x7E]));
    m.insert("gpu_silu".into(), ffi("env", "gpu_silu", vec![0x7E; 2], vec![0x7E]));
    m.insert("gpu_layernorm".into(), ffi("env", "gpu_layernorm", vec![0x7E; 4], vec![0x7E]));
    m.insert("gpu_add".into(), ffi("env", "gpu_add", vec![0x7E; 3], vec![0x7E]));
    m.insert("load_weights".into(), ffi("env", "load_weights", vec![0x7E; 4], vec![0x7E]));
    m.insert("weight_info".into(), ffi("env", "weight_info", vec![0x7E], vec![0x7E]));
    m
}

// ── Wasm Code Generator ─────────────────────────────────────

struct Emitter {
    func_index: HashMap<String, usize>,
    ffi_imports: Vec<(String, FfiImport)>,
    // Per-function state
    local_env: HashMap<String, usize>,
    next_local: usize,
    local_types: HashMap<String, String>, // var name → "i64" | "f32"
    is_f32_func: bool,
    func_return_types: HashMap<String, String>,
    // String table
    string_table: Vec<Vec<u8>>,
    string_offset: u64,
}

impl Emitter {
    fn new(ffi_imports: Vec<(String, FfiImport)>, func_names: &[(String, usize)]) -> Self {
        let mut func_index = HashMap::new();
        let num_imports = ffi_imports.len();
        
        for (i, (name, _)) in ffi_imports.iter().enumerate() {
            func_index.insert(name.clone(), i);
        }
        for (i, (name, _)) in func_names.iter().enumerate() {
            func_index.insert(name.clone(), num_imports + i);
        }
        
        Emitter {
            func_index,
            ffi_imports,
            local_env: HashMap::new(),
            next_local: 0,
            local_types: HashMap::new(),
            is_f32_func: false,
            func_return_types: HashMap::new(),
            string_table: Vec::new(),
            string_offset: 256,
        }
    }
    
    fn reset_locals(&mut self, arity: usize) {
        self.local_env.clear();
        self.local_types.clear();
        self.next_local = arity;
        // Pre-register parameter locals
        for i in 0..arity {
            let name = format!("${}", i);
            self.local_env.insert(name, i);
        }
    }
    
    // ── Type inference ──
    
    fn expr_type(&self, node: &Node) -> &str {
        match node {
            Node::Literal(Value::Float(_)) => "f32",
            Node::Literal(Value::Int(_)) => "i64",
            Node::Literal(Value::Str(_)) => "i64",
            Node::Identifier(name) => {
                if let Some(t) = self.local_types.get(name) { t }
                else if self.is_f32_func { "f32" }
                else { "i64" }
            }
            Node::BinaryExpr { op, left, right } => {
                if matches!(op.as_str(), ">" | "<" | ">=" | "<=" | "==" | "===" | "!=") {
                    return "i64";
                }
                let lt = self.expr_type(left);
                let rt = self.expr_type(right);
                if lt == "f32" || rt == "f32" { "f32" } else { "i64" }
            }
            Node::IfStmt { consequent, .. } => self.expr_type(consequent),
            Node::Block(items) => {
                if let Some(last) = items.last() { self.expr_type(last) } else { "i64" }
            }
            Node::VarDecl { init, .. } => self.expr_type(init),
            Node::VarAssign { value, .. } => self.expr_type(value),
            Node::Call { callee, .. } => {
                if callee == "__f32_load" { return "f32"; }
                self.func_return_types.get(callee).map(|s| s.as_str()).unwrap_or("i64")
            }
            Node::Convert { target, .. } => {
                if target == "to_f32" { "f32" } else { "i64" }
            }
            _ => "i64",
        }
    }
    
    fn has_f32_literal(node: &Node) -> bool {
        match node {
            Node::Literal(Value::Float(_)) => true,
            Node::BinaryExpr { left, right, .. } => Self::has_f32_literal(left) || Self::has_f32_literal(right),
            Node::IfStmt { test, consequent, alternate } => {
                Self::has_f32_literal(test) || Self::has_f32_literal(consequent)
                || alternate.as_ref().map_or(false, |a| Self::has_f32_literal(a))
            }
            Node::WhileStmt { test, body } => Self::has_f32_literal(test) || Self::has_f32_literal(body),
            Node::Block(items) => items.iter().any(|n| Self::has_f32_literal(n)),
            Node::VarDecl { init, .. } => Self::has_f32_literal(init),
            Node::VarAssign { value, .. } => Self::has_f32_literal(value),
            Node::Call { args, .. } => args.iter().any(|n| Self::has_f32_literal(n)),
            Node::Convert { arg, .. } => Self::has_f32_literal(arg),
            _ => false,
        }
    }
    
    // ── Void-returns check ──
    
    fn is_void_call(&self, callee: &str) -> bool {
        matches!(callee, "print" | "print_i64" | "print_f32" | "crypto_sign" 
                | "__store8" | "__store" | "__memcpy" | "__f32_store")
    }
    
    fn does_call_return(&self, callee: &str) -> bool {
        if self.is_void_call(callee) { return false; }
        if let Some((_, info)) = self.ffi_imports.iter().find(|(n, _)| n == callee) {
            return !info.result_types.is_empty();
        }
        self.func_index.contains_key(callee)
    }
    
    // ── Expression-mode compilation (pushes exactly 1 value) ──
    
    fn compile_expr(&mut self, node: &Node) -> Vec<u8> {
        match node {
            Node::Literal(Value::Int(v)) => {
                let mut out = vec![0x42]; // i64.const
                out.extend(encode_sleb128(*v));
                out
            }
            Node::Literal(Value::Float(v)) => {
                let mut out = vec![0x43]; // f32.const
                out.extend(encode_f32(*v));
                out
            }
            Node::Literal(Value::Str(s)) => {
                let str_bytes = s.as_bytes();
                let ptr = self.string_offset;
                let mut data = str_bytes.to_vec();
                data.push(0); // null-terminated
                self.string_table.push(data);
                self.string_offset += str_bytes.len() as u64 + 1;
                let mut out = vec![0x42]; // i64.const
                out.extend(encode_sleb128(ptr as i64));
                out
            }
            
            Node::Identifier(name) => {
                if let Some(&idx) = self.local_env.get(name) {
                    let mut out = vec![0x20]; // local.get
                    out.extend(encode_uleb128(idx as u64));
                    out
                } else {
                    vec![0x42, 0x00] // i64.const 0
                }
            }
            
            Node::BinaryExpr { op, left, right } => {
                let lt = self.expr_type(left).to_string();
                let rt = self.expr_type(right).to_string();
                let use_f32 = (lt == "f32" || rt == "f32") 
                    && !matches!(op.as_str(), "&" | "|" | "^" | "<<" | ">>" | "%");
                
                let mut result = self.compile_expr(left);
                if use_f32 && lt == "i64" { result.push(0xB4); } // f32.convert_i64_s
                result.extend(self.compile_expr(right));
                if use_f32 && rt == "i64" { result.push(0xB4); }
                
                if use_f32 {
                    match op.as_str() {
                        "==" | "===" => result.push(0x5B),
                        "!=" => result.push(0x5C),
                        "+" => result.push(0x92),
                        "-" => result.push(0x93),
                        "*" => result.push(0x94),
                        "/" => result.push(0x95),
                        ">" => result.push(0x5E),
                        "<" => result.push(0x5D),
                        ">=" => result.push(0x60),
                        "<=" => result.push(0x5F),
                        _ => {}
                    }
                } else {
                    match op.as_str() {
                        "==" | "===" => result.push(0x51),
                        "!=" => { result.push(0x51); result.push(0x45); result.push(0xAD); }
                        "+" => result.push(0x7C),
                        "-" => result.push(0x7D),
                        "*" => result.push(0x7E),
                        "/" => result.push(0x7F),
                        "%" => result.push(0x81),
                        ">" => result.push(0x55),
                        "<" => result.push(0x53),
                        ">=" => result.push(0x59),
                        "<=" => result.push(0x57),
                        "&" => result.push(0x83),
                        "|" => result.push(0x84),
                        "^" => result.push(0x85),
                        "<<" => result.push(0x86),
                        ">>" => result.push(0x88),
                        _ => {}
                    }
                }
                
                // Comparisons return i32, extend to i64
                if matches!(op.as_str(), "==" | "===" | "!=" | ">" | "<" | ">=" | "<=") {
                    if !(!use_f32 && op == "!=") {
                        result.push(0xAD); // i64.extend_i32_u
                    }
                }
                result
            }
            
            Node::IfStmt { test, consequent, alternate } => {
                let branch_type = self.expr_type(consequent).to_string();
                let test_type = self.expr_type(test).to_string();
                let mut result = self.compile_expr(test);
                if test_type == "f32" {
                    result.extend([0x43]);
                    result.extend(encode_f32(0.0));
                    result.push(0x5C); // f32.ne
                } else {
                    result.push(0xA7); // i32.wrap_i64
                }
                result.push(0x04); // if
                result.push(if branch_type == "f32" { 0x7D } else { 0x7E });
                result.extend(self.compile_expr(consequent));
                result.push(0x05); // else
                if let Some(alt) = alternate {
                    result.extend(self.compile_expr(alt));
                } else if branch_type == "f32" {
                    result.extend([0x43]);
                    result.extend(encode_f32(0.0));
                } else {
                    result.extend([0x42, 0x00]);
                }
                result.push(0x0B); // end
                result
            }
            
            Node::WhileStmt { test, body } => {
                let mut result = Vec::new();
                result.extend([0x02, 0x40]); // block (void)
                result.extend([0x03, 0x40]); // loop (void)
                result.extend(self.compile_expr(test));
                result.push(0xA7); // i32.wrap_i64
                result.extend([0x45, 0x0D, 0x01]); // i32.eqz, br_if 1
                result.extend(self.compile_void(body));
                result.extend([0x0C, 0x00]); // br 0
                result.extend([0x0B, 0x0B]); // end loop, end block
                if self.is_f32_func {
                    result.extend([0x43]);
                    result.extend(encode_f32(0.0));
                } else {
                    result.extend([0x42, 0x00]);
                }
                result
            }
            
            Node::Block(items) => {
                if items.is_empty() {
                    return if self.is_f32_func {
                        let mut r = vec![0x43];
                        r.extend(encode_f32(0.0));
                        r
                    } else {
                        vec![0x42, 0x00]
                    };
                }
                let mut result = Vec::new();
                for item in &items[..items.len()-1] {
                    result.extend(self.compile_void(item));
                }
                result.extend(self.compile_expr(&items[items.len()-1]));
                result
            }
            
            Node::VarDecl { name, init } => {
                let val_type = self.expr_type(init).to_string();
                if !self.local_env.contains_key(name) {
                    let idx = self.next_local;
                    self.local_env.insert(name.clone(), idx);
                    self.next_local += 1;
                }
                if !self.local_types.contains_key(name) {
                    self.local_types.insert(name.clone(), val_type.clone());
                }
                let declared = self.local_types.get(name).cloned().unwrap_or("i64".into());
                let mut result = self.compile_expr(init);
                if val_type == "f32" && declared == "i64" { result.push(0xAE); }
                else if val_type == "i64" && declared == "f32" { result.push(0xB4); }
                let idx = self.local_env[name];
                result.push(0x22); // local.tee
                result.extend(encode_uleb128(idx as u64));
                result
            }
            
            Node::VarAssign { name, value } => {
                let val_type = self.expr_type(value).to_string();
                if !self.local_env.contains_key(name) {
                    let idx = self.next_local;
                    self.local_env.insert(name.clone(), idx);
                    self.next_local += 1;
                }
                if !self.local_types.contains_key(name) {
                    self.local_types.insert(name.clone(), val_type.clone());
                }
                let declared = self.local_types.get(name).cloned().unwrap_or("i64".into());
                let mut result = self.compile_expr(value);
                if val_type == "f32" && declared == "i64" { result.push(0xAE); }
                else if val_type == "i64" && declared == "f32" { result.push(0xB4); }
                let idx = self.local_env[name];
                result.push(0x22); // local.tee
                result.extend(encode_uleb128(idx as u64));
                result
            }
            
            Node::Call { callee, args } => {
                let mut result = self.compile_call(callee, args);
                if self.is_void_call(callee) {
                    result.extend([0x42, 0x00]); // push 0 for void calls in expr context
                }
                result
            }
            
            Node::Convert { target, arg } => {
                let mut result = self.compile_expr(arg);
                if target == "to_f32" { result.push(0xB4); }
                else if target == "to_i64" { result.push(0xAE); }
                result
            }
            
            Node::DataSegment { .. } => vec![0x42, 0x00],
        }
    }
    
    // ── Void-mode compilation (drops result) ──
    
    fn compile_void(&mut self, node: &Node) -> Vec<u8> {
        match node {
            Node::VarDecl { name, init } => {
                let val_type = self.expr_type(init).to_string();
                if !self.local_env.contains_key(name) {
                    let idx = self.next_local;
                    self.local_env.insert(name.clone(), idx);
                    self.next_local += 1;
                }
                if !self.local_types.contains_key(name) {
                    self.local_types.insert(name.clone(), val_type.clone());
                }
                let declared = self.local_types.get(name).cloned().unwrap_or("i64".into());
                let mut result = self.compile_expr(init);
                if val_type == "f32" && declared == "i64" { result.push(0xAE); }
                else if val_type == "i64" && declared == "f32" { result.push(0xB4); }
                let idx = self.local_env[name];
                result.push(0x21); // local.set
                result.extend(encode_uleb128(idx as u64));
                result
            }
            Node::VarAssign { name, value } => {
                let val_type = self.expr_type(value).to_string();
                if !self.local_env.contains_key(name) {
                    let idx = self.next_local;
                    self.local_env.insert(name.clone(), idx);
                    self.next_local += 1;
                }
                if !self.local_types.contains_key(name) {
                    self.local_types.insert(name.clone(), val_type.clone());
                }
                let declared = self.local_types.get(name).cloned().unwrap_or("i64".into());
                let mut result = self.compile_expr(value);
                if val_type == "f32" && declared == "i64" { result.push(0xAE); }
                else if val_type == "i64" && declared == "f32" { result.push(0xB4); }
                let idx = self.local_env[name];
                result.push(0x21); // local.set
                result.extend(encode_uleb128(idx as u64));
                result
            }
            Node::WhileStmt { test, body } => {
                let mut result = Vec::new();
                result.extend([0x02, 0x40]);
                result.extend([0x03, 0x40]);
                result.extend(self.compile_expr(test));
                result.push(0xA7);
                result.extend([0x45, 0x0D, 0x01]);
                result.extend(self.compile_void(body));
                result.extend([0x0C, 0x00]);
                result.extend([0x0B, 0x0B]);
                result
            }
            Node::IfStmt { test, consequent, alternate } => {
                let mut result = self.compile_expr(test);
                result.push(0xA7);
                result.push(0x04);
                result.push(0x40); // void block type
                result.extend(self.compile_void(consequent));
                if let Some(alt) = alternate {
                    result.push(0x05);
                    result.extend(self.compile_void(alt));
                }
                result.push(0x0B);
                result
            }
            Node::Block(items) => {
                let mut result = Vec::new();
                for item in items {
                    result.extend(self.compile_void(item));
                }
                result
            }
            Node::Call { callee, args } => {
                if self.is_void_call(callee) {
                    return self.compile_call(callee, args);
                }
                let mut result = self.compile_call(callee, args);
                if self.does_call_return(callee) {
                    result.push(0x1A); // drop
                }
                result
            }
            _ => {
                let mut result = self.compile_expr(node);
                result.push(0x1A); // drop
                result
            }
        }
    }
    
    // ── Call compilation ──
    
    fn compile_call(&mut self, callee: &str, args: &[Node]) -> Vec<u8> {
        // Intrinsics
        match callee {
            "__load8" => {
                let mut r = self.compile_expr(&args[0]);
                r.extend([0xA7, 0x2D, 0x00, 0x00, 0xAD]);
                return r;
            }
            "__store8" => {
                let mut r = self.compile_expr(&args[0]);
                r.push(0xA7);
                r.extend(self.compile_expr(&args[1]));
                r.extend([0xA7, 0x3A, 0x00, 0x00]);
                return r;
            }
            "__load" => {
                let mut r = self.compile_expr(&args[0]);
                r.extend([0xA7, 0x29, 0x03, 0x00]);
                return r;
            }
            "__store" => {
                let mut r = self.compile_expr(&args[0]);
                r.push(0xA7);
                r.extend(self.compile_expr(&args[1]));
                r.extend([0x37, 0x03, 0x00]);
                return r;
            }
            "__memory_size" => {
                return vec![0x3F, 0x00, 0xAD];
            }
            "__memory_grow" => {
                let mut r = self.compile_expr(&args[0]);
                r.extend([0xA7, 0x40, 0x00, 0xAD]);
                return r;
            }
            "__f32_store" => {
                // store ptr idx val → addr = (ptr + idx*4), f32.store
                let mut r = self.compile_expr(&args[0]);
                r.push(0xA7); // i32.wrap ptr
                r.extend(self.compile_expr(&args[1]));
                r.push(0xA7); // i32.wrap idx
                r.extend([0x41, 0x04]); // i32.const 4
                r.push(0x6C); // i32.mul
                r.push(0x6A); // i32.add
                let val = self.compile_expr(&args[2]);
                let val_type = self.expr_type(&args[2]).to_string();
                r.extend(val);
                if val_type == "i64" { r.push(0xB4); } // convert to f32 if needed
                r.extend([0x38, 0x02, 0x00]); // f32.store
                return r;
            }
            "__f32_load" => {
                let mut r = self.compile_expr(&args[0]);
                r.push(0xA7);
                r.extend(self.compile_expr(&args[1]));
                r.push(0xA7);
                r.extend([0x41, 0x04]);
                r.push(0x6C);
                r.push(0x6A);
                r.extend([0x2A, 0x02, 0x00]);
                return r;
            }
            _ => {}
        }
        
        // Regular function/FFI call
        if let Some(&idx) = self.func_index.get(callee) {
            let mut result = Vec::new();
            
            // Check if this is an FFI import (need type coercion)
            // Clone param_types to avoid borrow conflict with compile_expr
            let ffi_param_types: Option<Vec<u8>> = self.ffi_imports.iter()
                .find(|(n, _)| n == callee)
                .map(|(_, info)| info.param_types.clone());
            
            if let Some(param_types) = ffi_param_types {
                for (i, arg) in args.iter().enumerate() {
                    let arg_type = self.expr_type(arg).to_string();
                    result.extend(self.compile_expr(arg));
                    if i < param_types.len() {
                        let expected = param_types[i];
                        if expected == 0x7F { result.push(0xA7); } // i32.wrap_i64
                        else if expected == 0x7D && arg_type != "f32" { result.push(0xB4); }
                        else if expected == 0x7E && arg_type == "f32" { result.push(0xAE); }
                    }
                }
            } else {
                // User function call
                for arg in args {
                    result.extend(self.compile_expr(arg));
                }
            }
            
            result.push(0x10); // call
            result.extend(encode_uleb128(idx as u64));
            return result;
        }
        
        vec![0x42, 0x00] // unknown call
    }
}

// ── Main Compilation Entry Point ────────────────────────────

pub fn compile_syn(source: &str) -> Result<Vec<u8>, String> {
    let program = parse_program(source)?;
    
    // Build function name list (helpers first, main last)
    let mut func_names: Vec<(String, usize)> = Vec::new();
    for (name, func) in &program.functions {
        if name != "main" {
            func_names.push((name.clone(), func.arity));
        }
    }
    for (name, func) in &program.functions {
        if name == "main" {
            func_names.push((name.clone(), func.arity));
        }
    }
    
    let mut emitter = Emitter::new(program.ffi_imports.clone(), &func_names);
    
    // Detect f32 functions
    let mut func_is_f32: HashMap<String, bool> = HashMap::new();
    for (name, func) in &program.functions {
        func_is_f32.insert(name.clone(), Emitter::has_f32_literal(&func.body));
    }
    
    // Infer return types (simple pass — assume body type)
    for (name, func) in &program.functions {
        let is_f32 = func_is_f32.get(name).copied().unwrap_or(false);
        emitter.is_f32_func = is_f32;
        let ret_type = emitter.expr_type(&func.body).to_string();
        emitter.func_return_types.insert(name.clone(), ret_type);
    }
    
    // ── Type section (Section 1) ──
    let mut type_sigs: Vec<(Vec<u8>, Vec<u8>)> = Vec::new();
    let mut type_map: HashMap<(Vec<u8>, Vec<u8>), usize> = HashMap::new();
    
    let mut get_or_add_type = |params: Vec<u8>, results: Vec<u8>| -> usize {
        let key = (params.clone(), results.clone());
        if let Some(&idx) = type_map.get(&key) { return idx; }
        let idx = type_sigs.len();
        type_map.insert(key, idx);
        type_sigs.push((params, results));
        idx
    };
    
    // Import type indices
    let import_type_indices: Vec<usize> = program.ffi_imports.iter()
        .map(|(_, info)| get_or_add_type(info.param_types.clone(), info.result_types.clone()))
        .collect();
    
    // User function type indices
    let user_type_indices: Vec<usize> = func_names.iter()
        .map(|(name, arity)| {
            let is_f32 = func_is_f32.get(name).copied().unwrap_or(false);
            let param_type = if is_f32 { 0x7D } else { 0x7E };
            let params = vec![param_type; *arity];
            let ret = if is_f32 { vec![0x7D] } else { vec![0x7E] };
            get_or_add_type(params, ret)
        })
        .collect();
    
    // Build type section payload
    let mut type_payload = encode_uleb128(type_sigs.len() as u64);
    for (params, results) in &type_sigs {
        type_payload.push(0x60); // func type
        type_payload.extend(encode_uleb128(params.len() as u64));
        type_payload.extend(params);
        type_payload.extend(encode_uleb128(results.len() as u64));
        type_payload.extend(results);
    }
    
    // ── Import section (Section 2) ──
    let mut import_payload = encode_uleb128(program.ffi_imports.len() as u64);
    for (i, (_, info)) in program.ffi_imports.iter().enumerate() {
        import_payload.extend(encode_string(&info.module));
        import_payload.extend(encode_string(&info.field));
        import_payload.push(0x00); // func import
        import_payload.extend(encode_uleb128(import_type_indices[i] as u64));
    }
    
    // ── Function section (Section 3) ──
    let mut func_payload = encode_uleb128(func_names.len() as u64);
    for &tidx in &user_type_indices {
        func_payload.extend(encode_uleb128(tidx as u64));
    }
    
    // ── Memory section (Section 5) ──
    let memory_payload = vec![0x01, 0x00, 0x04]; // 1 memory, min 4 pages
    
    // ── Export section (Section 7) ──
    let mut export_payload = encode_uleb128(2); // main + memory
    // Export main
    let main_idx = emitter.func_index.get("main").copied().unwrap_or(0);
    export_payload.extend(encode_string("main"));
    export_payload.push(0x00); // func
    export_payload.extend(encode_uleb128(main_idx as u64));
    // Export memory
    export_payload.extend(encode_string("memory"));
    export_payload.push(0x02); // memory
    export_payload.push(0x00);
    
    // ── Code section (Section 10) ──
    let mut compiled_bodies: Vec<Vec<u8>> = Vec::new();
    let mut global_data_segments: Vec<(u64, Vec<u8>)> = program.data_segments.clone();
    
    for (name, func) in &program.functions {
        let is_f32 = func_is_f32.get(name).copied().unwrap_or(false);
        
        emitter.reset_locals(func.arity);
        emitter.is_f32_func = is_f32;
        
        let mut body_opcodes = emitter.compile_expr(&func.body);
        body_opcodes.push(0x0B); // end
        
        // Build locals declaration
        let num_extra = if emitter.next_local > func.arity { emitter.next_local - func.arity } else { 0 };
        let locals_decl = if num_extra > 0 {
            let default_type = if is_f32 { 0x7D } else { 0x7E };
            // Build index-to-name mapping for extra locals
            let idx_to_name: HashMap<usize, String> = emitter.local_env.iter()
                .filter(|(_, &idx)| idx >= func.arity)
                .map(|(name, &idx)| (idx, name.clone()))
                .collect();
            
            let extra_types: Vec<u8> = (func.arity..emitter.next_local)
                .map(|idx| {
                    let name = idx_to_name.get(&idx).map(|s| s.as_str()).unwrap_or("");
                    let t = emitter.local_types.get(name).map(|s| s.as_str()).unwrap_or("");
                    match t {
                        "f32" => 0x7D,
                        "i64" => 0x7E,
                        _ => default_type,
                    }
                })
                .collect();
            
            // Group consecutive same-type locals
            let mut groups: Vec<(usize, u8)> = Vec::new();
            for &t in &extra_types {
                if let Some(last) = groups.last_mut() {
                    if last.1 == t { last.0 += 1; continue; }
                }
                groups.push((1, t));
            }
            
            let mut decl = encode_uleb128(groups.len() as u64);
            for (cnt, t) in &groups {
                decl.extend(encode_uleb128(*cnt as u64));
                decl.push(*t);
            }
            decl
        } else {
            encode_uleb128(0)
        };
        
        let mut func_body = locals_decl;
        func_body.extend(body_opcodes);
        compiled_bodies.push(func_body);
    }
    
    // Code section
    let mut code_payload = encode_uleb128(compiled_bodies.len() as u64);
    for body in &compiled_bodies {
        code_payload.extend(encode_uleb128(body.len() as u64));
        code_payload.extend(body);
    }
    
    // ── Data section (Section 11) ──
    // Merge string table
    if !emitter.string_table.is_empty() {
        let mut data_payload = Vec::new();
        for s in &emitter.string_table {
            data_payload.extend(s);
        }
        global_data_segments.push((256, data_payload));
    }
    
    // ── Assemble Wasm module ──
    let mut wasm = vec![0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00]; // header
    wasm.extend(write_sec(1, &type_payload));
    wasm.extend(write_sec(2, &import_payload));
    wasm.extend(write_sec(3, &func_payload));
    wasm.extend(write_sec(5, &memory_payload));
    wasm.extend(write_sec(7, &export_payload));
    wasm.extend(write_sec(10, &code_payload));
    
    if !global_data_segments.is_empty() {
        let mut data_sec = encode_uleb128(global_data_segments.len() as u64);
        for (offset, bytes) in &global_data_segments {
            data_sec.extend(encode_uleb128(0)); // active segment
            data_sec.push(0x41); // i32.const
            data_sec.extend(encode_sleb128(*offset as i64));
            data_sec.push(0x0B); // end
            data_sec.extend(encode_uleb128(bytes.len() as u64));
            data_sec.extend(bytes);
        }
        wasm.extend(write_sec(11, &data_sec));
    }
    
    Ok(wasm)
}
