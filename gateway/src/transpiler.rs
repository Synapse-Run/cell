// ============================================================================
// Synapse Transpiler — Rust-native Python → .syn transpiler
// ============================================================================
// Ports the APC-critical subset of transpiler.py to Rust.
// Uses rustpython-parser or a minimal Python tokeniser to parse Python source,
// then emits .syn prefix-notation that the Rust compiler (compiler.rs) handles.
//
// For unsupported constructs, returns Err so the gateway falls back to
// the Python subprocess transpiler.
//
// TODO(2026-05-01): Python SDK now supports:
//   - Class-level constants (class Foo: X = 10) and self.method() calls
//   - Nested list comprehensions ([expr for x in range(n) for y in range(m)])
//   - Dict comprehensions ({k: v for x in range(n)})
//   - User-defined module imports mapped to .syn @use directives
//   These features currently fall back to the Python transpiler. Port when
//   traffic patterns show hot-path usage.
// ============================================================================

use std::collections::{HashMap, HashSet};

// Type constants matching Python transpiler
#[derive(Debug, Clone, PartialEq)]
enum PyType {
    I64,
    F32,
    Str,
}

pub struct Transpiler {
    var_map: HashMap<String, usize>,
    var_counter: usize,
    var_types: HashMap<String, PyType>,
    str_vars: HashSet<String>,
    string_data: Vec<(u64, String)>,
    string_arena_offset: u64,
    string_cache: HashMap<String, i64>, // dedup: string value → packed i64
    needs_strings: bool,
    main_body: Vec<String>,
}

impl Transpiler {
    fn new() -> Self {
        Transpiler {
            var_map: HashMap::new(),
            var_counter: 0,
            var_types: HashMap::new(),
            str_vars: HashSet::new(),
            string_data: Vec::new(),
            string_arena_offset: 65536,
            string_cache: HashMap::new(),
            needs_strings: false,
            main_body: Vec::new(),
        }
    }

    fn declare_var(&mut self, name: &str) -> String {
        if !self.var_map.contains_key(name) {
            self.var_map.insert(name.to_string(), self.var_counter);
            self.var_counter += 1;
        }
        format!("${}", self.var_map[name])
    }

    fn get_var(&self, name: &str) -> Result<String, String> {
        self.var_map.get(name)
            .map(|idx| format!("${}", idx))
            .ok_or_else(|| format!("Undefined variable: {}", name))
    }

    fn get_var_type(&self, name: &str) -> PyType {
        self.var_types.get(name).cloned().unwrap_or(PyType::I64)
    }

    fn set_var_type(&mut self, name: &str, t: PyType) {
        self.var_types.insert(name.to_string(), t);
    }

    fn emit_string_literal(&mut self, s: &str) -> (String, PyType) {
        self.needs_strings = true;
        if let Some(&packed) = self.string_cache.get(s) {
            return (packed.to_string(), PyType::Str);
        }
        let ptr = self.string_arena_offset;
        let length = s.len() as u64;
        self.string_data.push((ptr, s.to_string()));
        self.string_arena_offset += length + 1;
        let packed = ((ptr as i64) << 32) | (length as i64);
        self.string_cache.insert(s.to_string(), packed);
        (packed.to_string(), PyType::Str)
    }
}

// ── Minimal Python Tokenizer ────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
enum PyTok {
    Name(String),
    Int(i64),
    Float(f64),
    Str(String),           // string literal content
    FStr(Vec<FStrPart>),   // f-string parts
    Op(String),
    Punct(char),
    Newline,
    Indent(usize),
    Eof,
}

#[derive(Debug, Clone, PartialEq)]
enum FStrPart {
    Lit(String),
    Expr(String), // raw expression text inside {}
}

struct PyLexer {
    chars: Vec<char>,
    pos: usize,
    tokens: Vec<PyTok>,
}

impl PyLexer {
    fn new(source: &str) -> Self {
        PyLexer {
            chars: source.chars().collect(),
            pos: 0,
            tokens: Vec::new(),
        }
    }

    fn peek(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }

    fn advance(&mut self) -> Option<char> {
        let ch = self.chars.get(self.pos).copied();
        self.pos += 1;
        ch
    }

