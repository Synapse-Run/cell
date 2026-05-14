"""Dockerfile → Synapse Cell .celltemplate transpiler — Sprint C Phase C4.

The E2B replacement bridge. Customers bring their existing Dockerfiles;
this module converts them to TemplateInfo JSON that the gateway registers
as a Wasm-native template (no Docker runtime needed).

Supported (80% of AI agent Dockerfiles):
    FROM python:3.x[-slim|-alpine]  -> runtime: python3
    FROM node:18[-alpine]           -> runtime: javascript
    RUN pip install pkg1 pkg2       -> packages: [pkg1, pkg2]
    RUN npm install pkg1 pkg2       -> packages: [pkg1, pkg2]
    RUN pip install -r req.txt      -> read file, merge into packages
    COPY src dest                   -> files: [{src, dest}]
    WORKDIR /path                   -> working_directory: /path
    ENV KEY=VALUE                   -> envs: {KEY: VALUE}
    CMD ["cmd","arg"] / CMD cmd     -> start_command: cmd arg
    ENTRYPOINT ... + CMD ...        -> merge into start_command
    USER name                       -> user: name
    LABEL k=v                       -> metadata: {k: v}

Unsupported (emit Warning with migration_hint):
    FROM <custom-image>  ->  error, can't pull Docker images
    RUN apt-get install  ->  warning + FFI mapping (git -> cell.git, etc.)
    RUN <arbitrary sh>   ->  warning, use start_command instead
    EXPOSE <port>        ->  warning, Cell is client-initiated
    ADD <url>            ->  warning, use cell.fetch()
    HEALTHCHECK          ->  warning, Cell has built-in health
    VOLUME               ->  warning, use volume_mounts in Sandbox.create()

Usage:
    from synapse.dockerfile_transpiler import transpile_dockerfile_file
    spec, warnings = transpile_dockerfile_file("Dockerfile")
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


# ─── Exceptions + data classes ───────────────────────────────────

class TranspileError(Exception):
    """Raised for Dockerfile directives that have no Cell equivalent."""
    def __init__(self, message: str, line: int = 0, migration_hint: str = ""):
        super().__init__(message)
        self.line = line
        self.migration_hint = migration_hint


@dataclass
class Directive:
    """A single parsed Dockerfile directive."""
    op: str                  # FROM, RUN, COPY, ENV, etc. (uppercase)
    args: str                # the raw argument string (after the op)
    line: int                # 1-indexed line number in the source

    def __repr__(self) -> str:
        return f"Directive({self.op} {self.args!r} @L{self.line})"


@dataclass
class Warning:
    """A non-fatal migration warning."""
    level: str               # "info", "warning", "error"
    message: str             # what the user sees
    migration_hint: str = "" # actionable Cell alternative
    line: int = 0

    def __str__(self) -> str:
        out = f"[{self.level}] line {self.line}: {self.message}"
        if self.migration_hint:
            out += f"\n         -> {self.migration_hint}"
        return out


# ─── Migration hint catalog ──────────────────────────────────────

UNSUPPORTED_MIGRATION = {
    "apt-git": "Use cell.git.clone() / cell.git.commit() — Cell has a first-class git namespace.",
    "apt-curl": "Use cell.fetch(url) — Cell proxies HTTP through the gateway with SSRF protection.",
    "apt-wget": "Use cell.fetch(url) — same as curl replacement.",
    "apt-ffmpeg": "ffmpeg is not yet available as a Wasm FFI; contact us for enterprise Wasm-packaged ffmpeg.",
    "apt-imagemagick": "ImageMagick requires C extensions; contact us for a Wasm-compiled build.",
    "apt-generic": "System packages via apt are not supported in Wasm. Check if a pure-Python / JS alternative exists.",
    "custom-base-image": "Custom base images require Docker runtime. Use runtime='python3' or 'javascript' and install your packages via the `packages` field.",
    "expose-port": "Cell sandboxes are client-initiated. For outbound HTTP use cell.fetch(). For inbound services, contact us about the Hub tier's custom-domain feature.",
    "run-shell-arbitrary": "RUN shell commands in Dockerfile run once at build time. In Cell, the equivalent is `start_command` which runs at sandbox creation.",
    "add-from-url": "ADD from URL is not supported. Use cell.fetch(url) at runtime, or download the file locally and use COPY.",
    "multi-stage-build": "Cell flattens multi-stage builds. The last FROM determines the runtime; all RUN pip install lines are merged.",
    "healthcheck": "HEALTHCHECK is not needed. Cell has a built-in /v1/health endpoint and per-sandbox status checks.",
    "volume": "VOLUME declarations are not supported as build-time. Use `volume_mounts` in Sandbox.create() to attach persistent volumes at runtime.",
    "onbuild": "ONBUILD triggers require Docker's layered build system. Cell templates are flat; define the work directly.",
    "shell": "SHELL directive changes the default shell for RUN. In Cell, `start_command` runs via /bin/sh directly.",
    "stopsignal": "STOPSIGNAL is Docker-specific. Cell sandboxes are killed via the DELETE /v1/cells/{id} API or cell.kill().",
}

# apt package -> Cell migration path
APT_KNOWN_TOOLS = {
    "git": "apt-git",
    "git-core": "apt-git",
    "curl": "apt-curl",
    "wget": "apt-wget",
    "ffmpeg": "apt-ffmpeg",
    "imagemagick": "apt-imagemagick",
}

# Known image registries / patterns that indicate a custom image
CUSTOM_IMAGE_PATTERNS = [
    r"^[\w.-]+/[\w.-]+",           # user/image  (Docker Hub)
    r"^[\w.-]+\.[a-z]{2,}/.+",      # registry.io/...
    r"^(tensorflow|pytorch|nvidia|alpine|ubuntu|debian|centos|fedora|rockylinux|amazonlinux|almalinux|opensuse|archlinux|oraclelinux|busybox|scratch)\b",
]


# ─── Parser ──────────────────────────────────────────────────────

def parse_dockerfile(source: str) -> List[Directive]:
    """Parse Dockerfile source into a list of Directives.

    Handles:
      - Comments (#) stripped
      - Line continuations (backslash at end of line)
      - Empty lines ignored
      - Case-insensitive directive names (normalized to uppercase)
    """
    directives: List[Directive] = []
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        stripped = raw.strip()
        # Skip comments and blank lines
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        # Handle backslash line continuations: join with next line(s)
        while stripped.endswith("\\") and i + 1 < len(lines):
            stripped = stripped[:-1].rstrip() + " " + lines[i + 1].strip()
            i += 1
        # Split into opcode + args
        m = re.match(r"^(\w+)\s*(.*)$", stripped, flags=re.DOTALL)
        if not m:
            i += 1
            continue
        op = m.group(1).upper()
        args = m.group(2).strip()
        directives.append(Directive(op=op, args=args, line=lineno))
        i += 1
    return directives


# ─── Directive handlers ──────────────────────────────────────────

def _handle_from(d: Directive, spec: Dict, warnings: List[Warning]) -> None:
    """Map FROM python:X -> runtime:python3, FROM node:X -> runtime:javascript."""
    args = d.args
    # Strip BuildKit flags: --platform=linux/amd64, --chown=, etc.
    args = re.sub(r"--\w+=\S+\s*", "", args).strip()
    # Strip "AS stage-name" suffix
    image_ref = args.split(" AS ", 1)[0].split(" as ", 1)[0].strip()

    # Detect multi-stage
    if len(spec.get("_from_count", [])) > 0:
        warnings.append(Warning(
            level="warning",
            message=f"Multi-stage build detected (second FROM: {image_ref})",
            migration_hint=UNSUPPORTED_MIGRATION["multi-stage-build"],
            line=d.line,
        ))
    spec.setdefault("_from_count", []).append(image_ref)

    # Parse image:tag
    if ":" in image_ref:
        image, tag = image_ref.split(":", 1)
    else:
        image, _tag = image_ref, "latest"
    image = image.lower()

    if image in ("python", "python3"):
        spec["runtime"] = "python3"
    elif image in ("node", "nodejs"):
        spec["runtime"] = "javascript"
    elif image == "scratch":
        raise TranspileError(
            "FROM scratch requires a statically-linked Wasm module. "
            "Use runtime='python3' or 'javascript' and add your code via COPY.",
            line=d.line,
            migration_hint=UNSUPPORTED_MIGRATION["custom-base-image"],
        )
    else:
        # Custom image — check if it matches a known pattern
        for pattern in CUSTOM_IMAGE_PATTERNS:
            if re.match(pattern, image_ref):
                raise TranspileError(
                    f"Custom base image '{image_ref}' is not supported. "
                    f"Cell templates run on real CPython-WASI and QuickJS-WASI.",
                    line=d.line,
                    migration_hint=UNSUPPORTED_MIGRATION["custom-base-image"],
                )
        # Unknown — warn but default to python3
        warnings.append(Warning(
            level="warning",
            message=f"Unknown base image '{image_ref}', defaulting to runtime=python3",
            migration_hint=UNSUPPORTED_MIGRATION["custom-base-image"],
            line=d.line,
        ))
        spec["runtime"] = "python3"


def _parse_pip_packages(args: str) -> List[str]:
    """Extract package names from a `pip install [flags] pkg1 pkg2` string.

    Drops flags (--no-cache-dir, --upgrade, etc.) and leaves package specs
    (with ==, >=, [extras]) intact.
    """
    tokens = shlex.split(args)
    packages = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in ("pip", "pip3", "install", "-m"):
            continue
        if tok.startswith("-"):
            # Flag — skip this and potentially its value
            # Flags with values: --index-url, --extra-index-url, -r, -t, etc.
            if tok in ("-r", "--requirement", "-t", "--target",
                      "--index-url", "--extra-index-url", "--find-links",
                      "--constraint", "-c"):
                skip_next = True
            continue
        packages.append(tok)
    return packages


def _parse_npm_packages(args: str) -> List[str]:
    """Extract package names from `npm install [flags] pkg1 pkg2`."""
    tokens = shlex.split(args)
    packages = []
    for tok in tokens:
        if tok in ("npm", "install", "i", "add"):
            continue
        if tok.startswith("-"):
            continue
        packages.append(tok)
    return packages


def _read_requirements_file(path: str, base_dir: str) -> List[str]:
    """Read a requirements.txt-style file and return package specs.

    Returns empty list if file can't be read — the caller emits a warning.
    """
    candidate = path
    if not os.path.isabs(candidate):
        candidate = os.path.join(base_dir, path)
    if not os.path.exists(candidate):
        return []
    packages = []
    with open(candidate, "r") as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("-"):  # e.g. -r other.txt, -e .
                continue
            packages.append(stripped)
    return packages


def _handle_run(d: Directive, spec: Dict, warnings: List[Warning], base_dir: str) -> None:
    """Interpret RUN commands — extract pip/npm installs, warn on others."""
    cmd = d.args.strip()
    # Split multiple commands on && / ; for processing
    for sub in re.split(r"\s*&&\s*|\s*;\s*", cmd):
        sub = sub.strip()
        if not sub:
            continue
        _handle_run_single(sub, d.line, spec, warnings, base_dir)


def _handle_run_single(sub: str, line: int, spec: Dict, warnings: List[Warning], base_dir: str) -> None:
    # Strip BuildKit RUN flags (--mount=, --network=, --security=)
    sub = re.sub(r"--(?:mount|network|security)=\S+\s*", "", sub).strip()
    if not sub:
        return
    # pip install variants
    pip_match = re.match(r"(?:python3?\s+-m\s+)?pip3?\s+install\b", sub)
    if pip_match:
        args_after = sub[pip_match.end():].strip()
        # Check for -r requirements.txt
        req_match = re.search(r"(?:-r|--requirement)\s+(\S+)", args_after)
        if req_match:
            req_path = req_match.group(1)
            req_pkgs = _read_requirements_file(req_path, base_dir)
            if req_pkgs:
                spec.setdefault("packages", []).extend(req_pkgs)
            else:
                warnings.append(Warning(
                    level="warning",
                    message=f"requirements file '{req_path}' not readable; skipped",
                    migration_hint="Place the file alongside the Dockerfile, or list packages inline.",
                    line=line,
                ))
        # Also parse inline packages (pip install -r req.txt + explicit pkgs)
        inline_pkgs = _parse_pip_packages(args_after)
        if inline_pkgs:
            spec.setdefault("packages", []).extend(inline_pkgs)
        return

    # npm install
    # npm install / npm i / npm add / npm ci / yarn install / yarn add / pnpm install / pnpm add / bun install
    npm_match = re.match(r"(?:npm|yarn|pnpm|bun)\s+(install|i|ci|add)\b", sub)
    if npm_match:
        args_after = sub[npm_match.end():].strip()
        pkgs = _parse_npm_packages(args_after)
        if pkgs:
            spec.setdefault("packages", []).extend(pkgs)
        # `npm ci` / `yarn install` / etc. without args = install from lockfile;
        # we can't resolve packages at transpile time, but we should at least warn.
        elif npm_match.group(1) in ("ci", "install"):
            # Look for package.json reference in COPY directives (later phase)
            spec.setdefault("_lockfile_install", True)
        return

    # apt-get / apt install
    apt_match = re.match(r"(?:apt-get|apt)\s+(?:install|-y\s+install|install\s+-y)\s+(.+)", sub)
    if apt_match:
        pkgs = shlex.split(apt_match.group(1))
        pkgs = [p for p in pkgs if not p.startswith("-")]
        for pkg in pkgs:
            hint_key = APT_KNOWN_TOOLS.get(pkg, "apt-generic")
            warnings.append(Warning(
                level="warning",
                message=f"apt package '{pkg}' cannot be installed in Wasm sandbox",
                migration_hint=UNSUPPORTED_MIGRATION[hint_key],
                line=line,
            ))
        return

    # apt-get update / apt update — silently ignore (not actionable)
    if re.match(r"(?:apt-get|apt)\s+(update|upgrade|clean|autoclean|autoremove)", sub):
        return

    # mkdir, chmod, chown — common infra, treat as no-op with light warning
    if re.match(r"(mkdir|chmod|chown|rm|cp|mv|ln|touch|echo)\b", sub):
        warnings.append(Warning(
            level="info",
            message=f"RUN shell command '{sub[:60]}...' ignored; filesystem set up via COPY and cell.files API",
            line=line,
        ))
        return

    # Arbitrary shell
    warnings.append(Warning(
        level="warning",
        message=f"RUN command '{sub[:60]}...' ignored (Docker RUN runs at build time)",
        migration_hint=UNSUPPORTED_MIGRATION["run-shell-arbitrary"],
        line=line,
    ))


def _handle_copy_add(d: Directive, spec: Dict, warnings: List[Warning], is_add: bool = False) -> None:
    """Handle COPY src dest (or COPY --from=... src dest) and ADD."""
    args = d.args
    # Drop --from / --chown flags
    args = re.sub(r"--\w+=\S+\s*", "", args).strip()
    tokens = shlex.split(args)
    if len(tokens) < 2:
        return
    # ADD from URL?
    if is_add and re.match(r"https?://", tokens[0]):
        warnings.append(Warning(
            level="warning",
            message=f"ADD from URL '{tokens[0]}' is not supported",
            migration_hint=UNSUPPORTED_MIGRATION["add-from-url"],
            line=d.line,
        ))
        return
    # Last token is destination, everything before is source(s)
    dest = tokens[-1]
    sources = tokens[:-1]
    for src in sources:
        spec.setdefault("files", []).append({"src": src, "dest": dest})


def _handle_env(d: Directive, spec: Dict, warnings: List[Warning]) -> None:
    """ENV KEY=VALUE [KEY=VALUE ...]  or  ENV KEY VALUE (legacy)."""
    args = d.args.strip()
    envs = spec.setdefault("envs", {})
    # Modern form: KEY=VALUE pairs
    if "=" in args:
        # Use shlex to handle quoted values
        try:
            tokens = shlex.split(args)
        except ValueError:
            tokens = args.split()
        for tok in tokens:
            if "=" in tok:
                k, v = tok.split("=", 1)
                envs[k] = v
    else:
        # Legacy form: ENV KEY VALUE (space-separated, first word is key)
        parts = args.split(None, 1)
        if len(parts) == 2:
            envs[parts[0]] = parts[1].strip('"\'')


def _handle_label(d: Directive, spec: Dict) -> None:
    """LABEL k=v [k=v ...] — map to metadata."""
    metadata = spec.setdefault("metadata", {})
    try:
        tokens = shlex.split(d.args)
    except ValueError:
        tokens = d.args.split()
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            metadata[k] = v


def _parse_cmd_or_entrypoint(args: str) -> str:
    """Parse CMD/ENTRYPOINT — JSON array form or shell form — return command string."""
    args = args.strip()
    # JSON array form: ["cmd", "arg1", "arg2"]
    if args.startswith("[") and args.endswith("]"):
        try:
            parts = json.loads(args)
            if isinstance(parts, list):
                return " ".join(shlex.quote(str(p)) for p in parts)
        except (json.JSONDecodeError, ValueError):
            pass
    # Shell form: the whole args string is the command
    return args


def _handle_cmd(d: Directive, spec: Dict) -> None:
    """CMD [...] or CMD cmd arg — sets start_command (merged with ENTRYPOINT if present)."""
    cmd_str = _parse_cmd_or_entrypoint(d.args)
    entrypoint = spec.get("_entrypoint", "")
    if entrypoint:
        spec["start_command"] = f"{entrypoint} {cmd_str}".strip()
    else:
        spec["start_command"] = cmd_str


def _handle_entrypoint(d: Directive, spec: Dict) -> None:
    """ENTRYPOINT [...] — stash; merge with CMD later."""
    spec["_entrypoint"] = _parse_cmd_or_entrypoint(d.args)
    # If a CMD already set start_command, prepend entrypoint
    if spec.get("start_command"):
        spec["start_command"] = f"{spec['_entrypoint']} {spec['start_command']}".strip()


# ─── Top-level transpile ─────────────────────────────────────────

def directives_to_template(
    directives: List[Directive],
    base_dir: str = ".",
) -> Tuple[Dict[str, Any], List[Warning]]:
    """Convert parsed directives into a TemplateInfo dict + warnings."""
    spec: Dict[str, Any] = {
        "name": "",            # filled in by caller
        "version": "1.0.0",
        "runtime": "python3",  # default
        "description": "Transpiled from Dockerfile by Synapse Cell",
        "author": "",
        "packages": [],
        "files": [],
        "envs": {},
        "metadata": {},
        "start_command": None,
        "ready_command": None,
        "user": "sandbox",
        "working_directory": "/data",
    }
    warnings: List[Warning] = []

    for d in directives:
        op = d.op
        try:
            if op == "FROM":
                _handle_from(d, spec, warnings)
            elif op == "RUN":
                _handle_run(d, spec, warnings, base_dir)
            elif op == "COPY":
                _handle_copy_add(d, spec, warnings, is_add=False)
            elif op == "ADD":
                _handle_copy_add(d, spec, warnings, is_add=True)
            elif op == "ENV":
                _handle_env(d, spec, warnings)
            elif op == "LABEL":
                _handle_label(d, spec)
            elif op == "WORKDIR":
                spec["working_directory"] = d.args.strip()
            elif op == "USER":
                spec["user"] = d.args.strip()
            elif op == "CMD":
                _handle_cmd(d, spec)
            elif op == "ENTRYPOINT":
                _handle_entrypoint(d, spec)
            elif op == "EXPOSE":
                warnings.append(Warning(
                    level="warning",
                    message=f"EXPOSE {d.args} is not supported",
                    migration_hint=UNSUPPORTED_MIGRATION["expose-port"],
                    line=d.line,
                ))
            elif op == "HEALTHCHECK":
                warnings.append(Warning(
                    level="info",
                    message="HEALTHCHECK ignored",
                    migration_hint=UNSUPPORTED_MIGRATION["healthcheck"],
                    line=d.line,
                ))
            elif op == "VOLUME":
                warnings.append(Warning(
                    level="warning",
                    message=f"VOLUME {d.args} is a build-time declaration; use volume_mounts at runtime",
                    migration_hint=UNSUPPORTED_MIGRATION["volume"],
                    line=d.line,
                ))
            elif op == "ONBUILD":
                warnings.append(Warning(
                    level="warning",
                    message="ONBUILD is not supported",
                    migration_hint=UNSUPPORTED_MIGRATION["onbuild"],
                    line=d.line,
                ))
            elif op == "SHELL":
                warnings.append(Warning(
                    level="info",
                    message="SHELL directive ignored",
                    migration_hint=UNSUPPORTED_MIGRATION["shell"],
                    line=d.line,
                ))
            elif op == "STOPSIGNAL":
                warnings.append(Warning(
                    level="info",
                    message="STOPSIGNAL ignored",
                    migration_hint=UNSUPPORTED_MIGRATION["stopsignal"],
                    line=d.line,
                ))
            elif op in ("ARG",):
                # ARG is build-time only in Docker; ignore
                pass
            elif op in ("MAINTAINER",):
                # Deprecated Docker directive
                pass
            else:
                warnings.append(Warning(
                    level="info",
                    message=f"Unknown directive '{op}' ignored",
                    line=d.line,
                ))
        except TranspileError:
            raise
        except Exception as e:  # noqa: BLE001
            warnings.append(Warning(
                level="warning",
                message=f"Failed to process {op}: {e}",
                line=d.line,
            ))

    # Cleanup: remove private keys, deduplicate packages
    spec.pop("_from_count", None)
    spec.pop("_entrypoint", None)
    # Dedup packages while preserving order
    seen = set()
    deduped_pkgs = []
    for p in spec["packages"]:
        if p not in seen:
            deduped_pkgs.append(p)
            seen.add(p)
    spec["packages"] = deduped_pkgs
    # Drop empty collections (cleaner JSON output)
    if not spec["packages"]:
        del spec["packages"]
    if not spec["files"]:
        del spec["files"]
    if not spec["envs"]:
        del spec["envs"]
    if not spec["metadata"]:
        del spec["metadata"]

    return spec, warnings


def transpile_dockerfile(source: str, base_dir: str = ".") -> Tuple[Dict[str, Any], List[Warning]]:
    """Parse + transpile Dockerfile source into (TemplateInfo dict, warnings)."""
    directives = parse_dockerfile(source)
    if not directives:
        raise TranspileError("Empty Dockerfile")
    return directives_to_template(directives, base_dir=base_dir)


def transpile_dockerfile_file(path: str) -> Tuple[Dict[str, Any], List[Warning]]:
    """Read a Dockerfile and transpile it.

    The directory of the Dockerfile is used as base_dir for resolving
    relative paths in RUN pip install -r and COPY directives.
    """
    if not os.path.exists(path):
        raise TranspileError(f"Dockerfile not found: {path}")
    with open(path, "r") as f:
        source = f.read()
    base_dir = os.path.dirname(os.path.abspath(path))
    spec, warnings = transpile_dockerfile(source, base_dir=base_dir)

    # Use parent directory name as default template name
    parent_name = os.path.basename(os.path.dirname(os.path.abspath(path)))
    if parent_name and not spec.get("name"):
        # Sanitize: lowercase, alphanum+dash only
        clean = re.sub(r"[^a-z0-9-]", "-", parent_name.lower()).strip("-")
        spec["name"] = clean or "cell-template"

    return spec, warnings
