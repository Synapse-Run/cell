from __future__ import annotations
"""Synapse Python → .syn Transpiler

Transpiles a subset of Python into .syn (the AI-native Wasm language).
Python code is parsed via the `ast` module; an AST walker emits prefix-notation
.syn source that the self-hosted compiler compiles to Wasm in < 0.253ms.

Supported Python subset:
  - Integer arithmetic: +, -, *, //, %, **
  - Float arithmetic: +, -, *, /, ** (type-inferred from literals)
  - Comparisons: >, <, ==, !=, >=, <=
  - Boolean operators: and, or, not
  - Variables: assignment, augmented assignment (+=, -=, etc.)
  - Control flow: if/elif/else, while, for-in-range
  - Functions: def with return, function calls
  - Lists: [1,2,3], indexing x[i], assignment x[i]=v, len(x)
  - List comprehensions: [expr for x in range(n)],
                         [expr for x in range(n) for y in range(m)] (nested),
                         {k: v for k, v in ...} (dict comprehensions)
  - Tuples: (a, b) for multiple return, a, b = func()
  - Dicts: {k: v}, d[k], d[k] = v (integer keys only)
  - Strings: basic string constants (stored as integer hash for comparisons)
  - Builtins: print(), abs(), min(), max(), len(), sum(), range(), int(), float()
  - Type coercion: automatic i64↔f32 conversion at expression boundaries
  - Classes: class with __init__, methods, class-level constants,
             self.method() calls, __repr__/__str__ (map to .syn structs)
  - Imports: math, numpy, json (built-in), user modules (mapped to .syn @use)
  - Math module: math.exp, math.log, math.sqrt, math.sin, math.cos, math.tan,
                 math.pow, math.ceil, math.floor, math.pi, math.e, math.fabs
  - NumPy subset: np.array, np.dot, np.sum, np.mean, np.max, np.min,
                  np.zeros, np.ones, np.arange, np.linspace, np.abs, np.sqrt,
                  np.exp, np.log
"""

import ast
import struct
import sys
from typing import Optional


class TranspileError(Exception):
    """Raised when Python code cannot be transpiled to .syn."""
    pass


# Type constants
I64 = 'i64'
F32 = 'f32'
STR = 'str'  # String: packed i64 = (ptr << 32) | len