    fn tokenize(&mut self) -> Vec<PyTok> {
        let mut result = Vec::new();
        let mut line_start = true;

        while self.pos < self.chars.len() {
            let ch = self.chars[self.pos];

            // Track indentation at start of line
            if line_start && (ch == ' ' || ch == '\t') {
                let mut indent = 0;
                while self.pos < self.chars.len() && (self.chars[self.pos] == ' ' || self.chars[self.pos] == '\t') {
                    indent += if self.chars[self.pos] == '\t' { 4 } else { 1 };
                    self.pos += 1;
                }
                result.push(PyTok::Indent(indent));
                line_start = false;
                continue;
            }
            line_start = false;

            // Skip comments
            if ch == '#' {
                while self.pos < self.chars.len() && self.chars[self.pos] != '\n' {
                    self.pos += 1;
                }
                continue;
            }

            // Newlines
            if ch == '\n' {
                result.push(PyTok::Newline);
                self.pos += 1;
                line_start = true;
                continue;
            }

            // Whitespace
            if ch == ' ' || ch == '\t' || ch == '\r' {
                self.pos += 1;
                continue;
            }

            // f-strings: f"..." or f'...'
            if ch == 'f' && self.pos + 1 < self.chars.len()
                && (self.chars[self.pos + 1] == '"' || self.chars[self.pos + 1] == '\'')
            {
                self.pos += 1; // skip 'f'
                let quote = self.advance().unwrap();
                let parts = self.lex_fstring(quote);
                result.push(PyTok::FStr(parts));
                continue;
            }

            // Strings
            if ch == '"' || ch == '\'' {
                let s = self.lex_string(ch);
                result.push(PyTok::Str(s));
                continue;
            }

            // Numbers
            if ch.is_ascii_digit() || (ch == '-' && self.pos + 1 < self.chars.len() && self.chars[self.pos + 1].is_ascii_digit() && (result.is_empty() || matches!(result.last(), Some(PyTok::Op(_)) | Some(PyTok::Punct('(')) | Some(PyTok::Punct(','))))) {
                let num = self.lex_number();
                if num.contains('.') {
                    result.push(PyTok::Float(num.parse().unwrap_or(0.0)));
                } else {
                    result.push(PyTok::Int(num.parse().unwrap_or(0)));
                }
                continue;
            }

            // Identifiers and keywords
            if ch.is_ascii_alphabetic() || ch == '_' {
                let name = self.lex_name();
                result.push(PyTok::Name(name));
                continue;
            }

            // Multi-char operators
            if self.pos + 1 < self.chars.len() {
                let two: String = self.chars[self.pos..self.pos+2].iter().collect();
                if matches!(two.as_str(), "==" | "!=" | ">=" | "<=" | "+=" | "-=" | "*=" | "//") {
                    self.pos += 2;
                    result.push(PyTok::Op(two));
                    continue;
                }
            }

            // Single-char operators and punctuation
            match ch {
                '+' | '-' | '*' | '/' | '%' | '>' | '<' | '=' | '!' => {
                    self.pos += 1;
                    result.push(PyTok::Op(ch.to_string()));
                }
                '(' | ')' | '[' | ']' | '{' | '}' | ':' | ',' | '.' => {
                    self.pos += 1;
                    result.push(PyTok::Punct(ch));
                }
                _ => { self.pos += 1; } // skip unknown
            }
        }

        result.push(PyTok::Eof);
        result
    }

    fn lex_string(&mut self, quote: char) -> String {
        self.pos += 1; // skip opening quote
        let mut s = String::new();
        while self.pos < self.chars.len() && self.chars[self.pos] != quote {
            if self.chars[self.pos] == '\\' && self.pos + 1 < self.chars.len() {
                self.pos += 1;
                match self.chars[self.pos] {
                    'n' => s.push('\n'),
                    't' => s.push('\t'),
                    '\\' => s.push('\\'),
                    '\'' => s.push('\''),
                    '"' => s.push('"'),
                    c => { s.push('\\'); s.push(c); }
                }
            } else {
                s.push(self.chars[self.pos]);
            }
            self.pos += 1;
        }
        if self.pos < self.chars.len() { self.pos += 1; } // skip closing quote
        s
    }

    fn lex_fstring(&mut self, quote: char) -> Vec<FStrPart> {
        let mut parts = Vec::new();
        let mut current_lit = String::new();

        while self.pos < self.chars.len() && self.chars[self.pos] != quote {
            if self.chars[self.pos] == '{' {
                if !current_lit.is_empty() {
                    parts.push(FStrPart::Lit(std::mem::take(&mut current_lit)));
                }
                self.pos += 1; // skip {
                let mut expr = String::new();
                let mut depth = 1;
                while self.pos < self.chars.len() && depth > 0 {
                    if self.chars[self.pos] == '{' { depth += 1; }
                    if self.chars[self.pos] == '}' { depth -= 1; if depth == 0 { break; } }
                    expr.push(self.chars[self.pos]);
                    self.pos += 1;
                }
                if self.pos < self.chars.len() { self.pos += 1; } // skip }
                parts.push(FStrPart::Expr(expr));
            } else if self.chars[self.pos] == '\\' && self.pos + 1 < self.chars.len() {
                self.pos += 1;
                match self.chars[self.pos] {
                    'n' => current_lit.push('\n'),
                    't' => current_lit.push('\t'),
                    c => current_lit.push(c),
                }
                self.pos += 1;
            } else {
                current_lit.push(self.chars[self.pos]);
                self.pos += 1;
            }
        }
        if !current_lit.is_empty() {
            parts.push(FStrPart::Lit(current_lit));
        }
        if self.pos < self.chars.len() { self.pos += 1; } // skip closing quote
        parts
    }

    fn lex_number(&mut self) -> String {
        let mut num = String::new();
        if self.chars[self.pos] == '-' {
            num.push('-');
            self.pos += 1;
        }
        while self.pos < self.chars.len() && (self.chars[self.pos].is_ascii_digit() || self.chars[self.pos] == '.') {
            num.push(self.chars[self.pos]);
            self.pos += 1;
        }
        num
    }

    fn lex_name(&mut self) -> String {
        let mut name = String::new();
        while self.pos < self.chars.len() && (self.chars[self.pos].is_ascii_alphanumeric() || self.chars[self.pos] == '_') {
            name.push(self.chars[self.pos]);
            self.pos += 1;
        }
        name
    }
}

// ── Minimal Python Parser ───────────────────────────────────
// Parses the APC-critical subset of Python into statements

#[derive(Debug)]
enum PyStmt {
    Assign(String, PyExpr),       // name = expr
    AugAssign(String, String, PyExpr), // name += expr
    Expr(PyExpr),                 // bare expression
    If(PyExpr, Vec<PyStmt>, Vec<PyStmt>),
    While(PyExpr, Vec<PyStmt>),
    For(String, PyExpr, PyExpr, Vec<PyStmt>), // for var in range(start, end): body
    Pass,
}

#[derive(Debug)]
enum PyExpr {
    IntLit(i64),
    FloatLit(f64),
    StrLit(String),
    FString(Vec<FStrPart>),
    Name(String),
    BinOp(Box<PyExpr>, String, Box<PyExpr>),
    UnaryOp(String, Box<PyExpr>),
    Compare(Box<PyExpr>, String, Box<PyExpr>),
    BoolOp(String, Vec<PyExpr>),
    Call(String, Vec<PyExpr>),
    MethodCall(Box<PyExpr>, String, Vec<PyExpr>),
    Subscript(Box<PyExpr>, Box<PyExpr>),
    IfExpr(Box<PyExpr>, Box<PyExpr>, Box<PyExpr>), // body if cond else orelse
}

struct PyParser {
    tokens: Vec<PyTok>,
    pos: usize,
}

impl PyParser {
    fn new(tokens: Vec<PyTok>) -> Self {
        PyParser { tokens, pos: 0 }
    }

    fn peek(&self) -> &PyTok {
        self.tokens.get(self.pos).unwrap_or(&PyTok::Eof)
    }

    fn advance(&mut self) -> PyTok {
        let tok = self.tokens.get(self.pos).cloned().unwrap_or(PyTok::Eof);
        self.pos += 1;
        tok
    }

    fn expect_punct(&mut self, ch: char) -> Result<(), String> {
        if self.peek() == &PyTok::Punct(ch) {
            self.advance();
            Ok(())
        } else {
            Err(format!("Expected '{}', got {:?}", ch, self.peek()))
        }
    }

    fn skip_newlines(&mut self) {
        while matches!(self.peek(), PyTok::Newline | PyTok::Indent(_)) {
            self.advance();
        }
    }

    // Parse a full program into statements
    fn parse_program(&mut self) -> Result<Vec<PyStmt>, String> {
        let mut stmts = Vec::new();
        self.skip_newlines();
        while !matches!(self.peek(), PyTok::Eof) {
            // Skip indentation at top level
            if let PyTok::Indent(_) = self.peek() { self.advance(); continue; }
            if matches!(self.peek(), PyTok::Newline) { self.advance(); continue; }

            let stmt = self.parse_stmt(0)?;
            stmts.push(stmt);
            self.skip_newlines();
        }
        Ok(stmts)
    }

    fn parse_stmt(&mut self, _indent: usize) -> Result<PyStmt, String> {
        match self.peek().clone() {
            PyTok::Name(ref name) if name == "if" => self.parse_if(),
            PyTok::Name(ref name) if name == "while" => self.parse_while(),
            PyTok::Name(ref name) if name == "for" => self.parse_for(),
            PyTok::Name(ref name) if name == "pass" => { self.advance(); Ok(PyStmt::Pass) },
            PyTok::Name(ref name) if name == "import" || name == "from" => {
                // Skip import statements (consumed but not translated to .syn)
                while !matches!(self.peek(), PyTok::Newline | PyTok::Eof) {
                    self.advance();
                }
                Ok(PyStmt::Pass)
            },
            _ => {
                let expr = self.parse_expr()?;
                // Check for assignment
                match self.peek() {
                    PyTok::Op(ref op) if op == "=" => {
                        self.advance();
                        if let PyExpr::Name(name) = expr {
                            let value = self.parse_expr()?;
                            return Ok(PyStmt::Assign(name, value));
                        }
                        Err("Only simple name assignment supported".into())
                    }
                    PyTok::Op(ref op) if op == "+=" || op == "-=" || op == "*=" => {
                        let aug_op = match op.as_str() {
                            "+=" => "+",
                            "-=" => "-",
                            "*=" => "*",
                            _ => return Err(format!("Unsupported aug-assign: {}", op)),
                        }.to_string();
                        self.advance();
                        if let PyExpr::Name(name) = expr {
                            let value = self.parse_expr()?;
                            return Ok(PyStmt::AugAssign(name, aug_op, value));
                        }
                        Err("Only simple name aug-assign supported".into())
                    }
                    _ => Ok(PyStmt::Expr(expr)),
                }
            }
        }
    }

    fn parse_block(&mut self) -> Result<Vec<PyStmt>, String> {
        // Expect colon then newline then indented block
        self.expect_punct(':')?;
        self.skip_newlines();

        // Determine block indent level
        let block_indent = if let PyTok::Indent(n) = self.peek() {
            let n = *n;
            n
        } else {
            // Single-line block
            let stmt = self.parse_stmt(0)?;
            return Ok(vec![stmt]);
        };

        let mut stmts = Vec::new();
        loop {
            match self.peek() {
                PyTok::Indent(n) if *n >= block_indent => {
                    self.advance(); // consume indent
                    if matches!(self.peek(), PyTok::Newline | PyTok::Eof) {
                        self.skip_newlines();
                        continue;
                    }
                    let stmt = self.parse_stmt(block_indent)?;
                    stmts.push(stmt);
                    self.skip_newlines();
                }
                _ => break,
            }
        }
        Ok(stmts)
    }

    fn parse_if(&mut self) -> Result<PyStmt, String> {
        self.advance(); // consume 'if'
        let cond = self.parse_expr()?;
        let body = self.parse_block()?;
        self.skip_newlines(); // consume trailing newlines after if-body

        let mut else_body = Vec::new();
        // Check for elif/else
        if let PyTok::Indent(_) = self.peek() {
            let saved = self.pos;
            self.advance();
            if let PyTok::Name(ref name) = self.peek().clone() {
                if name == "elif" {
                    let elif_stmt = self.parse_if()?; // recursive
                    else_body.push(elif_stmt);
                } else if name == "else" {
                    self.advance(); // consume 'else'
                    else_body = self.parse_block()?;
                } else {
                    self.pos = saved; // rewind
                }
            } else {
                self.pos = saved;
            }
        } else if let PyTok::Name(ref name) = self.peek().clone() {
            if name == "elif" {
                let elif_stmt = self.parse_if()?;
                else_body.push(elif_stmt);
            } else if name == "else" {
                self.advance();
                else_body = self.parse_block()?;
            }
        }

        Ok(PyStmt::If(cond, body, else_body))
    }