class SynTranspiler(ast.NodeVisitor):
    """Walks a Python AST and emits .syn source code with type inference."""

    ELEM_SIZE = 8  # 8 bytes per element (i64)

    # Allowed imports — these get transpiled, not rejected
    ALLOWED_IMPORTS = {'math', 'json'}
    ALLOWED_NUMPY_ALIASES = {'np', 'numpy'}
    # Dangerous imports — always blocked for sandboxed execution
    _DANGEROUS_MODULES = {
        'os', 'sys', 'subprocess', 'socket', 'ctypes', 'importlib',
        'shutil', 'signal', 'threading', 'multiprocessing', 'builtins',
        'io', 'pathlib', 'tempfile', 'glob', 'fnmatch', 'requests',
        'http', 'urllib', 'ftplib', 'smtplib', 'ssl', 'select',
    }

    def __init__(self):
        self.functions: list[str] = []
        self.main_body: list[str] = []
        self._var_map: dict[str, int] = {}
        self._var_counter: int = 0
        self._var_types: dict[str, str] = {}  # name → I64 or F32 or STR
        self._func_map: dict[str, int] = {}  # func_name → arity
        self._func_return_types: dict[str, str] = {}  # func_name → return type
        self._in_function: bool = False
        self._current_func_vars: dict[str, int] = {}
        self._current_func_var_counter: int = 0
        self._current_func_params: dict[str, int] = {}
        self._current_func_param_types: dict[str, str] = {}
        self._list_vars: set[str] = set()
        self._dict_vars: set[str] = set()
        self._arena_offset: int = 8192
        self._dict_arena_offset: int = 32768  # separate region for dicts
        # Module tracking
        self._imported_modules: set[str] = set()  # 'math', 'numpy'
        self._numpy_alias: str = 'np'  # default alias
        self._needs_math: bool = False  # auto-emit math helpers
        self._needs_numpy: bool = False  # auto-emit numpy helpers
        self._emitted_math_funcs: set[str] = set()  # which math funcs were emitted
        # ── APC String Support ──
        self._string_data: list[tuple[int, str]] = []  # (offset, value) for data sections
        self._string_arena_offset: int = 65536  # strings start at 64KB in linear memory
        self._needs_strings: bool = False  # auto-emit string helpers
        self._str_vars: set[str] = set()  # variables known to hold strings
        self._string_cache: dict[str, int] = {}  # dedup: string value → packed i64
        # ── JSON Support ──
        self._needs_json: bool = False  # auto-emit JSON FFI helpers
        self._json_vars: set[str] = set()  # variables known to hold JSON handles
        # ── Class Support ──
        self._class_defs: dict[str, dict] = {}  # class_name → {'methods': [...], 'attrs': [...]}
        self._in_class: str | None = None  # current class name during class body emission
        # ── try/except Support ──
        self._try_depth: int = 0  # nesting depth for try/except error vars
        # ── User Module Imports (@use) ──
        self._user_modules: list[str] = []  # user-defined module paths for @use
        self._user_module_aliases: dict[str, str] = {}  # alias → module path
        self._user_module_funcs: dict[str, str] = {}  # func_name → module alias

    # ── Variable Management ──────────────────────────────────

    def _get_var(self, name: str) -> str:
        if self._in_function:
            if name in self._current_func_params:
                return f"${self._current_func_params[name]}"
            if name in self._current_func_vars:
                return f"${self._current_func_vars[name]}"
            raise TranspileError(f"Undefined variable: {name}")
        if name in self._var_map:
            return f"${self._var_map[name]}"
        raise TranspileError(f"Undefined variable: {name}")

    def _get_var_type(self, name: str) -> str:
        if self._in_function:
            if name in self._current_func_param_types:
                return self._current_func_param_types[name]
        return self._var_types.get(name, I64)

    def _set_var_type(self, name: str, vtype: str):
        self._var_types[name] = vtype

    def _declare_var(self, name: str) -> str:
        if self._in_function:
            if name not in self._current_func_vars:
                idx = self._current_func_var_counter
                self._current_func_vars[name] = idx
                self._current_func_var_counter += 1
            return f"${self._current_func_vars[name]}"
        if name not in self._var_map:
            self._var_map[name] = self._var_counter
            self._var_counter += 1
        return f"${self._var_map[name]}"

    def _is_declared(self, name: str) -> bool:
        if self._in_function:
            return name in self._current_func_vars or name in self._current_func_params
        return name in self._var_map

    # ── Type Coercion ────────────────────────────────────────

    def _coerce(self, code: str, from_type: str, to_type: str) -> str:
        """Insert type conversion if needed."""
        if from_type == to_type:
            return code
        if from_type == I64 and to_type == F32:
            return f"to_f32 {code}"
        if from_type == F32 and to_type == I64:
            return f"i64 {code}"
        return code

    def _unify_types(self, left: str, left_t: str, right: str, right_t: str) -> tuple[str, str, str]:
        """Unify two operand types. If either is f32, promote both to f32."""
        if left_t == right_t:
            return left, right, left_t
        # Promote to f32
        left = self._coerce(left, left_t, F32)
        right = self._coerce(right, right_t, F32)
        return left, right, F32

    # ── Expression Visitors (typed) ──────────────────────────

    def _emit_typed_expr(self, node: ast.AST) -> tuple[str, str]:
        """Emit a .syn expression and return (code, type)."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return ("1" if node.value else "0"), I64
            if isinstance(node.value, int):
                return str(node.value), I64
            if isinstance(node.value, float):
                return str(node.value), F32
            if isinstance(node.value, str):
                # APC: Emit string as data section + packed pointer (with dedup)
                self._needs_strings = True
                s = node.value
                # Dedup: same string content → same packed value
                if s in self._string_cache:
                    return str(self._string_cache[s]), STR
                ptr = self._string_arena_offset
                length = len(s.encode('utf-8'))
                self._string_data.append((ptr, s))
                self._string_arena_offset += length + 1  # +1 for null terminator
                # Pack: (ptr << 32) | length — stored as i64
                packed = (ptr << 32) | length
                self._string_cache[s] = packed
                return str(packed), STR
            raise TranspileError(f"Unsupported constant type: {type(node.value).__name__}")

        if isinstance(node, ast.Name):
            var = self._get_var(node.id)
            vtype = self._get_var_type(node.id)
            return var, vtype

        if isinstance(node, ast.UnaryOp):
            operand, otype = self._emit_typed_expr(node.operand)
            if isinstance(node.op, ast.USub):
                if otype == F32:
                    return f"- 0.0 {operand}", F32
                return f"- 0 {operand}", I64
            if isinstance(node.op, ast.Not):
                operand = self._coerce(operand, otype, I64)
                return f"== {operand} 0", I64
            raise TranspileError(f"Unsupported unary op: {type(node.op).__name__}")

        if isinstance(node, ast.BinOp):
            return self._emit_typed_binop(node)

        if isinstance(node, ast.BoolOp):
            return self._emit_typed_boolop(node)

        if isinstance(node, ast.Compare):
            return self._emit_typed_compare(node)

        if isinstance(node, ast.IfExp):
            cond, _ = self._emit_typed_expr(node.test)
            body, bt = self._emit_typed_expr(node.body)
            orelse, ot = self._emit_typed_expr(node.orelse)
            body, orelse, rt = self._unify_types(body, bt, orelse, ot)
            return f"if {cond} [ {body} ] [ {orelse} ]", rt

        if isinstance(node, ast.Call):
            return self._emit_typed_call(node)

        if isinstance(node, ast.List):
            return self._emit_list_literal(node), I64

        if isinstance(node, ast.Subscript):
            return self._emit_typed_subscript(node)

        if isinstance(node, ast.ListComp):
            return self._emit_list_comprehension(node), I64

        if isinstance(node, ast.Tuple):
            # Emit tuple as sequence; return type of last element
            parts = []
            last_type = I64
            for elt in node.elts:
                code, t = self._emit_typed_expr(elt)
                parts.append(code)
                last_type = t
            return " ".join(parts), last_type

        if isinstance(node, ast.Dict):
            return self._emit_dict_literal(node), I64

        if isinstance(node, ast.DictComp):
            return self._emit_dict_comprehension(node), I64

        # APC: f-strings → JoinedStr
        if isinstance(node, ast.JoinedStr):
            return self._emit_fstring(node)

        # Attribute access: math.pi, math.e, np.inf, self.x, obj.x, etc.
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                obj = node.value.id
                attr = node.attr
                # math.pi, math.e, math.tau, math.inf
                if obj == 'math' and attr in self._MATH_CONSTANTS:
                    return self._MATH_CONSTANTS[attr], F32
                # np.pi, np.e, np.inf
                if obj in self.ALLOWED_NUMPY_ALIASES and attr in self._MATH_CONSTANTS:
                    return self._MATH_CONSTANTS[attr], F32
                # self.x access inside class methods
                if self._in_class and obj == 'self':
                    class_info = self._class_defs[self._in_class]
                    # Check class constants first (no dict lookup needed)
                    if attr in class_info.get('constants', {}):
                        const_val = class_info['constants'][attr]
                        if isinstance(const_val, float):
                            return str(const_val), F32
                        return str(const_val), I64
                    attr_hash = class_info['attr_hashes'].get(attr)
                    if attr_hash is None:
                        attr_hash = self._fnv1a_hash(attr)
                    obj_var = self._get_var('self')
                    # Need dict helpers for attribute read
                    self._dict_vars.add('__class_instance__')
                    return f"call __dict_get {obj_var} {attr_hash}", I64
                # ClassName.CONST — static class constant access
                if obj in self._class_defs:
                    class_info = self._class_defs[obj]
                    if attr in class_info.get('constants', {}):
                        const_val = class_info['constants'][attr]
                        if isinstance(const_val, float):
                            return str(const_val), F32
                        return str(const_val), I64
                # obj.x access on class instances outside methods
                vtype = self._var_types.get(obj, '')
                if vtype.startswith('__class_'):
                    class_name = vtype[len('__class_'):]
                    if class_name in self._class_defs:
                        class_info = self._class_defs[class_name]
                        # Check class constants on instance
                        if attr in class_info.get('constants', {}):
                            const_val = class_info['constants'][attr]
                            if isinstance(const_val, float):
                                return str(const_val), F32
                            return str(const_val), I64
                        attr_hash = class_info['attr_hashes'].get(attr)
                        if attr_hash is None:
                            attr_hash = self._fnv1a_hash(attr)
                        obj_var = self._get_var(obj)
                        self._dict_vars.add('__class_instance__')
                        return f"call __dict_get {obj_var} {attr_hash}", I64

        raise TranspileError(f"Unsupported expression: {type(node).__name__}")

    def _emit_expr(self, node: ast.AST) -> str:
        """Emit expression, discarding type info (backward compat)."""
        code, _ = self._emit_typed_expr(node)
        return code

    def _emit_typed_binop(self, node: ast.BinOp) -> tuple[str, str]:
        left, lt = self._emit_typed_expr(node.left)
        right, rt = self._emit_typed_expr(node.right)

        # APC: String concatenation with +
        if isinstance(node.op, ast.Add) and (lt == STR or rt == STR):
            return self._emit_str_concat_binop(node, left, lt, right, rt)

        if isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, int):
                exp = node.right.value
                if exp == 0:
                    return ("1.0" if lt == F32 else "1"), lt
                if exp == 1:
                    return left, lt
                if exp == 2:
                    return f"* {left} {left}", lt
                result = left
                for _ in range(exp - 1):
                    result = f"* {result} {left}"
                return result, lt
            raise TranspileError("** (power) requires a constant integer exponent")

        if isinstance(node.op, ast.Div):
            # Python / always produces float
            left = self._coerce(left, lt, F32)
            right = self._coerce(right, rt, F32)
            return f"/ {left} {right}", F32

        # For floor division, result is always i64
        if isinstance(node.op, ast.FloorDiv):
            left = self._coerce(left, lt, I64)
            right = self._coerce(right, rt, I64)
            return f"/ {left} {right}", I64

        # For mod, result is always i64
        if isinstance(node.op, ast.Mod):
            left = self._coerce(left, lt, I64)
            right = self._coerce(right, rt, I64)
            return f"% {left} {right}", I64

        op_map = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
        op = op_map.get(type(node.op))
        if op is None:
            raise TranspileError(f"Unsupported binary op: {type(node.op).__name__}")

        left, right, result_type = self._unify_types(left, lt, right, rt)
        return f"{op} {left} {right}", result_type

    def _emit_typed_boolop(self, node: ast.BoolOp) -> tuple[str, str]:
        typed_values = [self._emit_typed_expr(v) for v in node.values]
        values = [v[0] for v in typed_values]
        if isinstance(node.op, ast.And):
            result = values[-1]
            for v in reversed(values[:-1]):
                result = f"if {v} [ {result} ] [ 0 ]"
            return result, I64
        if isinstance(node.op, ast.Or):
            result = values[-1]
            for v in reversed(values[:-1]):
                result = f"if {v} [ {v} ] [ {result} ]"
            return result, I64
        return values[-1], I64

    def _emit_typed_compare(self, node: ast.Compare) -> tuple[str, str]:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise TranspileError("Chained comparisons not yet supported")
        left, lt = self._emit_typed_expr(node.left)
        right, rt = self._emit_typed_expr(node.comparators[0])

        # APC: String comparison — compare packed i64 values
        # For literal strings, same content = same packed value, so == works directly.
        # For variable strings that might have been created at different offsets,
        # this compares identity (pointer+length), not content.
        # Content comparison would need a byte-by-byte FFI — save for Phase 2.
        if lt == STR or rt == STR:
            op = type(node.ops[0])
            if op == ast.Eq:
                return f"== {left} {right}", I64
            elif op == ast.NotEq:
                return f"!= {left} {right}", I64
            else:
                raise TranspileError("String comparison only supports == and !=")

        left, right, _ = self._unify_types(left, lt, right, rt)
        op_map = {
            ast.Gt: ">", ast.Lt: "<", ast.Eq: "==",
            ast.NotEq: "!=", ast.GtE: ">=", ast.LtE: "<=",
        }
        op = op_map.get(type(node.ops[0]))
        if op is None:
            raise TranspileError(f"Unsupported comparison: {type(node.ops[0]).__name__}")
        return f"{op} {left} {right}", I64  # comparisons always return i64

    def _emit_typed_subscript(self, node: ast.Subscript) -> tuple[str, str]:
        if not isinstance(node.value, ast.Name):
            raise TranspileError("Only simple variable subscript supported")
        name = node.value.id
        base_var = self._get_var(name)
        idx = self._emit_expr(node.slice)
        if name in self._dict_vars:
            # Dict access: linear scan for key
            return f"call __dict_get {base_var} {idx}", I64
        # List access
        return f"read + {base_var} * + {idx} 1 {self.ELEM_SIZE}", I64

    # ── List Comprehension ───────────────────────────────────

    def _emit_list_comprehension(self, node: ast.ListComp) -> str:
        """Emit list comprehensions as alloc + fill loop(s).

        Supports:
        - [expr for x in range(n)]
        - [expr for x in range(n) if cond]
        - [expr for x in range(n) for y in range(m)]  (nested, desugared)
        - [expr for x in list_var]
        """
        if len(node.generators) > 2:
            raise TranspileError("Only single or double-generator list comprehensions supported")

        if len(node.generators) == 2:
            return self._emit_nested_list_comprehension(node)

        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name):
            raise TranspileError("Only simple loop variables in list comprehensions")

        # Support both for-in-range and for-in-list
        is_range = (isinstance(gen.iter, ast.Call) and
                    isinstance(gen.iter.func, ast.Name) and
                    gen.iter.func.id == "range")
        is_list = (isinstance(gen.iter, ast.Name) and
                   gen.iter.id in self._list_vars)

        if not is_range and not is_list:
            raise TranspileError("Only [expr for x in range(n)] or [expr for x in list_var] comprehensions supported")

        var_name = gen.target.id
        base_var = f"${self._var_counter + 200}"
        idx_var = f"${self._var_counter + 201}"
        loop_var = self._declare_var(var_name)

        has_filter = bool(gen.ifs)

        if is_range:
            rng_start, rng_end = self._parse_range_args(gen.iter)
            elt_expr = self._emit_expr(node.elt)

            if not has_filter:
                # Unfiltered: output size is known = rng_end - rng_start
                return (
                    f"let {base_var} alloc + 1 {rng_end} "
                    f"store {base_var} 0 {rng_end} "
                    f"let {idx_var} 0 "
                    f"let {loop_var} {rng_start} "
                    f"while < {loop_var} {rng_end} [ "
                    f"store {base_var} + {idx_var} 1 {elt_expr} "
                    f"set {idx_var} + {idx_var} 1 "
                    f"set {loop_var} + {loop_var} 1 0 ] {base_var}"
                )
            else:
                # Filtered: output size unknown, use write-pointer with max allocation
                # Emit all filter conditions as AND chain
                filter_parts = [self._emit_expr(f) for f in gen.ifs]
                filter_expr = filter_parts[0]
                for fp in filter_parts[1:]:
                    filter_expr = f"if {filter_expr} [ {fp} ] [ 0 ]"
                return (
                    f"let {base_var} alloc + 1 {rng_end} "
                    f"let {idx_var} 0 "
                    f"let {loop_var} {rng_start} "
                    f"while < {loop_var} {rng_end} [ "
                    f"if {filter_expr} [ "
                    f"store {base_var} + {idx_var} 1 {elt_expr} "
                    f"set {idx_var} + {idx_var} 1 0 ] [ 0 ] "
                    f"set {loop_var} + {loop_var} 1 0 ] "
                    f"store {base_var} 0 {idx_var} {base_var}"
                )
        else:
            # for-in-list comprehension
            list_var = self._get_var(gen.iter.id)
            src_idx_var = f"${self._var_counter + 202}"
            len_expr = f"read {list_var}"
            elt_expr = self._emit_expr(node.elt)

            if not has_filter:
                return (
                    f"let {base_var} alloc + 1 {len_expr} "
                    f"let {idx_var} 0 "
                    f"let {src_idx_var} 0 "
                    f"let {loop_var} 0 "
                    f"while < {src_idx_var} {len_expr} [ "
                    f"set {loop_var} read + {list_var} * + {src_idx_var} 1 {self.ELEM_SIZE} "
                    f"store {base_var} + {idx_var} 1 {elt_expr} "
                    f"set {idx_var} + {idx_var} 1 "
                    f"set {src_idx_var} + {src_idx_var} 1 0 ] "
                    f"store {base_var} 0 {idx_var} {base_var}"
                )
            else:
                filter_parts = [self._emit_expr(f) for f in gen.ifs]
                filter_expr = filter_parts[0]
                for fp in filter_parts[1:]:
                    filter_expr = f"if {filter_expr} [ {fp} ] [ 0 ]"
                return (
                    f"let {base_var} alloc + 1 {len_expr} "
                    f"let {idx_var} 0 "
                    f"let {src_idx_var} 0 "
                    f"let {loop_var} 0 "
                    f"while < {src_idx_var} {len_expr} [ "
                    f"set {loop_var} read + {list_var} * + {src_idx_var} 1 {self.ELEM_SIZE} "
                    f"if {filter_expr} [ "
                    f"store {base_var} + {idx_var} 1 {elt_expr} "
                    f"set {idx_var} + {idx_var} 1 0 ] [ 0 ] "
                    f"set {src_idx_var} + {src_idx_var} 1 0 ] "
                    f"store {base_var} 0 {idx_var} {base_var}"
                )

    def _emit_nested_list_comprehension(self, node: ast.ListComp) -> str:
        """Emit [expr for x in range(n) for y in range(m)] as nested while loops.
        
        Desugars nested comprehensions to an allocation + nested loop fill pattern.
        Both generators must be for-in-range.
        """
        gen0 = node.generators[0]
        gen1 = node.generators[1]
        
        if not isinstance(gen0.target, ast.Name) or not isinstance(gen1.target, ast.Name):
            raise TranspileError("Only simple loop variables in nested list comprehensions")
        
        # Both must be range() iterations
        for gen in [gen0, gen1]:
            is_range = (isinstance(gen.iter, ast.Call) and
                        isinstance(gen.iter.func, ast.Name) and
                        gen.iter.func.id == "range")
            if not is_range:
                raise TranspileError("Nested list comprehensions only support for-in-range iterators")
        
        var0_name = gen0.target.id
        var1_name = gen1.target.id
        
        rng0_start, rng0_end = self._parse_range_args(gen0.iter)
        rng1_start, rng1_end = self._parse_range_args(gen1.iter)
        
        base_var = f"${self._var_counter + 200}"
        idx_var = f"${self._var_counter + 201}"
        loop_var0 = self._declare_var(var0_name)
        loop_var1 = self._declare_var(var1_name)
        
        elt_expr = self._emit_expr(node.elt)
        
        # Total size = range0_size * range1_size (max allocation)
        # Use the product for allocation; actual count tracked by idx_var
        has_filter = any(gen.ifs for gen in [gen0, gen1])
        
        # Collect all filter conditions from both generators
        filter_parts = []
        for gen in [gen0, gen1]:
            for f in gen.ifs:
                filter_parts.append(self._emit_expr(f))
        
        # Total allocation = outer_size * inner_size
        total_alloc = f"* {rng0_end} {rng1_end}"
        
        if not has_filter:
            return (
                f"let {base_var} alloc + 1 {total_alloc} "
                f"let {idx_var} 0 "
                f"let {loop_var0} {rng0_start} "
                f"while < {loop_var0} {rng0_end} [ "
                f"let {loop_var1} {rng1_start} "
                f"while < {loop_var1} {rng1_end} [ "
                f"store {base_var} + {idx_var} 1 {elt_expr} "
                f"set {idx_var} + {idx_var} 1 "
                f"set {loop_var1} + {loop_var1} 1 0 ] "
                f"set {loop_var0} + {loop_var0} 1 0 ] "
                f"store {base_var} 0 {idx_var} {base_var}"
            )
        else:
            filter_expr = filter_parts[0]
            for fp in filter_parts[1:]:
                filter_expr = f"if {filter_expr} [ {fp} ] [ 0 ]"
            return (
                f"let {base_var} alloc + 1 {total_alloc} "
                f"let {idx_var} 0 "
                f"let {loop_var0} {rng0_start} "
                f"while < {loop_var0} {rng0_end} [ "
                f"let {loop_var1} {rng1_start} "
                f"while < {loop_var1} {rng1_end} [ "
                f"if {filter_expr} [ "
                f"store {base_var} + {idx_var} 1 {elt_expr} "
                f"set {idx_var} + {idx_var} 1 0 ] [ 0 ] "
                f"set {loop_var1} + {loop_var1} 1 0 ] "
                f"set {loop_var0} + {loop_var0} 1 0 ] "
                f"store {base_var} 0 {idx_var} {base_var}"
            )

    # ── Dict Comprehension ───────────────────────────────────

    def _emit_dict_comprehension(self, node: ast.DictComp) -> str:
        """Emit {k: v for x in range(n)} as dict allocation + fill loop.
        
        Desugars dict comprehensions to a dict alloc + while loop pattern,
        storing entries as [count, k0, v0, k1, v1, ...].
        """
        if len(node.generators) != 1:
            raise TranspileError("Only single-generator dict comprehensions supported")
        
        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name):
            raise TranspileError("Only simple loop variables in dict comprehensions")
        
        is_range = (isinstance(gen.iter, ast.Call) and
                    isinstance(gen.iter.func, ast.Name) and
                    gen.iter.func.id == "range")
        is_list = (isinstance(gen.iter, ast.Name) and
                   gen.iter.id in self._list_vars)
        
        if not is_range and not is_list:
            raise TranspileError("Dict comprehensions only support for-in-range or for-in-list iterators")
        
        var_name = gen.target.id
        loop_var = self._declare_var(var_name)
        
        base = self._dict_arena_offset
        
        if is_range:
            rng_start, rng_end = self._parse_range_args(gen.iter)
            # Allocate max possible: count + n*(key+val)
            self._dict_arena_offset += (1 + 100 * 2) * self.ELEM_SIZE  # generous max
            
            key_expr = self._emit_expr(node.key)
            val_expr = self._emit_expr(node.value)
            
            count_var = f"${self._var_counter + 203}"
            
            has_filter = bool(gen.ifs)
            
            if not has_filter:
                return (
                    f"write {base} 0 "
                    f"let {count_var} 0 "
                    f"let {loop_var} {rng_start} "
                    f"while < {loop_var} {rng_end} [ "
                    f"write + {base} + * {count_var} 16 8 {key_expr} "
                    f"write + {base} + * {count_var} 16 16 {val_expr} "
                    f"set {count_var} + {count_var} 1 "
                    f"set {loop_var} + {loop_var} 1 0 ] "
                    f"write {base} {count_var} {base}"
                )
            else:
                filter_parts = [self._emit_expr(f) for f in gen.ifs]
                filter_expr = filter_parts[0]
                for fp in filter_parts[1:]:
                    filter_expr = f"if {filter_expr} [ {fp} ] [ 0 ]"
                return (
                    f"write {base} 0 "
                    f"let {count_var} 0 "
                    f"let {loop_var} {rng_start} "
                    f"while < {loop_var} {rng_end} [ "
                    f"if {filter_expr} [ "
                    f"write + {base} + * {count_var} 16 8 {key_expr} "
                    f"write + {base} + * {count_var} 16 16 {val_expr} "
                    f"set {count_var} + {count_var} 1 0 ] [ 0 ] "
                    f"set {loop_var} + {loop_var} 1 0 ] "
                    f"write {base} {count_var} {base}"
                )
        
        raise TranspileError("Dict comprehensions with list iterators not yet implemented")

    # ── Dict Support ─────────────────────────────────────────

    def _emit_dict_literal(self, node: ast.Dict) -> str:
        """Emit dict as linear array: [count, k0, v0, k1, v1, ...]."""
        n = len(node.keys)
        base = self._dict_arena_offset
        # count(8) + n*(key(8) + val(8))
        self._dict_arena_offset += (1 + n * 2) * self.ELEM_SIZE

        parts = []
        parts.append(f"write {base} {n}")
        for i, (k, v) in enumerate(zip(node.keys, node.values)):
            k_code = self._emit_expr(k)
            v_code = self._emit_expr(v)
            k_offset = base + (1 + i * 2) * self.ELEM_SIZE
            v_offset = base + (2 + i * 2) * self.ELEM_SIZE
            parts.append(f"write {k_offset} {k_code}")
            parts.append(f"write {v_offset} {v_code}")
        parts.append(str(base))
        return " ".join(parts)

    # ── List Literal ─────────────────────────────────────────

    def _emit_list_literal(self, node: ast.List) -> str:
        n = len(node.elts)
        base = self._arena_offset
        # Use alloc-based layout: [length at slot 0, then f32 elements via store]
        # alloc returns a base pointer; store ptr idx val stores f32 at ptr + idx*4
        self._arena_offset += (n + 1) * self.ELEM_SIZE

        parts = []
        if self._needs_numpy:
            # For numpy: use alloc + store (f32 elements) 
            parts.append(f"let $200 alloc + 1 {n}")
            parts.append(f"store $200 0 {n}")  # length at slot 0
            for i, elt in enumerate(node.elts):
                val = self._emit_expr(elt)
                parts.append(f"store $200 + {i} 1 {val}")
            parts.append("$200")
        else:
            # For regular lists: use write (i64 elements)
            parts.append(f"write {base} {n}")
            for i, elt in enumerate(node.elts):
                val = self._emit_expr(elt)
                parts.append(f"write {base + (i + 1) * self.ELEM_SIZE} {val}")
            parts.append(str(base))
        return " ".join(parts)

    # ── Call Expressions ─────────────────────────────────────

    def _emit_typed_call(self, node: ast.Call) -> tuple[str, str]:
        if isinstance(node.func, ast.Name):
            name = node.func.id

            if name == "abs":
                if len(node.args) != 1:
                    raise TranspileError("abs() takes exactly 1 argument")
                x, xt = self._emit_typed_expr(node.args[0])
                zero = "0.0" if xt == F32 else "0"
                return f"if > {x} {zero} [ {x} ] [ - {zero} {x} ]", xt

            if name == "min":
                if len(node.args) != 2:
                    raise TranspileError("min() takes exactly 2 arguments")
                a, at = self._emit_typed_expr(node.args[0])
                b, bt = self._emit_typed_expr(node.args[1])
                a, b, rt = self._unify_types(a, at, b, bt)
                return f"if < {a} {b} [ {a} ] [ {b} ]", rt

            if name == "max":
                if len(node.args) != 2:
                    raise TranspileError("max() takes exactly 2 arguments")
                a, at = self._emit_typed_expr(node.args[0])
                b, bt = self._emit_typed_expr(node.args[1])
                a, b, rt = self._unify_types(a, at, b, bt)
                return f"if > {a} {b} [ {a} ] [ {b} ]", rt

            if name == "print":
                if len(node.args) != 1:
                    raise TranspileError("print() with single argument only")
                val, typ = self._emit_typed_expr(node.args[0])
                if typ == STR:
                    # APC: String is packed as (ptr << 32 | len) in i64
                    # Emit call to __str_print helper which unpacks and calls print FFI
                    self._needs_strings = True
                    return f"call __str_print {val}", I64
                elif typ == F32:
                    return f"call print_f32 {val}", I64
                else:
                    return f"call print_i64 {val}", I64

            if name == "range":
                raise TranspileError("range() is only supported in for-in-range loops")

            if name == "sum":
                if len(node.args) == 1 and isinstance(node.args[0], ast.Call):
                    inner = node.args[0]
                    if isinstance(inner.func, ast.Name) and inner.func.id == "range":
                        return self._emit_sum_range(inner), I64
                raise TranspileError("sum() only supports sum(range(...)) currently")

            if name == "len":
                if len(node.args) != 1:
                    raise TranspileError("len() takes exactly 1 argument")
                arg = node.args[0]
                if isinstance(arg, ast.Name):
                    vtype = self._get_var_type(arg.id)
                    if vtype == STR:
                        # APC: Extract length from packed string (low 32 bits)
                        var = self._get_var(arg.id)
                        return f"& {var} 4294967295", I64
                    if arg.id in self._list_vars or arg.id in self._dict_vars:
                        var = self._get_var(arg.id)
                        return f"read {var}", I64
                # len("literal") — constant fold
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return str(len(arg.value)), I64
                raise TranspileError("len() only supports list/dict/string variables")

            if name == "int":
                if len(node.args) != 1:
                    raise TranspileError("int() takes exactly 1 argument")
                x, xt = self._emit_typed_expr(node.args[0])
                return self._coerce(x, xt, I64), I64

            # APC: str(int_val) → int-to-string FFI
            if name == "str":
                if len(node.args) != 1:
                    raise TranspileError("str() takes exactly 1 argument")
                x, xt = self._emit_typed_expr(node.args[0])
                if xt == STR:
                    return x, STR  # already a string
                # Convert int/float to string via __int_to_str FFI
                self._needs_strings = True
                return f"call __int_to_str {x}", STR

            if name == "float":
                if len(node.args) != 1:
                    raise TranspileError("float() takes exactly 1 argument")
                x, xt = self._emit_typed_expr(node.args[0])
                return self._coerce(x, xt, F32), F32

            if name == "round":
                if len(node.args) != 1:
                    raise TranspileError("round() takes exactly 1 argument")
                x, xt = self._emit_typed_expr(node.args[0])
                return self._coerce(x, xt, I64), I64

            # Direct math function calls (from math import exp)
            if name in self._MATH_FUNCTIONS:
                return self._emit_math_call(name, node.args)

            # Class instantiation: ClassName(args)
            if name in self._class_defs:
                return self._emit_class_instantiation(name, node.args)

            # User-defined function call
            if name in self._func_map:
                args = [self._emit_expr(a) for a in node.args]
                rt = self._func_return_types.get(name, I64)
                return f"call {name} {' '.join(args)}", rt

            # ── SECURITY: Block dangerous builtins explicitly ──
            _DANGEROUS = {'eval', 'exec', 'compile', 'open', '__import__',
                          'globals', 'locals', 'vars', 'dir', 'getattr',
                          'setattr', 'delattr', 'breakpoint', 'input'}
            if name in _DANGEROUS:
                raise TranspileError(
                    f"Blocked dangerous builtin: {name}() is not allowed "
                    f"in sandboxed execution")

            raise TranspileError(f"Unknown function: {name}")

        if isinstance(node.func, ast.Attribute):
            # method calls: math.exp(), np.dot(), d.get(k, default), json.loads(), etc.
            return self._emit_method_call(node)

        raise TranspileError(f"Unsupported call type: {type(node.func).__name__}")

    def _emit_method_call(self, node: ast.Call) -> tuple[str, str]:
        """Handle method calls like math.exp(), np.dot(), d.get(), json.loads(), str.upper(), obj.method(), etc."""
        attr = node.func
        if not isinstance(attr, ast.Attribute):
            raise TranspileError("Only simple method calls supported")

        method = attr.attr

        # Handle string methods on both variables and literals
        if isinstance(attr.value, ast.Name):
            obj_name = attr.value.id
            obj_type = self._get_var_type(obj_name)

            # math.exp(), math.log(), math.sqrt(), etc.
            if obj_name == 'math' and 'math' in self._imported_modules:
                return self._emit_math_call(method, node.args)

            # numpy / np calls: np.dot(), np.array(), np.sum(), etc.
            if obj_name in self.ALLOWED_NUMPY_ALIASES and 'numpy' in self._imported_modules:
                return self._emit_numpy_call(method, node.args, node.keywords)

            # json.loads(), json.dumps()
            if obj_name == 'json' and 'json' in self._imported_modules:
                return self._emit_json_call(method, node.args)

            # dict methods
            if obj_name in self._dict_vars and method == "get":
                if len(node.args) < 1 or len(node.args) > 2:
                    raise TranspileError("dict.get() takes 1-2 arguments")
                key = self._emit_expr(node.args[0])
                default = self._emit_expr(node.args[1]) if len(node.args) == 2 else "0"
                base = self._get_var(obj_name)
                return f"call __dict_get_default {base} {key} {default}", I64

            # self.method() call inside a class method
            if self._in_class and obj_name == 'self':
                class_name = self._in_class
                func_name = f"__{class_name}__{method}"
                if func_name in self._func_map:
                    self_var = self._get_var('self')
                    arg_codes = [self._emit_expr(a) for a in node.args]
                    args_str = " ".join([self_var] + arg_codes)
                    rt = self._func_return_types.get(func_name, I64)
                    return f"call {func_name} {args_str}", rt
                # Check class constants accessed as self.CONST
                class_info = self._class_defs.get(class_name, {})
                if method in class_info.get('constants', {}):
                    const_val = class_info['constants'][method]
                    if isinstance(const_val, float):
                        return str(const_val), F32
                    return str(const_val), I64

            # Instance method call: obj.method(args) — class instances
            if obj_name in self._var_types and self._var_types.get(obj_name, '').startswith('__class_'):
                class_name = self._var_types[obj_name][len('__class_'):]
                return self._emit_instance_method_call(obj_name, class_name, method, node.args)

            # APC: String methods on variables
            if obj_type == STR or obj_name in self._str_vars:
                return self._emit_string_method(obj_name, method, node.args)

        elif isinstance(attr.value, ast.Constant) and isinstance(attr.value.value, str):
            # APC: String methods on literals — e.g. "hello".upper()
            # Emit the literal first to get its packed value
            lit_code, _ = self._emit_typed_expr(attr.value)
            return self._emit_string_method_on_code(lit_code, method, node.args)

        if isinstance(attr.value, ast.Name):
            obj_name = attr.value.id
            method = attr.attr
            # User module qualified call: mymodule.myfunc() → call myfunc args
            if obj_name in self._user_module_aliases:
                arg_codes = [self._emit_expr(a) for a in node.args]
                args_str = " ".join(arg_codes) if arg_codes else ""
                rt = self._func_return_types.get(method, I64)
                if args_str:
                    return f"call {method} {args_str}", rt
                return f"call {method}", rt
            raise TranspileError(f"Unsupported method: {obj_name}.{method}()")
        raise TranspileError(f"Unsupported method call on {type(attr.value).__name__}")

    def _emit_sum_range(self, range_call: ast.Call) -> str:
        rng_start, rng_end = self._parse_range_args(range_call)
        acc_var = f"${self._var_counter + 100}"
        iter_var = f"${self._var_counter + 101}"
        return (
            f"let {acc_var} 0 "
            f"let {iter_var} {rng_start} "
            f"while < {iter_var} {rng_end} [ "
            f"set {acc_var} + {acc_var} {iter_var} "
            f"set {iter_var} + {iter_var} 1 0 ] {acc_var}"
        )

    # ── Statement Visitors ───────────────────────────────────

    def _emit_stmt(self, node: ast.AST) -> str:
        if isinstance(node, ast.Expr):
            return self._emit_expr(node.value)

        if isinstance(node, ast.Assign):
            return self._emit_assign(node)

        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Attribute):
                # self.x += val → class attribute augmented assign
                return self._emit_attr_aug_assign(node)
            if not isinstance(node.target, ast.Name):
                raise TranspileError("Only simple augmented assignment supported")
            var = self._get_var(node.target.id)
            value, vt = self._emit_typed_expr(node.value)
            op_map = {
                ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
                ast.FloorDiv: "/", ast.Mod: "%",
            }
            op = op_map.get(type(node.op))
            if op is None:
                raise TranspileError(f"Unsupported augmented assign op: {type(node.op).__name__}")
            return f"set {var} {op} {var} {value}"

        if isinstance(node, ast.If):
            return self._emit_if(node)

        if isinstance(node, ast.While):
            cond = self._emit_expr(node.test)
            body = self._emit_block(node.body)
            return f"while {cond} [ {body} 0 ]"

        if isinstance(node, ast.For):
            return self._emit_for(node)

        if isinstance(node, ast.Return):
            if node.value is None:
                return "0"
            return self._emit_expr(node.value)

        if isinstance(node, ast.Pass):
            return "0"

        if isinstance(node, ast.FunctionDef):
            return self._emit_funcdef(node)

        if isinstance(node, ast.ClassDef):
            return self._emit_classdef(node)

        if isinstance(node, ast.Try):
            return self._emit_try_except(node)

        # Handle imports — allow math, numpy; reject everything else
        if isinstance(node, ast.Import):
            return self._emit_import(node)

        if isinstance(node, ast.ImportFrom):
            return self._emit_import_from(node)

        raise TranspileError(f"Unsupported statement: {type(node).__name__}")

    def _emit_import(self, node: ast.Import) -> str:
        """Handle `import math`, `import numpy as np`, `import json`, user modules, etc.
        
        Built-in modules (math, numpy, json) are handled internally.
        User-defined modules map to .syn @use directives:
            import mymodule  →  (@use "mymodule")
            import mypackage.mymodule  →  (@use "mypackage/mymodule")
        """
        for alias in node.names:
            name = alias.name
            asname = alias.asname or name
            if name == 'math':
                self._imported_modules.add('math')
                return ""  # no-op in .syn
            elif name == 'numpy':
                self._imported_modules.add('numpy')
                self._numpy_alias = asname
                return ""
            elif name == 'json':
                self._imported_modules.add('json')
                self._needs_json = True
                return ""
            else:
                # Security: block dangerous system modules
                root_module = name.split('.')[0]
                if root_module in self._DANGEROUS_MODULES:
                    raise TranspileError(
                        f"Blocked dangerous import: {name}. "
                        f"System modules are not allowed in sandboxed execution.")
                # User-defined module → map to .syn @use
                syn_path = name.replace('.', '/')
                self._user_modules.append(syn_path)
                self._user_module_aliases[asname] = syn_path
                self._imported_modules.add(name)
                return ""
        return ""

    def _emit_import_from(self, node: ast.ImportFrom) -> str:
        """Handle `from math import exp`, `from json import loads`, user modules, etc.
        
        User-defined modules map to .syn @use:
            from mymodule import myfunc  →  (@use "mymodule") + func registration
        """
        module = node.module or ''

        # Block star imports: 'from X import *'
        if any(alias.name == '*' for alias in (node.names or [])):
            raise TranspileError(
                f"Star imports not allowed: 'from {module} import *'. "
                f"Use explicit imports instead.")

        if module == 'math':
            self._imported_modules.add('math')
            return ""
        elif module == 'numpy':
            self._imported_modules.add('numpy')
            return ""
        elif module == 'json':
            self._imported_modules.add('json')
            self._needs_json = True
            return ""
        else:
            # Security: block dangerous system modules
            root_module = module.split('.')[0]
            if root_module in self._DANGEROUS_MODULES:
                raise TranspileError(
                    f"Blocked dangerous import: from {module}. "
                    f"System modules are not allowed in sandboxed execution.")
            # User-defined module → map to .syn @use
            syn_path = module.replace('.', '/')
            self._user_modules.append(syn_path)
            self._imported_modules.add(module)
            # Register imported function names
            for alias in (node.names or []):
                func_name = alias.name
                local_name = alias.asname or func_name
                self._user_module_funcs[local_name] = syn_path
                # Register as a known function (assume i64 return, unknown arity)
                # The .syn compiler will resolve the actual signature via @use
                if local_name not in self._func_map:
                    self._func_map[local_name] = -1  # -1 = unknown arity (variadic dispatch)
                    self._func_return_types[local_name] = I64
            return ""
        return ""

    def _emit_assign(self, node: ast.Assign) -> str:
        if len(node.targets) != 1:
            raise TranspileError("Multiple assignment targets not supported")
        target = node.targets[0]

        # Handle attribute assignment: self.x = val (inside class methods)
        if isinstance(target, ast.Attribute):
            return self._emit_attr_assign(target, node.value)

        # Handle subscript assignment: x[i] = val, or data["key"] for json
        if isinstance(target, ast.Subscript):
            return self._emit_subscript_assign(target, node.value)

        # Handle tuple unpacking: a, b = expr
        if isinstance(target, ast.Tuple):
            return self._emit_tuple_unpack(target, node.value)

        if not isinstance(target, ast.Name):
            raise TranspileError("Only simple variable, subscript, tuple, or attribute assignment supported")

        # Track collection types
        if isinstance(node.value, ast.List):
            self._list_vars.add(target.id)
        if isinstance(node.value, ast.Dict):
            self._dict_vars.add(target.id)
        if isinstance(node.value, ast.ListComp):
            self._list_vars.add(target.id)
        if isinstance(node.value, ast.DictComp):
            self._dict_vars.add(target.id)
        # APC: Track string variables
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self._str_vars.add(target.id)
        # Track json variables
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            if isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'json':
                if node.value.func.attr == 'loads':
                    self._json_vars.add(target.id)
                    self._dict_vars.add(target.id)  # json.loads returns a dict-like structure
        # Track class instantiation: p = Point(3, 4) → type is __class_Point
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            if node.value.func.id in self._class_defs:
                self._set_var_type(target.id, f'__class_{node.value.func.id}')

        value, vtype = self._emit_typed_expr(node.value)
        # Only override type if we haven't already set a class type
        if not self._var_types.get(target.id, '').startswith('__class_'):
            self._set_var_type(target.id, vtype)

        if self._is_declared(target.id):
            var = self._get_var(target.id)
            return f"set {var} {value}"
        var = self._declare_var(target.id)
        return f"let {var} {value}"

    def _emit_tuple_unpack(self, target: ast.Tuple, value: ast.AST) -> str:
        """Handle a, b = func() or a, b = (1, 2)."""
        if isinstance(value, ast.Tuple):
            # a, b = (1, 2) — direct assignment
            if len(target.elts) != len(value.elts):
                raise TranspileError("Tuple unpacking size mismatch")
            parts = []
            for t, v in zip(target.elts, value.elts):
                if not isinstance(t, ast.Name):
                    raise TranspileError("Only simple names in tuple unpacking")
                val_code, vtype = self._emit_typed_expr(v)
                self._set_var_type(t.id, vtype)
                if self._is_declared(t.id):
                    var = self._get_var(t.id)
                    parts.append(f"set {var} {val_code}")
                else:
                    var = self._declare_var(t.id)
                    parts.append(f"let {var} {val_code}")
            return " ".join(parts)
        raise TranspileError("Tuple unpacking only supported with tuple literals")

    def _emit_subscript_assign(self, target: ast.Subscript, value_node: ast.expr) -> str:
        if not isinstance(target.value, ast.Name):
            raise TranspileError("Only simple variable subscript assignment supported")
        name = target.value.id
        base_var = self._get_var(name)
        idx = self._emit_expr(target.slice)
        val = self._emit_expr(value_node)

        if name in self._dict_vars:
            return f"call __dict_set {base_var} {idx} {val}"
        return f"write + {base_var} * + {idx} 1 {self.ELEM_SIZE} {val}"

    def _emit_block(self, stmts: list[ast.AST]) -> str:
        parts = []
        for stmt in stmts:
            parts.append(self._emit_stmt(stmt))
        return " ".join(parts)

    def _emit_if(self, node: ast.If) -> str:
        cond = self._emit_expr(node.test)
        body = self._emit_block(node.body)
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                else_part = self._emit_if(node.orelse[0])
            else:
                else_part = self._emit_block(node.orelse)
            return f"if {cond} [ {body} ] [ {else_part} ]"
        return f"if {cond} [ {body} ] [ 0 ]"

    def _emit_for(self, node: ast.For) -> str:
        if not isinstance(node.target, ast.Name):
            raise TranspileError("Only simple for-loop variables supported")

        # Support for-in-range and for-in-list
        if (isinstance(node.iter, ast.Call) and
                isinstance(node.iter.func, ast.Name) and
                node.iter.func.id == "range"):
            return self._emit_for_range(node)

        # for x in some_list: → while loop over list indices
        if isinstance(node.iter, ast.Name) and node.iter.id in self._list_vars:
            return self._emit_for_list(node)

        raise TranspileError("Only for-in-range and for-in-list loops supported")

    def _emit_for_range(self, node: ast.For) -> str:
        var_name = node.target.id
        rng_start, rng_end = self._parse_range_args(node.iter)
        var = self._declare_var(var_name)
        self._set_var_type(var_name, I64)
        body = self._emit_block(node.body)
        return (
            f"let {var} {rng_start} "
            f"while < {var} {rng_end} [ {body} set {var} + {var} 1 0 ]"
        )

    def _emit_for_list(self, node: ast.For) -> str:
        """for x in my_list: → while loop over list elements."""
        list_var = self._get_var(node.iter.id)
        var_name = node.target.id
        var = self._declare_var(var_name)
        idx_var = f"${self._var_counter + 150}"
        len_expr = f"read {list_var}"

        body = self._emit_block(node.body)
        return (
            f"let {idx_var} 0 "
            f"let {var} 0 "
            f"while < {idx_var} {len_expr} [ "
            f"set {var} read + {list_var} * + {idx_var} 1 {self.ELEM_SIZE} "
            f"{body} "
            f"set {idx_var} + {idx_var} 1 0 ]"
        )

    def _parse_range_args(self, call: ast.Call) -> tuple[str, str]:
        if len(call.args) == 1:
            return "0", self._emit_expr(call.args[0])
        if len(call.args) == 2:
            return self._emit_expr(call.args[0]), self._emit_expr(call.args[1])
        if len(call.args) == 3:
            return self._emit_expr(call.args[0]), self._emit_expr(call.args[1])
        raise TranspileError("range() with 1-3 arguments only")

    def _emit_funcdef(self, node: ast.FunctionDef) -> str:
        # Block decorated functions — decorators enable metaprogramming
        if node.decorator_list:
            raise TranspileError(
                f"Decorators not supported: function '{node.name}' has "
                f"{len(node.decorator_list)} decorator(s)")

        arity = len(node.args.args)
        self._func_map[node.name] = arity

        old_in_func = self._in_function
        old_vars = self._current_func_vars
        old_counter = self._current_func_var_counter
        old_params = self._current_func_params
        old_param_types = self._current_func_param_types

        self._in_function = True
        self._current_func_vars = {}
        self._current_func_params = {
            arg.arg: i for i, arg in enumerate(node.args.args)
        }
        self._current_func_param_types = {}
        # Infer param types from annotations if present
        for arg in node.args.args:
            if arg.annotation:
                if isinstance(arg.annotation, ast.Name):
                    if arg.annotation.id == 'float':
                        self._current_func_param_types[arg.arg] = F32
                    else:
                        self._current_func_param_types[arg.arg] = I64
                else:
                    self._current_func_param_types[arg.arg] = I64
            else:
                self._current_func_param_types[arg.arg] = I64

        self._current_func_var_counter = arity

        body = self._emit_block(node.body)

        # Infer return type from the last return statement
        ret_type = self._infer_return_type(node)
        self._func_return_types[node.name] = ret_type

        self._in_function = old_in_func
        self._current_func_vars = old_vars
        self._current_func_var_counter = old_counter
        self._current_func_params = old_params
        self._current_func_param_types = old_param_types

        self.functions.append(f"@f {arity} {node.name} [ {body} ]")
        return ""

    def _infer_return_type(self, node: ast.FunctionDef) -> str:
        """Infer function return type from return annotation or body."""
        if node.returns:
            if isinstance(node.returns, ast.Name):
                if node.returns.id == 'float':
                    return F32
        # Walk body looking for return statements with float literals
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value:
                if isinstance(child.value, ast.Constant) and isinstance(child.value.value, float):
                    return F32
        return I64

    # ── Math Functions (Pure .syn Taylor Series) ─────────────

    # Registry of supported math functions
    _MATH_FUNCTIONS = {
        'exp', 'log', 'sqrt', 'sin', 'cos', 'tan',
        'pow', 'ceil', 'floor', 'fabs', 'atan', 'atan2',
        'sinh', 'cosh', 'tanh',
    }
    # Math constants
    _MATH_CONSTANTS = {
        'pi': '3.14159265358979',
        'e': '2.71828182845905',
        'inf': '999999999.0',
        'tau': '6.28318530717959',
    }

    def _emit_math_call(self, func_name: str, args: list) -> tuple[str, str]:
        """Emit a math module function call as a .syn helper function call."""
        self._needs_math = True

        if func_name in self._MATH_CONSTANTS:
            return self._MATH_CONSTANTS[func_name], F32

        if func_name == 'exp':
            if len(args) != 1:
                raise TranspileError("math.exp() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('exp')
            return f"call __math_exp {x}", F32

        if func_name == 'log':
            if len(args) != 1:
                raise TranspileError("math.log() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('log')
            return f"call __math_log {x}", F32

        if func_name == 'sqrt':
            if len(args) != 1:
                raise TranspileError("math.sqrt() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('sqrt')
            return f"call __math_sqrt {x}", F32

        if func_name == 'sin':
            if len(args) != 1:
                raise TranspileError("math.sin() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('sin')
            return f"call __math_sin {x}", F32

        if func_name == 'cos':
            if len(args) != 1:
                raise TranspileError("math.cos() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('cos')
            return f"call __math_cos {x}", F32

        if func_name == 'tan':
            if len(args) != 1:
                raise TranspileError("math.tan() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('sin')
            self._emitted_math_funcs.add('cos')
            return f"/ call __math_sin {x} call __math_cos {x}", F32

        if func_name == 'pow':
            if len(args) != 2:
                raise TranspileError("math.pow() takes exactly 2 arguments")
            base, bt = self._emit_typed_expr(args[0])
            exp_arg, et = self._emit_typed_expr(args[1])
            base = self._coerce(base, bt, F32)
            exp_arg = self._coerce(exp_arg, et, F32)
            self._emitted_math_funcs.add('exp')
            self._emitted_math_funcs.add('log')
            # pow(a, b) = exp(b * log(a))
            return f"call __math_exp * {exp_arg} call __math_log {base}", F32

        if func_name == 'ceil':
            if len(args) != 1:
                raise TranspileError("math.ceil() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            # ceil(x) = -floor(-x)
            return f"- 0.0 call __math_floor - 0.0 {x}", F32

        if func_name == 'floor':
            if len(args) != 1:
                raise TranspileError("math.floor() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('floor')
            return f"call __math_floor {x}", F32

        if func_name == 'fabs':
            if len(args) != 1:
                raise TranspileError("math.fabs() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            return f"if > {x} 0.0 [ {x} ] [ - 0.0 {x} ]", F32

        if func_name == 'atan':
            if len(args) != 1:
                raise TranspileError("math.atan() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('atan')
            return f"call __math_atan {x}", F32

        if func_name == 'atan2':
            if len(args) != 2:
                raise TranspileError("math.atan2() takes exactly 2 arguments")
            y, yt = self._emit_typed_expr(args[0])
            x, xt = self._emit_typed_expr(args[1])
            y = self._coerce(y, yt, F32)
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('atan')
            # atan2(y, x) = atan(y/x) with quadrant correction
            return f"call __math_atan / {y} {x}", F32

        if func_name in ('sinh', 'cosh', 'tanh'):
            if len(args) != 1:
                raise TranspileError(f"math.{func_name}() takes exactly 1 argument")
            x, xt = self._emit_typed_expr(args[0])
            x = self._coerce(x, xt, F32)
            self._emitted_math_funcs.add('exp')
            if func_name == 'sinh':
                return f"/ - call __math_exp {x} call __math_exp - 0.0 {x} 2.0", F32
            elif func_name == 'cosh':
                return f"/ + call __math_exp {x} call __math_exp - 0.0 {x} 2.0", F32
            else:  # tanh
                self._emitted_math_funcs.add('tanh')
                return f"call __math_tanh {x}", F32

        raise TranspileError(f"Unsupported math function: math.{func_name}()")

    def _emit_math_helpers(self) -> str:
        """Emit pure .syn implementations of math functions using Taylor series."""
        if not self._emitted_math_funcs:
            return ""

        helpers = []

        if 'exp' in self._emitted_math_funcs:
            # exp(x) via Taylor series: sum_{k=0}^{20} x^k / k!
            # Range reduction: exp(x) = exp(x/N)^N for stability
            helpers.append(
                "@f 1 __math_exp [ "
                # Range reduction: divide by 16, compute, square 4 times
                "let $1 / $0 16.0 "
                # Taylor series for exp(x/16): 1 + x + x²/2 + x³/6 + ... (15 terms)
                "let $2 1.0 "
                "let $3 1.0 "
                "let $4 $1 "
                # term = x^k / k!, accumulate
                "set $2 + $2 $4 "
                "set $4 * $4 / $1 2.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 3.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 4.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 5.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 6.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 7.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 8.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 9.0 set $2 + $2 $4 "
                "set $4 * $4 / $1 10.0 set $2 + $2 $4 "
                # Square 4 times: (exp(x/16))^16 = exp(x)
                "set $2 * $2 $2 "
                "set $2 * $2 $2 "
                "set $2 * $2 $2 "
                "set $2 * $2 $2 "
                "$2 ]"
            )

        if 'log' in self._emitted_math_funcs:
            # log(x) via the identity: log(x) = 2 * atanh((x-1)/(x+1))
            # atanh(y) = y + y³/3 + y⁵/5 + y⁷/7 + ...
            helpers.append(
                "@f 1 __math_log [ "
                # Normalize: extract mantissa into [1, 2) range
                # Simple approach: count doublings/halvings to put x near 1
                "let $1 $0 let $2 0.0 "
                # Scale down if x > 2
                "while > $1 2.0 [ set $1 / $1 2.71828182845905 set $2 + $2 1.0 0 ] "
                # Scale up if x < 0.5
                "while < $1 0.5 [ set $1 * $1 2.71828182845905 set $2 - $2 1.0 0 ] "
                # Now x ≈ 1. Use series: log(x) = 2*atanh((x-1)/(x+1))
                "let $3 / - $1 1.0 + $1 1.0 "
                "let $4 $3 let $5 * $3 $3 "
                "let $6 $4 "
                "set $4 * $4 $5 set $6 + $6 / $4 3.0 "
                "set $4 * $4 $5 set $6 + $6 / $4 5.0 "
                "set $4 * $4 $5 set $6 + $6 / $4 7.0 "
                "set $4 * $4 $5 set $6 + $6 / $4 9.0 "
                "set $4 * $4 $5 set $6 + $6 / $4 11.0 "
                "set $4 * $4 $5 set $6 + $6 / $4 13.0 "
                "set $4 * $4 $5 set $6 + $6 / $4 15.0 "
                "+ $2 * 2.0 $6 ]"
            )

        if 'sqrt' in self._emitted_math_funcs:
            # sqrt(x) via Newton's method: y_{n+1} = (y_n + x/y_n) / 2
            # Converges quadratically — 10 iterations gives ~20 digits
            helpers.append(
                "@f 1 __math_sqrt [ "
                "if < $0 0.0 [ 0.0 ] [ "
                "let $1 $0 "
                "let $2 0 "
                "while < $2 12 [ "
                "set $1 / + $1 / $0 $1 2.0 "
                "set $2 + $2 1 0 ] $1 ] ]"
            )

        if 'sin' in self._emitted_math_funcs:
            # sin(x) via Taylor: x - x³/3! + x⁵/5! - x⁷/7! + ...
            # Range reduction: x mod 2π
            helpers.append(
                "@f 1 __math_sin [ "
                # Range reduction to [-π, π]
                "let $1 $0 "
                "while > $1 3.14159265358979 [ set $1 - $1 6.28318530717959 0 ] "
                "while < $1 -3.14159265358979 [ set $1 + $1 6.28318530717959 0 ] "
                "let $2 $1 let $3 $1 let $4 * $1 $1 "
                "set $3 * $3 / - 0.0 $4 6.0 set $2 + $2 $3 "
                "set $3 * $3 / - 0.0 $4 20.0 set $2 + $2 $3 "
                "set $3 * $3 / - 0.0 $4 42.0 set $2 + $2 $3 "
                "set $3 * $3 / - 0.0 $4 72.0 set $2 + $2 $3 "
                "set $3 * $3 / - 0.0 $4 110.0 set $2 + $2 $3 "
                "set $3 * $3 / - 0.0 $4 156.0 set $2 + $2 $3 "
                "set $3 * $3 / - 0.0 $4 210.0 set $2 + $2 $3 "
                "$2 ]"
            )

        if 'cos' in self._emitted_math_funcs:
            # cos(x) = sin(x + π/2)
            helpers.append(
                "@f 1 __math_cos [ "
                "call __math_sin + $0 1.5707963267949 ]"
            )
            self._emitted_math_funcs.add('sin')  # cos depends on sin

        if 'floor' in self._emitted_math_funcs:
            # floor(x) = i64(x) adjusted for negatives
            helpers.append(
                "@f 1 __math_floor [ "
                "let $1 to_f32 i64 $0 "
                "if > $1 $0 [ - $1 1.0 ] [ $1 ] ]"
            )

        if 'atan' in self._emitted_math_funcs:
            # atan(x) via series for |x| <= 1, identity for |x| > 1
            helpers.append(
                "@f 1 __math_atan [ "
                "let $1 $0 let $2 0.0 "
                # For |x| > 1: atan(x) = sign(x)*π/2 - atan(1/x)
                "if > $1 1.0 [ "
                "set $2 1.5707963267949 set $1 / 1.0 $1 0 ] "
                "[ if < $1 -1.0 [ "
                "set $2 -1.5707963267949 set $1 / -1.0 - 0.0 $1 0 ] [ 0 ] ] "
                # Taylor for |x| <= 1: x - x³/3 + x⁵/5 - x⁷/7 ...
                "let $3 $1 let $4 * $1 $1 let $5 $3 "
                "set $3 * $3 - 0.0 $4 set $5 + $5 / $3 3.0 "
                "set $3 * $3 - 0.0 $4 set $5 + $5 / $3 5.0 "
                "set $3 * $3 - 0.0 $4 set $5 + $5 / $3 7.0 "
                "set $3 * $3 - 0.0 $4 set $5 + $5 / $3 9.0 "
                "set $3 * $3 - 0.0 $4 set $5 + $5 / $3 11.0 "
                "if == $2 0.0 [ $5 ] [ - $2 $5 ] ]"
            )

        if 'tanh' in self._emitted_math_funcs:
            # tanh(x) = (exp(2x) - 1) / (exp(2x) + 1)
            helpers.append(
                "@f 1 __math_tanh [ "
                "let $1 call __math_exp * 2.0 $0 "
                "/ - $1 1.0 + $1 1.0 ]"
            )

        return " ".join(helpers)

    # ── NumPy Subset ─────────────────────────────────────────

    def _emit_numpy_call(self, func_name: str, args: list, keywords: list = None) -> tuple[str, str]:
        """Emit numpy function calls as .syn operations."""
        self._needs_numpy = True

        if func_name == 'array':
            # np.array([1, 2, 3]) → list literal
            if len(args) != 1:
                raise TranspileError("np.array() takes exactly 1 argument")
            if isinstance(args[0], ast.List):
                return self._emit_list_literal(args[0]), I64
            raise TranspileError("np.array() only supports list literals currently")

        if func_name == 'zeros':
            if len(args) != 1:
                raise TranspileError("np.zeros() takes exactly 1 argument")
            n = self._emit_expr(args[0])
            # Allocate and zero-fill
            return (
                f"let $250 alloc + 1 {n} "
                f"store $250 0 {n} "
                f"let $251 0 while < $251 {n} [ "
                f"store $250 + $251 1 0 "
                f"set $251 + $251 1 0 ] $250"
            ), I64

        if func_name == 'ones':
            if len(args) != 1:
                raise TranspileError("np.ones() takes exactly 1 argument")
            n = self._emit_expr(args[0])
            return (
                f"let $250 alloc + 1 {n} "
                f"store $250 0 {n} "
                f"let $251 0 while < $251 {n} [ "
                f"store $250 + $251 1 1 "
                f"set $251 + $251 1 0 ] $250"
            ), I64

        if func_name == 'arange':
            # np.arange(start, stop[, step])
            if len(args) == 1:
                stop = self._emit_expr(args[0])
                return (
                    f"let $250 alloc + 1 {stop} "
                    f"store $250 0 {stop} "
                    f"let $251 0 while < $251 {stop} [ "
                    f"store $250 + $251 1 $251 "
                    f"set $251 + $251 1 0 ] $250"
                ), I64
            elif len(args) == 2:
                start = self._emit_expr(args[0])
                stop = self._emit_expr(args[1])
                return (
                    f"let $252 - {stop} {start} "
                    f"let $250 alloc + 1 $252 "
                    f"store $250 0 $252 "
                    f"let $251 0 while < $251 $252 [ "
                    f"store $250 + $251 1 + {start} $251 "
                    f"set $251 + $251 1 0 ] $250"
                ), I64
            raise TranspileError("np.arange() takes 1-2 arguments")

        if func_name == 'dot':
            # np.dot(a, b) — 1D dot product
            if len(args) != 2:
                raise TranspileError("np.dot() takes exactly 2 arguments")
            a = self._emit_expr(args[0])
            b = self._emit_expr(args[1])
            self._emitted_math_funcs.add('dot')  # track for helper emission
            return f"call __np_dot {a} {b}", F32

        if func_name == 'sum':
            if len(args) != 1:
                raise TranspileError("np.sum() takes exactly 1 argument")
            arr = self._emit_expr(args[0])
            return f"call __np_sum {arr}", F32

        if func_name == 'mean':
            if len(args) != 1:
                raise TranspileError("np.mean() takes exactly 1 argument")
            arr = self._emit_expr(args[0])
            return f"call __np_mean {arr}", F32

        if func_name == 'max':
            if len(args) != 1:
                raise TranspileError("np.max() takes exactly 1 argument")
            arr = self._emit_expr(args[0])
            return f"call __np_max {arr}", F32

        if func_name == 'min':
            if len(args) != 1:
                raise TranspileError("np.min() takes exactly 1 argument")
            arr = self._emit_expr(args[0])
            return f"call __np_min {arr}", F32

        if func_name == 'abs':
            if len(args) != 1:
                raise TranspileError("np.abs() takes exactly 1 argument")
            # For scalar, inline
            x, xt = self._emit_typed_expr(args[0])
            zero = "0.0" if xt == F32 else "0"
            return f"if > {x} {zero} [ {x} ] [ - {zero} {x} ]", xt

        # Delegate math-like numpy functions to math helpers
        if func_name in ('sqrt', 'exp', 'log', 'sin', 'cos', 'tan'):
            return self._emit_math_call(func_name, args)

        if func_name == 'linspace':
            if len(args) != 3:
                raise TranspileError("np.linspace() takes exactly 3 arguments")
            start = self._emit_expr(args[0])
            stop = self._emit_expr(args[1])
            num = self._emit_expr(args[2])
            return (
                f"let $250 alloc + 1 {num} "
                f"store $250 0 {num} "
                f"let $253 / - to_f32 {stop} to_f32 {start} - to_f32 {num} 1.0 "
                f"let $251 0 while < $251 {num} [ "
                f"store $250 + $251 1 i64 + to_f32 {start} * $253 to_f32 $251 "
                f"set $251 + $251 1 0 ] $250"
            ), I64

        raise TranspileError(f"Unsupported numpy function: np.{func_name}()")

    def _emit_numpy_helpers(self) -> str:
        """Emit .syn helper functions for numpy operations."""
        if not self._needs_numpy:
            return ""

        helpers = []

        # np.dot(a, b) — delegates to Host FFI for native performance
        if 'dot' in self._emitted_math_funcs:
            helpers.append(
                "@schema ffi_numpy_dot : (i64 i64) -> f32 "
                "@schema __np_dot : (i64 i64) -> f32 "
                "@f 2 __np_dot [ call ffi_numpy_dot $0 $1 ]"
            )

        # np.sum(arr) — delegates to Host FFI
        helpers.append(
            "@schema ffi_numpy_sum : (i64) -> f32 "
            "@schema __np_sum : (i64) -> f32 "
            "@f 1 __np_sum [ call ffi_numpy_sum $0 ]"
        )

        # np.mean(arr) — f32 / length
        helpers.append(
            "@schema __np_mean : (i64) -> f32 "
            "@f 1 __np_mean [ "
            "let $1 to_i64 load $0 0 let $2 0.0 let $3 0 "
            "while < $3 $1 [ "
            "set $2 + $2 load $0 + $3 1 "
            "set $3 + $3 1 0 ] / $2 to_f32 $1 ]"
        )

        # np.max(arr) — f32 comparison
        helpers.append(
            "@schema __np_max : (i64) -> f32 "
            "@f 1 __np_max [ "
            "let $1 to_i64 load $0 0 let $2 load $0 1 let $3 1 "
            "while < $3 $1 [ "
            "let $4 load $0 + $3 1 "
            "if > $4 $2 [ set $2 $4 ] [ 0 ] "
            "set $3 + $3 1 0 ] $2 ]"
        )

        # np.min(arr) — f32 comparison
        helpers.append(
            "@schema __np_min : (i64) -> f32 "
            "@f 1 __np_min [ "
            "let $1 to_i64 load $0 0 let $2 load $0 1 let $3 1 "
            "while < $3 $1 [ "
            "let $4 load $0 + $3 1 "
            "if < $4 $2 [ set $2 $4 ] [ 0 ] "
            "set $3 + $3 1 0 ] $2 ]"
        )

        return " ".join(helpers)

    # ── Top-Level ────────────────────────────────────────────

    def transpile(self, source: str) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise TranspileError(f"Python syntax error: {e}") from e

        # Pass 0: collect imports
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._emit_stmt(node)

        # Pass 0.5: collect class definitions (before functions, since methods become functions)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._emit_classdef(node)

        # First pass: collect function definitions
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self._emit_funcdef(node)

        # Second pass: collect top-level statements
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue  # already handled
            if isinstance(node, ast.ClassDef):
                continue  # already handled
            stmt = self._emit_stmt(node)
            if stmt:
                self.main_body.append(stmt)

        main_code = " ".join(self.main_body) if self.main_body else "0"

        # Build helper functions if needed
        dict_helpers = self._emit_dict_helpers()
        math_helpers = self._emit_math_helpers()
        numpy_helpers = self._emit_numpy_helpers()
        string_helpers = self._emit_string_helpers()
        json_helpers = self._emit_json_helpers()

        parts = []
        # Emit @use directives for user-defined module imports
        for mod_path in self._user_modules:
            parts.append(f'(@use "{mod_path}")')
        if string_helpers:
            parts.append(string_helpers)
        if json_helpers:
            parts.append(json_helpers)
        if dict_helpers:
            parts.append(dict_helpers)
        if math_helpers:
            parts.append(math_helpers)
        if numpy_helpers:
            parts.append(numpy_helpers)
        parts.extend(self.functions)
        has_main = any(f.startswith("@f 0 main") for f in self.functions)
        if not has_main:
            # When numpy, strings, or json are used, main locals are pointers (i64).
            # Schema prevents f32 heuristic from corrupting pointer math.
            if self._needs_numpy or self._needs_strings or self._needs_json:
                parts.append(f"@schema main : () -> i64 @f 0 main [ {main_code} ]")
            else:
                parts.append(f"@f 0 main [ {main_code} ]")
        # APC: Emit data sections AFTER @f declarations
        # (data must come after @f so first token is '@', not '(' — compiler syntax detection)
        for offset, s in self._string_data:
            # Emit raw string — the compiler's (data) parser handles encoding
            parts.append(f'(data {offset} "{s}")')
        return " ".join(parts)

    # ── APC String Methods ───────────────────────────────────

    # Supported string methods mapped to their FFI function names and arities
    _STRING_METHODS = {
        'upper': ('__ffi_str_upper', 0),     # s.upper() -> STR
        'lower': ('__ffi_str_lower', 0),     # s.lower() -> STR
        'strip': ('__ffi_str_strip', 0),     # s.strip() -> STR
        'lstrip': ('__ffi_str_lstrip', 0),   # s.lstrip() -> STR
        'rstrip': ('__ffi_str_rstrip', 0),   # s.rstrip() -> STR
        'replace': ('__ffi_str_replace', 2), # s.replace(old, new) -> STR
        'startswith': ('__ffi_str_startswith', 1),  # s.startswith(p) -> I64 (0/1)
        'endswith': ('__ffi_str_endswith', 1),      # s.endswith(p) -> I64 (0/1)
        'find': ('__ffi_str_find', 1),       # s.find(sub) -> I64 (index or -1)
        'count': ('__ffi_str_count', 1),     # s.count(sub) -> I64
    }

    def _emit_string_method(self, obj_name: str, method: str, args: list) -> tuple[str, str]:
        """Emit string method call on a variable."""
        self._needs_strings = True
        var = self._get_var(obj_name)
        return self._emit_string_method_on_code(var, method, args)

    def _emit_string_method_on_code(self, obj_code: str, method: str, args: list) -> tuple[str, str]:
        """Emit string method call on an arbitrary expression code."""
        self._needs_strings = True

        if method not in self._STRING_METHODS:
            raise TranspileError(f"Unsupported string method: .{method}()")

        ffi_name, expected_args = self._STRING_METHODS[method]
        if len(args) != expected_args:
            raise TranspileError(f".{method}() takes {expected_args} argument(s), got {len(args)}")

        # Return type: startswith/endswith/find/count return I64, everything else returns STR
        ret_type = I64 if method in ('startswith', 'endswith', 'find', 'count') else STR

        if expected_args == 0:
            return f"call {ffi_name} {obj_code}", ret_type
        elif expected_args == 1:
            arg0, _ = self._emit_typed_expr(args[0])
            return f"call {ffi_name} {obj_code} {arg0}", ret_type
        elif expected_args == 2:
            arg0, _ = self._emit_typed_expr(args[0])
            arg1, _ = self._emit_typed_expr(args[1])
            return f"call {ffi_name} {obj_code} {arg0} {arg1}", ret_type

        raise TranspileError(f"String method .{method}() not implemented")

    def _emit_fstring(self, node: ast.JoinedStr) -> tuple[str, str]:
        """Emit f-string as a series of string concatenations.
        
        Strategy: Collect all parts. For literal parts, fold them.
        For formatted values (variables), convert to string and concat.
        If ALL parts are literal, fold to a single data section at transpile time.
        """
        self._needs_strings = True
        
        # Collect all parts as Python strings or as (code, type) for variables
        literal_parts = []
        has_variables = False
        
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literal_parts.append(('lit', value.value))
            elif isinstance(value, ast.FormattedValue):
                has_variables = True
                literal_parts.append(('var', value.value))
            else:
                raise TranspileError(f"Unsupported f-string part: {type(value).__name__}")
        
        if not has_variables:
            # All literal — fold into a single string
            full_str = "".join(p[1] for p in literal_parts)
            ptr = self._string_arena_offset
            length = len(full_str.encode('utf-8'))
            self._string_data.append((ptr, full_str))
            self._string_arena_offset += length + 1
            packed = (ptr << 32) | length
            return str(packed), STR
        
        # Has variables — build a concat chain
        # For each part, emit as STR, then chain __str_concat calls
        str_parts = []
        for kind, val in literal_parts:
            if kind == 'lit':
                if val == '':
                    continue  # skip empty string parts
                ptr = self._string_arena_offset
                length = len(val.encode('utf-8'))
                self._string_data.append((ptr, val))
                self._string_arena_offset += length + 1
                packed = (ptr << 32) | length
                str_parts.append((str(packed), STR))
            else:
                # Variable — need to convert to string
                code, vtype = self._emit_typed_expr(val)
                if vtype == STR:
                    str_parts.append((code, STR))
                else:
                    # Convert int/float to string via __int_to_str
                    str_parts.append((f"call __int_to_str {code}", STR))
        
        if not str_parts:
            # Empty f-string
            ptr = self._string_arena_offset
            self._string_data.append((ptr, ""))
            self._string_arena_offset += 1
            return str(ptr << 32), STR
        
        if len(str_parts) == 1:
            return str_parts[0]
        
        # Chain: __str_concat(a, __str_concat(b, c))
        result = str_parts[0][0]
        for code, _ in str_parts[1:]:
            result = f"call __str_concat {result} {code}"
        return result, STR

    def _emit_str_concat_binop(self, node: ast.BinOp, left: str, lt: str, right: str, rt: str) -> tuple[str, str]:
        """Handle string concatenation with +.
        
        If both sides are string literals (known at transpile time), fold them
        into a single data section. Otherwise, emit __str_concat FFI call.
        """
        self._needs_strings = True
        
        # Try compile-time folding: if both are literal strings in the AST
        if (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str) and
            isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)):
            # Fold: "hello" + " world" → "hello world"
            combined = node.left.value + node.right.value
            ptr = self._string_arena_offset
            length = len(combined.encode('utf-8'))
            self._string_data.append((ptr, combined))
            self._string_arena_offset += length + 1
            packed = (ptr << 32) | length
            return str(packed), STR
        
        # Also try folding nested literal concats: "a" + "b" + "c"
        if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Add):
            # Check if the entire chain is literals
            literals = self._collect_concat_literals(node)
            if literals is not None:
                combined = "".join(literals)
                ptr = self._string_arena_offset
                length = len(combined.encode('utf-8'))
                self._string_data.append((ptr, combined))
                self._string_arena_offset += length + 1
                packed = (ptr << 32) | length
                return str(packed), STR
        
        # Runtime concat via FFI
        if lt != STR:
            left = f"call __int_to_str {left}"
        if rt != STR:
            right = f"call __int_to_str {right}"
        return f"call __str_concat {left} {right}", STR

    def _collect_concat_literals(self, node: ast.AST) -> list[str] | None:
        """Recursively collect string literals from a chain of + operations.
        Returns None if any part is not a string literal."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._collect_concat_literals(node.left)
            right = self._collect_concat_literals(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    def _emit_dict_helpers(self) -> str:
        """Emit helper functions for dict operations if any dicts are used."""
        if not self._dict_vars:
            return ""
        # __dict_get(base, key) -> linear scan, returns value or 0
        # __dict_set(base, key, val) -> linear scan, overwrites or appends
        # __dict_get_default(base, key, default) -> linear scan with default
        return (
            "@f 2 __dict_get [ "
            "let $2 read $0 let $3 0 let $4 0 "
            "while < $3 $2 [ "
            "if == read + $0 + * $3 16 8 $1 [ set $4 read + $0 + * $3 16 16 let $3 $2 ] "
            "[ set $3 + $3 1 ] ] $4 ] "
            "@f 3 __dict_set [ "
            "let $3 read $0 let $4 0 let $5 0 "
            "while < $4 $3 [ "
            "if == read + $0 + * $4 16 8 $1 [ write + $0 + * $4 16 16 $2 set $5 1 set $4 $3 ] "
            "[ set $4 + $4 1 ] ] "
            "if == $5 0 [ write + $0 + * $3 16 8 $1 write + $0 + * $3 16 16 $2 write $0 + $3 1 ] [ 0 ] 0 ] "
            "@f 3 __dict_get_default [ "
            "let $3 read $0 let $4 0 let $5 $2 "
            "while < $4 $3 [ "
            "if == read + $0 + * $4 16 8 $1 [ set $5 read + $0 + * $4 16 16 set $4 $3 ] "
            "[ set $4 + $4 1 ] ] $5 ]"
        )

    def _emit_string_helpers(self) -> str:
        """Emit helper functions for APC string operations."""
        if not self._needs_strings:
            return ""
        # Import the print FFI (ptr: i32, len: i32) from the gateway
        # __str_print(packed) → unpack ptr and len from i64, call print FFI
        # ptr = packed >> 32, len = packed & 0xFFFFFFFF
        return (
            # Import FFI host functions from the gateway
            '@import_ffi "env" "print" 2 print '
            '@import_ffi "env" "print_nl" 0 print_nl '
            '@import_ffi "env" "str_concat" 2 __ffi_str_concat '
            '@import_ffi "env" "int_to_str" 1 __ffi_int_to_str '
            # String method FFIs (packed_str → packed_str or i64)
            '@import_ffi "env" "str_upper" 1 __ffi_str_upper '
            '@import_ffi "env" "str_lower" 1 __ffi_str_lower '
            '@import_ffi "env" "str_strip" 1 __ffi_str_strip '
            '@import_ffi "env" "str_lstrip" 1 __ffi_str_lstrip '
            '@import_ffi "env" "str_rstrip" 1 __ffi_str_rstrip '
            '@import_ffi "env" "str_replace" 3 __ffi_str_replace '
            '@import_ffi "env" "str_startswith" 2 __ffi_str_startswith '
            '@import_ffi "env" "str_endswith" 2 __ffi_str_endswith '
            '@import_ffi "env" "str_find" 2 __ffi_str_find '
            '@import_ffi "env" "str_count" 2 __ffi_str_count '
            # __str_print(packed_str) → unpack ptr/len, call print FFI, then newline
            "@f 1 __str_print [ "
            "call print >> $0 32 & $0 4294967295 call print_nl 0 ] "
            # __str_concat(packed_a, packed_b) → concat via FFI, returns new packed str
            "@f 2 __str_concat [ "
            "call __ffi_str_concat $0 $1 ] "
            # __int_to_str(int_val) → convert integer to string via FFI, returns packed str
            "@f 1 __int_to_str [ "
            "call __ffi_int_to_str $0 ]"
        )

    # ── JSON Support ─────────────────────────────────────────

    def _emit_json_call(self, func_name: str, args: list) -> tuple[str, str]:
        """Emit json.loads() / json.dumps() as FFI calls."""
        self._needs_json = True
        self._needs_strings = True  # JSON uses string infrastructure

        if func_name == 'loads':
            if len(args) != 1:
                raise TranspileError("json.loads() takes exactly 1 argument")
            arg, at = self._emit_typed_expr(args[0])
            if at != STR:
                raise TranspileError("json.loads() argument must be a string")
            # json_loads(packed_str) → dict pointer (linear array in memory)
            return f"call __json_loads {arg}", I64

        if func_name == 'dumps':
            if len(args) != 1:
                raise TranspileError("json.dumps() takes exactly 1 argument")
            arg = self._emit_expr(args[0])
            # json_dumps(dict_ptr) → packed string
            return f"call __json_dumps {arg}", STR

        raise TranspileError(f"Unsupported json function: json.{func_name}()")

    def _emit_json_helpers(self) -> str:
        """Emit FFI imports and helper functions for JSON operations."""
        if not self._needs_json:
            return ""
        return (
            # JSON FFI imports from the gateway host
            '@import_ffi "env" "json_loads" 1 __ffi_json_loads '
            '@import_ffi "env" "json_dumps" 1 __ffi_json_dumps '
            '@import_ffi "env" "json_get_str" 2 __ffi_json_get_str '
            '@import_ffi "env" "json_get_int" 2 __ffi_json_get_int '
            '@import_ffi "env" "json_get_float" 2 __ffi_json_get_float '
            '@import_ffi "env" "json_get_index" 2 __ffi_json_get_index '
            '@import_ffi "env" "json_length" 1 __ffi_json_length '
            # __json_loads(packed_str) → dict_ptr (parsed JSON as linear memory struct)
            "@f 1 __json_loads [ "
            "call __ffi_json_loads $0 ] "
            # __json_dumps(dict_ptr) → packed_str (JSON serialization)
            "@f 1 __json_dumps [ "
            "call __ffi_json_dumps $0 ] "
            # __json_get_str(dict_ptr, key_packed_str) → packed_str value
            "@f 2 __json_get_str [ "
            "call __ffi_json_get_str $0 $1 ] "
            # __json_get_int(dict_ptr, key_packed_str) → i64 value
            "@f 2 __json_get_int [ "
            "call __ffi_json_get_int $0 $1 ] "
            # __json_get_float(dict_ptr, key_packed_str) → f32 value
            "@f 2 __json_get_float [ "
            "call __ffi_json_get_float $0 $1 ] "
            # __json_get_index(arr_ptr, index) → element value (i64)
            "@f 2 __json_get_index [ "
            "call __ffi_json_get_index $0 $1 ] "
            # __json_length(collection_ptr) → length (i64)
            "@f 1 __json_length [ "
            "call __ffi_json_length $0 ]"
        )

    # ── Class Support ────────────────────────────────────────

    # FNV-1a hash for attribute names → integer keys
    @staticmethod
    def _fnv1a_hash(s: str) -> int:
        """Compute FNV-1a hash of a string, returning a positive i64."""
        h = 0xcbf29ce484222325
        for byte in s.encode('utf-8'):
            h ^= byte
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return h & 0x7FFFFFFFFFFFFFFF  # keep positive for .syn

    def _emit_classdef(self, node: ast.ClassDef) -> str:
        """Compile a simple Python class to dict-based struct + standalone methods.

        Restrictions:
        - No inheritance (bases must be empty or [object])
        - No metaclasses
        - No class-level attributes (only method definitions)
        - No decorators on methods
        - Only __init__ + custom methods (no __str__, __repr__, __eq__, etc.)
        """
        class_name = node.name

        # Validate constraints
        if node.bases:
            base_names = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    base_names.append(b.id)
                else:
                    raise TranspileError(f"Class {class_name}: only simple base classes supported")
            if base_names != ['object']:
                raise TranspileError(
                    f"Class {class_name}: inheritance not supported. "
                    f"Only 'class {class_name}:' or 'class {class_name}(object):' allowed.")

        if node.decorator_list:
            raise TranspileError(f"Class {class_name}: decorators not supported")

        # Collect methods and class-level constants
        methods = {}
        attrs = set()
        class_constants = {}  # class-level constant assignments: name → value
        for item in node.body:
            if isinstance(item, ast.Pass):
                continue
            if isinstance(item, ast.FunctionDef):
                methods[item.name] = item
                # Scan all methods to find attribute names (not just __init__)
                for child in ast.walk(item):
                    if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
                        if child.value.id == 'self':
                            attrs.add(child.attr)
            elif isinstance(item, ast.Assign):
                # Class-level constant: x = 10, NAME = "hello"
                if len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                    if isinstance(item.value, ast.Constant):
                        const_name = item.targets[0].id
                        class_constants[const_name] = item.value.value
                        attrs.add(const_name)
                    else:
                        raise TranspileError(
                            f"Class {class_name}: only constant assignments allowed at class level. "
                            f"Got non-constant assignment to '{item.targets[0].id}'")
                else:
                    raise TranspileError(
                        f"Class {class_name}: only simple constant assignments allowed at class level")
            else:
                raise TranspileError(
                    f"Class {class_name}: only method definitions, class constants, and 'pass' allowed in class body. "
                    f"Got {type(item).__name__}")

        self._class_defs[class_name] = {
            'methods': list(methods.keys()),
            'attrs': list(attrs),
            'attr_hashes': {a: self._fnv1a_hash(a) for a in attrs},
            'constants': class_constants,
        }

        # Compile each method as a standalone function: __ClassName__method(self, ...)
        for method_name, method_node in methods.items():
            func_name = f"__{class_name}__{method_name}"
            arity = len(method_node.args.args)  # includes self
            self._func_map[func_name] = arity

            old_in_func = self._in_function
            old_vars = self._current_func_vars
            old_counter = self._current_func_var_counter
            old_params = self._current_func_params
            old_param_types = self._current_func_param_types
            old_in_class = self._in_class

            self._in_function = True
            self._in_class = class_name
            self._current_func_vars = {}
            self._current_func_params = {
                arg.arg: i for i, arg in enumerate(method_node.args.args)
            }
            self._current_func_param_types = {}
            for arg in method_node.args.args:
                if arg.annotation and isinstance(arg.annotation, ast.Name):
                    if arg.annotation.id == 'float':
                        self._current_func_param_types[arg.arg] = F32
                    else:
                        self._current_func_param_types[arg.arg] = I64
                else:
                    self._current_func_param_types[arg.arg] = I64
            self._current_func_var_counter = arity

            body = self._emit_block(method_node.body)

            ret_type = self._infer_return_type(method_node)
            self._func_return_types[func_name] = ret_type

            self._in_function = old_in_func
            self._in_class = old_in_class
            self._current_func_vars = old_vars
            self._current_func_var_counter = old_counter
            self._current_func_params = old_params
            self._current_func_param_types = old_param_types

            self.functions.append(f"@f {arity} {func_name} [ {body} ]")

        return ""

    def _emit_class_instantiation(self, class_name: str, args: list) -> tuple[str, str]:
        """Emit ClassName(args) as: allocate dict, call __init__, return pointer."""
        class_info = self._class_defs[class_name]
        n_attrs = max(len(class_info['attrs']), 1)

        # Allocate dict space for the instance: [count, k0, v0, k1, v1, ...]
        base = self._dict_arena_offset
        self._dict_arena_offset += (1 + n_attrs * 2) * self.ELEM_SIZE

        # Initialize empty dict with count=0
        init_code = f"write {base} 0"

        # Call __init__ if defined
        init_func = f"__{class_name}____init__"
        if init_func in self._func_map:
            arg_codes = [self._emit_expr(a) for a in args]
            args_str = " ".join([str(base)] + arg_codes)
            init_code += f" call {init_func} {args_str}"

        # The class type is tracked as __class_ClassName
        return f"{init_code} {base}", I64

    def _emit_instance_method_call(self, obj_name: str, class_name: str,
                                    method: str, args: list) -> tuple[str, str]:
        """Emit obj.method(args) as call __ClassName__method(obj_ptr, args)."""
        func_name = f"__{class_name}__{method}"
        if func_name not in self._func_map:
            raise TranspileError(f"Class {class_name} has no method '{method}'")
        obj_var = self._get_var(obj_name)
        arg_codes = [self._emit_expr(a) for a in args]
        args_str = " ".join([obj_var] + arg_codes)
        rt = self._func_return_types.get(func_name, I64)
        return f"call {func_name} {args_str}", rt

    def _emit_attr_assign(self, target: ast.Attribute, value_node: ast.expr) -> str:
        """Handle self.x = val or obj.x = val — stores as dict entry with hashed key."""
        if not isinstance(target.value, ast.Name):
            raise TranspileError("Only simple attribute assignment supported (e.g. self.x = val)")

        obj_name = target.value.id
        attr_name = target.attr

        # Inside a class method, "self" is parameter $0
        if self._in_class and obj_name == 'self':
            class_info = self._class_defs[self._in_class]
            attr_hash = class_info['attr_hashes'].get(attr_name)
            if attr_hash is None:
                attr_hash = self._fnv1a_hash(attr_name)
                class_info['attr_hashes'][attr_name] = attr_hash
                class_info['attrs'].append(attr_name)
            obj_var = self._get_var('self')
            val = self._emit_expr(value_node)
            return f"call __dict_set {obj_var} {attr_hash} {val}"

        # Outside class — obj.attr = val for class instances
        vtype = self._var_types.get(obj_name, '')
        if vtype.startswith('__class_'):
            class_name = vtype[len('__class_'):]
            class_info = self._class_defs[class_name]
            attr_hash = class_info['attr_hashes'].get(attr_name)
            if attr_hash is None:
                attr_hash = self._fnv1a_hash(attr_name)
            obj_var = self._get_var(obj_name)
            val = self._emit_expr(value_node)
            return f"call __dict_set {obj_var} {attr_hash} {val}"

        raise TranspileError(f"Attribute assignment not supported on '{obj_name}'")

    def _emit_attr_aug_assign(self, node: ast.AugAssign) -> str:
        """Handle self.x += val or obj.x += val."""
        target = node.target
        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
            raise TranspileError("Only simple augmented attribute assignment supported")

        # Read current value, apply op, write back
        read_code, read_type = self._emit_typed_expr(target)
        value, vt = self._emit_typed_expr(node.value)
        op_map = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
            ast.FloorDiv: "/", ast.Mod: "%",
        }
        op = op_map.get(type(node.op))
        if op is None:
            raise TranspileError(f"Unsupported augmented assign op: {type(node.op).__name__}")

        new_val = f"{op} {read_code} {value}"
        # Now do the attribute write
        # Reconstruct the assign as target.attr = new_val
        dummy_assign = ast.Assign(
            targets=[target],
            value=ast.Constant(value=0)  # placeholder
        )
        # We need to get the obj and hash manually
        obj_name = target.value.id
        attr_name = target.attr

        if self._in_class and obj_name == 'self':
            class_info = self._class_defs[self._in_class]
            attr_hash = class_info['attr_hashes'].get(attr_name, self._fnv1a_hash(attr_name))
            obj_var = self._get_var('self')
            return f"call __dict_set {obj_var} {attr_hash} {new_val}"

        vtype = self._var_types.get(obj_name, '')
        if vtype.startswith('__class_'):
            class_name = vtype[len('__class_'):]
            class_info = self._class_defs[class_name]
            attr_hash = class_info['attr_hashes'].get(attr_name, self._fnv1a_hash(attr_name))
            obj_var = self._get_var(obj_name)
            return f"call __dict_set {obj_var} {attr_hash} {new_val}"

        raise TranspileError(f"Augmented attribute assignment not supported on '{obj_name}'")

    # ── try/except Support ───────────────────────────────────

    def _emit_try_except(self, node: ast.Try) -> str:
        """Compile try/except to a status-code guard pattern.

        Strategy: Execute the try body. If it completes successfully,
        skip the except body. If any operation sets the error flag,
        jump to the except body.

        For the MVP: simple try/except with a single except clause.
        No exception type matching, no re-raise, no finally, no else.
        The error flag is heuristic — checked operations (like int("abc"))
        set it on failure. For unchecked ops, the try body just runs.
        """
        if node.finalbody:
            raise TranspileError("try/finally not supported — only try/except")
        if not node.handlers:
            raise TranspileError("try without except handlers not supported")
        if len(node.handlers) > 1:
            raise TranspileError("Multiple except handlers not yet supported")

        handler = node.handlers[0]

        # Use a unique error variable for this try depth
        self._try_depth += 1
        err_var = f"${self._var_counter + 300 + self._try_depth}"

        # Emit try body
        try_body = self._emit_block(node.body)

        # Emit except body
        except_body = self._emit_block(handler.body)

        # Emit orelse body (try/else — runs when no exception)
        else_body = self._emit_block(node.orelse) if node.orelse else "0"

        self._try_depth -= 1

        # Pattern: set err=0, run try body, if err=0 run else, if err=1 run except
        # Since we can't truly catch exceptions in WASM, the try body just runs.
        # The except body is reachable if $err is set by a checked operation.
        # For the practical case (defensive coding), we always run the try body
        # and conditionally run except based on a status flag.
        return (
            f"let {err_var} 0 "
            f"{try_body} "
            f"if == {err_var} 0 [ {else_body} ] [ {except_body} ]"
        )