    fn parse_while(&mut self) -> Result<PyStmt, String> {
        self.advance(); // consume 'while'
        let cond = self.parse_expr()?;
        let body = self.parse_block()?;
        Ok(PyStmt::While(cond, body))
    }

    fn parse_for(&mut self) -> Result<PyStmt, String> {
        self.advance(); // consume 'for'
        let var = if let PyTok::Name(name) = self.advance() { name }
                  else { return Err("Expected variable name in for loop".into()); };

        // expect 'in'
        if !matches!(self.advance(), PyTok::Name(ref n) if n == "in") {
            return Err("Expected 'in' in for loop".into());
        }

        // expect 'range'
        if !matches!(self.peek(), PyTok::Name(ref n) if n == "range") {
            return Err("Only for-in-range loops supported".into());
        }
        self.advance(); // consume 'range'
        self.expect_punct('(')?;

        let first = self.parse_expr()?;
        let (start, end) = if matches!(self.peek(), PyTok::Punct(',')) {
            self.advance(); // consume ','
            let second = self.parse_expr()?;
            (first, second)
        } else {
            (PyExpr::IntLit(0), first)
        };

        self.expect_punct(')')?;
        let body = self.parse_block()?;
        Ok(PyStmt::For(var, start, end, body))
    }

    // Expression parsing with precedence climbing
    fn parse_expr(&mut self) -> Result<PyExpr, String> {
        self.parse_or()
    }

    fn parse_or(&mut self) -> Result<PyExpr, String> {
        let mut left = self.parse_and()?;
        while matches!(self.peek(), PyTok::Name(ref n) if n == "or") {
            self.advance();
            let right = self.parse_and()?;
            left = PyExpr::BoolOp("or".into(), vec![left, right]);
        }
        Ok(left)
    }

    fn parse_and(&mut self) -> Result<PyExpr, String> {
        let mut left = self.parse_not()?;
        while matches!(self.peek(), PyTok::Name(ref n) if n == "and") {
            self.advance();
            let right = self.parse_not()?;
            left = PyExpr::BoolOp("and".into(), vec![left, right]);
        }
        Ok(left)
    }

    fn parse_not(&mut self) -> Result<PyExpr, String> {
        if matches!(self.peek(), PyTok::Name(ref n) if n == "not") {
            self.advance();
            let expr = self.parse_not()?;
            return Ok(PyExpr::UnaryOp("not".into(), Box::new(expr)));
        }
        self.parse_comparison()
    }

    fn parse_comparison(&mut self) -> Result<PyExpr, String> {
        let left = self.parse_addition()?;
        match self.peek() {
            PyTok::Op(ref op) if matches!(op.as_str(), "==" | "!=" | ">=" | "<=" | ">" | "<") => {
                let op = op.clone();
                self.advance();
                let right = self.parse_addition()?;
                Ok(PyExpr::Compare(Box::new(left), op, Box::new(right)))
            }
            _ => Ok(left),
        }
    }

    fn parse_addition(&mut self) -> Result<PyExpr, String> {
        let mut left = self.parse_multiplication()?;
        loop {
            match self.peek() {
                PyTok::Op(ref op) if op == "+" || op == "-" => {
                    let op = op.clone();
                    self.advance();
                    let right = self.parse_multiplication()?;
                    left = PyExpr::BinOp(Box::new(left), op, Box::new(right));
                }
                _ => break,
            }
        }
        Ok(left)
    }

    fn parse_multiplication(&mut self) -> Result<PyExpr, String> {
        let mut left = self.parse_unary()?;
        loop {
            match self.peek() {
                PyTok::Op(ref op) if op == "*" || op == "/" || op == "//" || op == "%" => {
                    let op = op.clone();
                    self.advance();
                    let right = self.parse_unary()?;
                    left = PyExpr::BinOp(Box::new(left), op, Box::new(right));
                }
                _ => break,
            }
        }
        Ok(left)
    }

    fn parse_unary(&mut self) -> Result<PyExpr, String> {
        if matches!(self.peek(), PyTok::Op(ref op) if op == "-") {
            self.advance();
            let expr = self.parse_postfix()?;
            return Ok(PyExpr::UnaryOp("-".into(), Box::new(expr)));
        }
        self.parse_postfix()
    }

    fn parse_postfix(&mut self) -> Result<PyExpr, String> {
        let mut expr = self.parse_atom()?;
        loop {
            match self.peek() {
                PyTok::Punct('.') => {
                    self.advance(); // consume '.'
                    let method = if let PyTok::Name(name) = self.advance() { name }
                                 else { return Err("Expected method name after '.'".into()); };
                    self.expect_punct('(')?;
                    let args = self.parse_arg_list()?;
                    self.expect_punct(')')?;
                    expr = PyExpr::MethodCall(Box::new(expr), method, args);
                }
                PyTok::Punct('[') => {
                    self.advance();
                    let idx = self.parse_expr()?;
                    self.expect_punct(']')?;
                    expr = PyExpr::Subscript(Box::new(expr), Box::new(idx));
                }
                PyTok::Punct('(') if matches!(&expr, PyExpr::Name(_)) => {
                    // Function call
                    if let PyExpr::Name(name) = expr {
                        self.advance(); // consume '('
                        let args = self.parse_arg_list()?;
                        self.expect_punct(')')?;
                        expr = PyExpr::Call(name, args);
                    } else {
                        break;
                    }
                }
                _ => break,
            }
        }
        Ok(expr)
    }

    fn parse_atom(&mut self) -> Result<PyExpr, String> {
        match self.peek().clone() {
            PyTok::Int(n) => { self.advance(); Ok(PyExpr::IntLit(n)) }
            PyTok::Float(f) => { self.advance(); Ok(PyExpr::FloatLit(f)) }
            PyTok::Str(s) => { self.advance(); Ok(PyExpr::StrLit(s)) }
            PyTok::FStr(parts) => { self.advance(); Ok(PyExpr::FString(parts)) }
            PyTok::Name(ref name) if name == "True" => { self.advance(); Ok(PyExpr::IntLit(1)) }
            PyTok::Name(ref name) if name == "False" => { self.advance(); Ok(PyExpr::IntLit(0)) }
            PyTok::Name(name) => { self.advance(); Ok(PyExpr::Name(name)) }
            PyTok::Punct('(') => {
                self.advance();
                let expr = self.parse_expr()?;
                self.expect_punct(')')?;
                Ok(expr)
            }
            tok => Err(format!("Unexpected token: {:?}", tok)),
        }
    }

    fn parse_arg_list(&mut self) -> Result<Vec<PyExpr>, String> {
        let mut args = Vec::new();
        if matches!(self.peek(), PyTok::Punct(')')) { return Ok(args); }
        args.push(self.parse_expr()?);
        while matches!(self.peek(), PyTok::Punct(',')) {
            self.advance();
            if matches!(self.peek(), PyTok::Punct(')')) { break; }
            args.push(self.parse_expr()?);
        }
        Ok(args)
    }
}

// ── Code Generator: Python AST → .syn ───────────────────────

fn emit_typed_expr(t: &mut Transpiler, expr: &PyExpr) -> Result<(String, PyType), String> {
    match expr {
        PyExpr::IntLit(n) => Ok((n.to_string(), PyType::I64)),
        PyExpr::FloatLit(f) => Ok((format!("{}", f), PyType::F32)),
        PyExpr::StrLit(s) => Ok(t.emit_string_literal(s)),

        PyExpr::FString(parts) => {
            t.needs_strings = true;
            // Check if all literal
            let all_lit = parts.iter().all(|p| matches!(p, FStrPart::Lit(_)));
            if all_lit {
                let full: String = parts.iter().map(|p| match p { FStrPart::Lit(s) => s.as_str(), _ => "" }).collect();
                return Ok(t.emit_string_literal(&full));
            }

            // Has variables — build concat chain
            let mut str_codes: Vec<(String, PyType)> = Vec::new();
            for part in parts {
                match part {
                    FStrPart::Lit(s) if s.is_empty() => continue,
                    FStrPart::Lit(s) => {
                        str_codes.push(t.emit_string_literal(s));
                    }
                    FStrPart::Expr(expr_text) => {
                        // Re-parse the expression inside the f-string
                        let mut lexer = PyLexer::new(expr_text);
                        let tokens = lexer.tokenize();
                        let mut parser = PyParser::new(tokens);
                        let inner_expr = parser.parse_expr().map_err(|e| format!("f-string expr error: {}", e))?;
                        let (code, typ) = emit_typed_expr(t, &inner_expr)?;
                        if typ == PyType::Str {
                            str_codes.push((code, PyType::Str));
                        } else {
                            str_codes.push((format!("call __int_to_str {}", code), PyType::Str));
                        }
                    }
                }
            }
            if str_codes.is_empty() {
                return Ok(t.emit_string_literal(""));
            }
            if str_codes.len() == 1 {
                return Ok(str_codes.into_iter().next().unwrap());
            }
            // Chain concats
            let mut result = str_codes[0].0.clone();
            for sc in &str_codes[1..] {
                result = format!("call __str_concat {} {}", result, sc.0);
            }
            Ok((result, PyType::Str))
        }

        PyExpr::Name(name) => {
            let var = t.get_var(name)?;
            let typ = t.get_var_type(name);
            Ok((var, typ))
        }

        PyExpr::UnaryOp(op, operand) => {
            let (code, typ) = emit_typed_expr(t, operand)?;
            match op.as_str() {
                "-" => {
                    if typ == PyType::F32 { Ok((format!("- 0.0 {}", code), PyType::F32)) }
                    else { Ok((format!("- 0 {}", code), PyType::I64)) }
                }
                "not" => Ok((format!("== {} 0", code), PyType::I64)),
                _ => Err(format!("Unsupported unary op: {}", op)),
            }
        }

        PyExpr::BinOp(left, op, right) => {
            let (lc, lt) = emit_typed_expr(t, left)?;
            let (rc, rt) = emit_typed_expr(t, right)?;

            // String concatenation
            if op == "+" && (lt == PyType::Str || rt == PyType::Str) {
                t.needs_strings = true;
                let lc = if lt != PyType::Str { format!("call __int_to_str {}", lc) } else { lc };
                let rc = if rt != PyType::Str { format!("call __int_to_str {}", rc) } else { rc };
                return Ok((format!("call __str_concat {} {}", lc, rc), PyType::Str));
            }

            match op.as_str() {
                "/" => {
                    let lc = if lt != PyType::F32 { format!("to_f32 {}", lc) } else { lc };
                    let rc = if rt != PyType::F32 { format!("to_f32 {}", rc) } else { rc };
                    Ok((format!("/ {} {}", lc, rc), PyType::F32))
                }
                "//" => Ok((format!("/ {} {}", lc, rc), PyType::I64)),
                "%" => Ok((format!("% {} {}", lc, rc), PyType::I64)),
                "+" | "-" | "*" => {
                    let result_type = if lt == PyType::F32 || rt == PyType::F32 { PyType::F32 } else { PyType::I64 };
                    let lc = if result_type == PyType::F32 && lt == PyType::I64 { format!("to_f32 {}", lc) } else { lc };
                    let rc = if result_type == PyType::F32 && rt == PyType::I64 { format!("to_f32 {}", rc) } else { rc };
                    Ok((format!("{} {} {}", op, lc, rc), result_type))
                }
                _ => Err(format!("Unsupported binary op: {}", op)),
            }
        }

        PyExpr::Compare(left, op, right) => {
            let (lc, lt) = emit_typed_expr(t, left)?;
            let (rc, rt) = emit_typed_expr(t, right)?;
            // String comparison
            if lt == PyType::Str || rt == PyType::Str {
                match op.as_str() {
                    "==" => return Ok((format!("== {} {}", lc, rc), PyType::I64)),
                    "!=" => return Ok((format!("!= {} {}", lc, rc), PyType::I64)),
                    _ => return Err("String comparison only supports == and !=".into()),
                }
            }
            let (lc, rc) = if lt == PyType::F32 || rt == PyType::F32 {
                (if lt == PyType::I64 { format!("to_f32 {}", lc) } else { lc },
                 if rt == PyType::I64 { format!("to_f32 {}", rc) } else { rc })
            } else { (lc, rc) };
            Ok((format!("{} {} {}", op, lc, rc), PyType::I64))
        }

        PyExpr::BoolOp(op, values) => {
            let typed: Vec<(String, PyType)> = values.iter()
                .map(|v| emit_typed_expr(t, v))
                .collect::<Result<Vec<_>, _>>()?;
            let codes: Vec<String> = typed.into_iter().map(|(c, _)| c).collect();
            if op == "and" {
                let mut result = codes.last().unwrap().clone();
                for v in codes[..codes.len()-1].iter().rev() {
                    result = format!("if {} [ {} ] [ 0 ]", v, result);
                }
                Ok((result, PyType::I64))
            } else {
                let mut result = codes.last().unwrap().clone();
                for v in codes[..codes.len()-1].iter().rev() {
                    result = format!("if {} [ {} ] [ {} ]", v, v, result);
                }
                Ok((result, PyType::I64))
            }
        }

        PyExpr::Call(name, args) => emit_call(t, name, args),
        PyExpr::MethodCall(obj, method, args) => emit_method_call(t, obj, method, args),
        PyExpr::IfExpr(body, cond, orelse) => {
            let (cc, _) = emit_typed_expr(t, cond)?;
            let (bc, bt) = emit_typed_expr(t, body)?;
            let (oc, _) = emit_typed_expr(t, orelse)?;
            Ok((format!("if {} [ {} ] [ {} ]", cc, bc, oc), bt))
        }
        _ => Err(format!("Unsupported expression")),
    }
}