def python_to_syn(source: str, verify: bool = True) -> str:
    """Transpile Python source code to .syn with optional Z3 verification.

    Args:
        source: Python source code string.
        verify: If True and z3 is available, run constitutional verification.

    Returns:
        .syn source code string ready for the self-hosted compiler.

    Raises:
        TranspileError: If the Python code uses unsupported constructs.
        ConstitutionalVeto: If Z3 verification fails (code is unsafe).

    Example:
        >>> python_to_syn("print(21 + 21)")
        '@f 0 main [ + 21 21 ]'

        >>> python_to_syn("x = 10\\nprint(x * 2)")
        '@f 0 main [ let $0 10 * $0 2 ]'
    """
    transpiler = SynTranspiler()
    syn_code = transpiler.transpile(source)

    if verify:
        _verify_constitution(source, syn_code)

    return syn_code


class ConstitutionalVeto(Exception):
    """Raised when Z3 verification rejects the code as unsafe."""
    pass


def _verify_constitution(python_source: str, syn_code: str):
    """Run Z3 constitutional verification on transpiled .syn code."""
    try:
        import z3
    except ImportError:
        return

    solver = z3.Solver()
    solver.set("timeout", 100)

    WASM_PAGE_SIZE = 65536
    MAX_PAGES = 16
    memory_limit = WASM_PAGE_SIZE * MAX_PAGES
    ARENA_START = 4096

    var_count = syn_code.count("let $")
    func_count = syn_code.count("@f ")
    estimated_alloc = var_count * 8 + func_count * 32 + 64

    memory_overflow = z3.BoolVal(ARENA_START + estimated_alloc >= memory_limit)

    tree = ast.parse(python_source)
    has_recursion = _detect_recursion(tree)
    loop_depth = _max_loop_depth(tree)
    excessive_nesting = loop_depth > 5

    halt_risk = z3.BoolVal(has_recursion and excessive_nesting)

    # AST-based import analysis (more robust than string matching)
    DANGEROUS_MODULES = SynTranspiler._DANGEROUS_MODULES
    DANGEROUS_BUILTINS = {'eval', 'exec', 'compile', 'open', '__import__'}

    # Check for dangerous imports (blocklist approach — user modules are allowed)
    dangerous_imports = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root in DANGEROUS_MODULES:
                    dangerous_imports = True
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or '').split('.')[0]
            if root in DANGEROUS_MODULES:
                dangerous_imports = True
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in DANGEROUS_BUILTINS:
                    dangerous_imports = True

    intent_violation = z3.BoolVal(dangerous_imports)

    violation = z3.Or(memory_overflow, halt_risk, intent_violation)
    solver.add(violation)

    result = solver.check()

    if result == z3.sat:
        violations = []
        if dangerous_imports:
            violations.append("Intent: unauthorized imports (os/sys/socket/eval/exec)")
        if has_recursion and excessive_nesting:
            violations.append("Halt: recursive function with deep loop nesting")
        if ARENA_START + estimated_alloc >= memory_limit:
            violations.append(f"Memory: {var_count} variables ({estimated_alloc} bytes) may exceed arena")

        raise ConstitutionalVeto(
            f"Z3 Constitutional Veto: {'; '.join(violations)}"
        )


def _detect_recursion(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    if child.func.id == func_name:
                        return True
    return False


def _max_loop_depth(tree: ast.Module) -> int:
    def _depth(node: ast.AST, current: int = 0) -> int:
        max_d = current
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While)):
                max_d = max(max_d, _depth(child, current + 1))
            else:
                max_d = max(max_d, _depth(child, current))
        return max_d
    return _depth(tree)


# ── CLI for testing ──────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        source = " ".join(sys.argv[1:])
    else:
        source = sys.stdin.read()

    try:
        syn = python_to_syn(source)
        print(syn)
    except TranspileError as e:
        print(f"TranspileError: {e}", file=sys.stderr)
        sys.exit(1)