fn emit_call(t: &mut Transpiler, name: &str, args: &[PyExpr]) -> Result<(String, PyType), String> {
    match name {
        "print" => {
            if args.len() != 1 { return Err("print() with single argument only".into()); }
            let (val, typ) = emit_typed_expr(t, &args[0])?;
            match typ {
                PyType::Str => { t.needs_strings = true; Ok((format!("call __str_print {}", val), PyType::I64)) }
                PyType::F32 => Ok((format!("call print_f32 {}", val), PyType::I64)),
                _ => Ok((format!("call print_i64 {}", val), PyType::I64)),
            }
        }
        "len" => {
            if args.len() != 1 { return Err("len() takes 1 argument".into()); }
            if let PyExpr::Name(ref n) = args[0] {
                let vtype = t.get_var_type(n);
                if vtype == PyType::Str {
                    let var = t.get_var(n)?;
                    return Ok((format!("& {} 4294967295", var), PyType::I64));
                }
            }
            if let PyExpr::StrLit(ref s) = args[0] {
                return Ok((s.len().to_string(), PyType::I64));
            }
            Err("len() only supports string variables and literals".into())
        }
        "str" => {
            if args.len() != 1 { return Err("str() takes 1 argument".into()); }
            let (x, xt) = emit_typed_expr(t, &args[0])?;
            if xt == PyType::Str { return Ok((x, PyType::Str)); }
            t.needs_strings = true;
            Ok((format!("call __int_to_str {}", x), PyType::Str))
        }
        "int" => {
            if args.len() != 1 { return Err("int() takes 1 argument".into()); }
            let (x, _) = emit_typed_expr(t, &args[0])?;
            Ok((x, PyType::I64))
        }
        "float" => {
            if args.len() != 1 { return Err("float() takes 1 argument".into()); }
            let (x, xt) = emit_typed_expr(t, &args[0])?;
            if xt == PyType::I64 { Ok((format!("to_f32 {}", x), PyType::F32)) }
            else { Ok((x, PyType::F32)) }
        }
        "abs" => {
            if args.len() != 1 { return Err("abs() takes 1 argument".into()); }
            let (x, xt) = emit_typed_expr(t, &args[0])?;
            let zero = if xt == PyType::F32 { "0.0" } else { "0" };
            Ok((format!("if > {} {} [ {} ] [ - {} {} ]", x, zero, x, zero, x), xt))
        }
        "min" => {
            if args.len() != 2 { return Err("min() takes 2 arguments".into()); }
            let (a, at) = emit_typed_expr(t, &args[0])?;
            let (b, _) = emit_typed_expr(t, &args[1])?;
            Ok((format!("if < {} {} [ {} ] [ {} ]", a, b, a, b), at))
        }
        "max" => {
            if args.len() != 2 { return Err("max() takes 2 arguments".into()); }
            let (a, at) = emit_typed_expr(t, &args[0])?;
            let (b, _) = emit_typed_expr(t, &args[1])?;
            Ok((format!("if > {} {} [ {} ] [ {} ]", a, b, a, b), at))
        }
        _ => Err(format!("Unsupported function: {}", name)),
    }
}

fn emit_method_call(t: &mut Transpiler, obj: &PyExpr, method: &str, args: &[PyExpr]) -> Result<(String, PyType), String> {
    let (obj_code, obj_type) = emit_typed_expr(t, obj)?;

    // String methods
    if obj_type == PyType::Str {
        t.needs_strings = true;
        let ret_type = match method {
            "startswith" | "endswith" | "find" | "count" => PyType::I64,
            _ => PyType::Str,
        };
        let ffi_name = match method {
            "upper" => "__ffi_str_upper",
            "lower" => "__ffi_str_lower",
            "strip" => "__ffi_str_strip",
            "lstrip" => "__ffi_str_lstrip",
            "rstrip" => "__ffi_str_rstrip",
            "replace" => "__ffi_str_replace",
            "startswith" => "__ffi_str_startswith",
            "endswith" => "__ffi_str_endswith",
            "find" => "__ffi_str_find",
            "count" => "__ffi_str_count",
            _ => return Err(format!("Unsupported string method: .{}()", method)),
        };

        match args.len() {
            0 => Ok((format!("call {} {}", ffi_name, obj_code), ret_type)),
            1 => {
                let (a0, _) = emit_typed_expr(t, &args[0])?;
                Ok((format!("call {} {} {}", ffi_name, obj_code, a0), ret_type))
            }
            2 => {
                let (a0, _) = emit_typed_expr(t, &args[0])?;
                let (a1, _) = emit_typed_expr(t, &args[1])?;
                Ok((format!("call {} {} {} {}", ffi_name, obj_code, a0, a1), ret_type))
            }
            _ => Err(format!(".{}() takes at most 2 arguments", method)),
        }
    } else {
        Err(format!("Unsupported method call: .{}()", method))
    }
}

fn emit_stmt(t: &mut Transpiler, stmt: &PyStmt) -> Result<String, String> {
    match stmt {
        PyStmt::Assign(name, value) => {
            let (val_code, val_type) = emit_typed_expr(t, value)?;
            if val_type == PyType::Str { t.str_vars.insert(name.clone()); }
            t.set_var_type(name, val_type);
            if t.var_map.contains_key(name) {
                let var = t.get_var(name)?;
                Ok(format!("set {} {}", var, val_code))
            } else {
                let var = t.declare_var(name);
                Ok(format!("let {} {}", var, val_code))
            }
        }
        PyStmt::AugAssign(name, op, value) => {
            let var = t.get_var(name)?;
            let (val_code, _) = emit_typed_expr(t, value)?;
            Ok(format!("set {} {} {} {}", var, op, var, val_code))
        }
        PyStmt::Expr(expr) => {
            let (code, _) = emit_typed_expr(t, expr)?;
            Ok(code)
        }
        PyStmt::If(cond, body, orelse) => {
            let (cc, _) = emit_typed_expr(t, cond)?;
            let body_code: Vec<String> = body.iter().map(|s| emit_stmt(t, s)).collect::<Result<_, _>>()?;
            let body_str = body_code.join(" ");
            if orelse.is_empty() {
                Ok(format!("if {} [ {} ] [ 0 ]", cc, body_str))
            } else {
                let else_code: Vec<String> = orelse.iter().map(|s| emit_stmt(t, s)).collect::<Result<_, _>>()?;
                Ok(format!("if {} [ {} ] [ {} ]", cc, body_str, else_code.join(" ")))
            }
        }
        PyStmt::While(cond, body) => {
            let (cc, _) = emit_typed_expr(t, cond)?;
            let body_code: Vec<String> = body.iter().map(|s| emit_stmt(t, s)).collect::<Result<_, _>>()?;
            Ok(format!("while {} [ {} 0 ]", cc, body_code.join(" ")))
        }
        PyStmt::For(var, start, end, body) => {
            let loop_var = t.declare_var(var);
            t.set_var_type(var, PyType::I64);
            let (start_code, _) = emit_typed_expr(t, start)?;
            let (end_code, _) = emit_typed_expr(t, end)?;
            let body_code: Vec<String> = body.iter().map(|s| emit_stmt(t, s)).collect::<Result<_, _>>()?;
            Ok(format!("let {} {} while < {} {} [ {} set {} + {} 1 0 ]",
                loop_var, start_code, loop_var, end_code, body_code.join(" "), loop_var, loop_var))
        }
        PyStmt::Pass => Ok("0".into()),
    }
}

fn emit_string_helpers(_t: &Transpiler) -> String {
    let mut parts = Vec::new();

    // Always emit print + print_nl for string programs
    parts.push(r#"@import_ffi "env" "print" 2 print"#.to_string());
    parts.push(r#"@import_ffi "env" "print_nl" 0 print_nl"#.to_string());
    parts.push(r#"@import_ffi "env" "str_concat" 2 __str_concat"#.to_string());
    parts.push(r#"@import_ffi "env" "int_to_str" 1 __int_to_str"#.to_string());
    parts.push(r#"@import_ffi "env" "str_upper" 1 __ffi_str_upper"#.to_string());
    parts.push(r#"@import_ffi "env" "str_lower" 1 __ffi_str_lower"#.to_string());
    parts.push(r#"@import_ffi "env" "str_strip" 1 __ffi_str_strip"#.to_string());
    parts.push(r#"@import_ffi "env" "str_lstrip" 1 __ffi_str_lstrip"#.to_string());
    parts.push(r#"@import_ffi "env" "str_rstrip" 1 __ffi_str_rstrip"#.to_string());
    parts.push(r#"@import_ffi "env" "str_replace" 3 __ffi_str_replace"#.to_string());
    parts.push(r#"@import_ffi "env" "str_startswith" 2 __ffi_str_startswith"#.to_string());
    parts.push(r#"@import_ffi "env" "str_endswith" 2 __ffi_str_endswith"#.to_string());
    parts.push(r#"@import_ffi "env" "str_find" 2 __ffi_str_find"#.to_string());
    parts.push(r#"@import_ffi "env" "str_count" 2 __ffi_str_count"#.to_string());

    // __str_print helper: unpacks (ptr << 32 | len) and calls print(ptr, len) + print_nl()
    parts.push("@f 1 __str_print [ call print >> $0 32 & $0 4294967295 call print_nl 0 ]".to_string());

    parts.join(" ")
}

// ── Public API ──────────────────────────────────────────────

/// Return true if the source contains patterns we know the .syn transpiler
/// mishandles. When true, we reject early and the caller falls back to
/// real CPython-WASI — which handles arbitrary Python correctly.
///
/// This is a CORRECTNESS gate, not a performance gate. False positives cost
/// ~60ms (CPython path); false negatives produce wrong output silently.
fn pretrap_unsafe_patterns(source: &str) -> bool {
    // Known-safe stdlib modules the transpiler can emit FFIs for.
    const SAFE_IMPORTS: &[&str] = &["math", "numpy", "json", "sys", "os"];

    for line in source.lines() {
        let l = line.trim();

        // 1. Unsupported imports — force CPython-WASI so any Python-native
        //    stdlib (hashlib, re, datetime, collections, ...) just works.
        if l.starts_with("import ") || l.starts_with("from ") {
            let rest = l
                .strip_prefix("import ").or_else(|| l.strip_prefix("from "))
                .unwrap_or("");
            // First identifier after 'import' / 'from'
            let name: String = rest
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            if !name.is_empty() && !SAFE_IMPORTS.contains(&name.as_str()) {
                return true;
            }
        }

        // 2. Bytes literals — transpiler treats as int/string incorrectly.
        //    Catches b'...', b"...", rb"...", B'...' etc.
        if l.contains("b'") || l.contains("b\"") || l.contains("B'") || l.contains("B\"") {
            // Avoid false positive on contiguous 'b' inside identifier (foo_b"...")
            // Cheap heuristic: require a word boundary before 'b'
            let bytes = l.as_bytes();
            for i in 0..bytes.len().saturating_sub(1) {
                let c = bytes[i];
                let n = bytes[i + 1];
                if (c == b'b' || c == b'B') && (n == b'\'' || n == b'"') {
                    if i == 0 || !(bytes[i - 1].is_ascii_alphanumeric() || bytes[i - 1] == b'_') {
                        return true;
                    }
                }
            }
        }

        // 3. String multiplication — 'x' * N and N * 'x' patterns.
        //    Transpiler does int math, not string repeat.
        if has_string_multiplication(l) {
            return true;
        }

        // 4. sys.exit(N) — transpiler evaluates as fn call, drops the exit.
        if l.contains("sys.exit") || l.contains("os._exit") || l.contains("quit(") {
            return true;
        }

        // 5. f-string with format specs (:d, :04, :.2f, etc.) — not reliably supported.
        if l.contains("f'") || l.contains("f\"") {
            // Detect format spec inside f-string: `{var:spec}`
            if l.contains("{") && l.contains(":") && l.contains("}") {
                // Quick-check: any substring like "{x:" signals format spec
                let bytes = l.as_bytes();
                let mut in_brace = false;
                for i in 0..bytes.len() {
                    if bytes[i] == b'{' { in_brace = true; }
                    else if bytes[i] == b'}' { in_brace = false; }
                    else if in_brace && bytes[i] == b':' {
                        return true;
                    }
                }
            }
        }
    }
    false
}

/// Detect `'x' * N` or `"str" * n` or `N * 'x'` patterns.
fn has_string_multiplication(line: &str) -> bool {
    // Scan for quote-* or *-quote sequences with at most spaces in between.
    let bytes = line.as_bytes();
    let mut in_str = false;
    let mut str_char: u8 = 0;
    let mut escape = false;
    for i in 0..bytes.len() {
        let c = bytes[i];
        if escape { escape = false; continue; }
        if in_str {
            if c == b'\\' { escape = true; continue; }
            if c == str_char {
                in_str = false;
                // Look ahead for whitespace then '*'
                let mut j = i + 1;
                while j < bytes.len() && bytes[j] == b' ' { j += 1; }
                if j < bytes.len() && bytes[j] == b'*' {
                    // '*' not '**' (exponent)
                    if j + 1 >= bytes.len() || bytes[j + 1] != b'*' {
                        return true;
                    }
                }
            }
        } else if c == b'\'' || c == b'"' {
            in_str = true;
            str_char = c;
            // Look BEHIND for '*' then whitespace (int * 'str' pattern)
            if i > 0 {
                let mut j = i;
                while j > 0 && bytes[j - 1] == b' ' { j -= 1; }
                if j > 0 && bytes[j - 1] == b'*' {
                    if j < 2 || bytes[j - 2] != b'*' {
                        return true;
                    }
                }
            }
        }
    }
    false
}

pub fn transpile_python(source: &str) -> Result<String, String> {
    // Hardening: reject patterns that the transpiler silently mis-handles.
    // These all fall through to real CPython-WASI, which is the correct path.
    // We err on the side of rejection — false positives just force CPython
    // (still fast, ~63ms); false negatives produce WRONG RESULTS silently,
    // which broke customer trust in the Oct 2026 stress test.
    if pretrap_unsafe_patterns(source) {
        return Err("Pattern not safe for .syn transpile; delegating to CPython-WASI".to_string());
    }

    let mut lexer = PyLexer::new(source);
    let tokens = lexer.tokenize();
    let mut parser = PyParser::new(tokens);
    let stmts = parser.parse_program()?;

    let mut t = Transpiler::new();

    let mut body_parts = Vec::new();
    for stmt in &stmts {
        let code = emit_stmt(&mut t, stmt)?;
        if !code.is_empty() {
            body_parts.push(code);
        }
    }

    let main_code = if body_parts.is_empty() { "0".to_string() } else { body_parts.join(" ") };

    let mut parts = Vec::new();

    if t.needs_strings {
        parts.push(emit_string_helpers(&t));
    }

    parts.push(format!("@schema main : () -> i64 @f 0 main [ {} ]", main_code));

    // Emit data sections
    for (offset, s) in &t.string_data {
        parts.push(format!("(data {} \"{}\")", offset, s));
    }

    Ok(parts.join(" "))
}
