"""Synapse CLI — sandbox management + legacy .syn running.

Cell sandbox commands (Sprint B Batch 9):
    synapse sandbox create [--template T] [--persistent] [--timeout S]
    synapse sandbox list [--limit N] [--state S]
    synapse sandbox info <id>
    synapse sandbox run <id> <code>
    synapse sandbox kill <id>
    synapse auth [--api-key KEY]

Legacy commands (kept for backward compat):
    synapse execute <file.syn>
"""

import argparse
import json
import os
import sys


# ─── Config helpers ──────────────────────────────────────────────

_CONFIG_DIR = os.path.expanduser("~/.synapse")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")


def _load_config() -> dict:
    """Load ~/.synapse/config.json or return empty dict."""
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(cfg: dict) -> None:
    """Write config to ~/.synapse/config.json."""
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    with open(_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def _get_api_key() -> str:
    """Resolve API key: CLI flag > env var > config file."""
    key = os.environ.get("SYNAPSE_API_KEY", "")
    if not key:
        cfg = _load_config()
        key = cfg.get("api_key", "")
    return key


def _get_api_url() -> str:
    """Resolve API URL: env var > config file > default."""
    url = os.environ.get("SYNAPSE_API_URL", "")
    if not url:
        cfg = _load_config()
        url = cfg.get("api_url", "http://localhost:8002")
    return url


# ─── Auth command ────────────────────────────────────────────────

def cmd_auth(args):
    """Store API key and optional API URL in ~/.synapse/config.json."""
    cfg = _load_config()

    if args.api_key:
        cfg["api_key"] = args.api_key
    else:
        # Interactive prompt
        key = input("Enter your Synapse API key: ").strip()
        if not key:
            print("No key provided. Aborting.", file=sys.stderr)
            sys.exit(1)
        cfg["api_key"] = key

    if args.api_url:
        cfg["api_url"] = args.api_url

    _save_config(cfg)
    print(f"Credentials saved to {_CONFIG_FILE}")


# ─── Sandbox commands ────────────────────────────────────────────

def cmd_sandbox_create(args):
    """Create a new Cell sandbox."""
    from synapse.cell import Cell

    kwargs = {
        "api_key": _get_api_key(),
        "api_url": _get_api_url(),
        "template": args.template,
        "persistent": args.persistent,
        "timeout_ms": args.timeout * 1000,
    }

    try:
        cell = Cell(**kwargs)
        print(json.dumps({
            "cell_id": cell.cell_id,
            "template": cell.template,
            "persistent": cell.persistent,
        }, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_list(args):
    """List active sandboxes."""
    from synapse.cell import Cell

    try:
        paginator = Cell.list(
            limit=args.limit,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        items = paginator.items
        for item in items:
            print(f"  {item.sandbox_id}  {item.state.value:8s}  {item.template_id}")
        print(f"\n{len(items)} sandbox(es)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_info(args):
    """Get info for a specific sandbox."""
    from synapse.cell import Cell

    try:
        cell = Cell.connect(
            args.id,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        info = cell.get_info()
        print(json.dumps({
            "sandbox_id": info.sandbox_id,
            "template_id": info.template_id,
            "state": info.state.value,
            "started_at": str(info.started_at),
            "end_at": str(info.end_at),
            "metadata": info.metadata,
        }, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_run(args):
    """Run code in an existing sandbox."""
    from synapse.cell import Cell

    try:
        cell = Cell.connect(
            args.id,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        code = args.code
        # If code looks like a file path, read it
        if os.path.isfile(code):
            with open(code) as f:
                code = f.read()

        result = cell.run(code)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        sys.exit(result.exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_kill(args):
    """Kill (destroy) a sandbox."""
    from synapse.cell import Cell

    try:
        cell = Cell.connect(
            args.id,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        cell.kill()
        print(f"Killed: {args.id}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_pause(args):
    """Pause a running sandbox."""
    from synapse.cell import Cell

    try:
        cell = Cell.connect(
            args.id,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        snap_id = cell.pause()
        print(json.dumps({"status": "paused", "snapshot_id": snap_id}, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_resume(args):
    """Resume a paused sandbox."""
    from synapse.cell import Cell

    try:
        cell = Cell.connect(
            args.id,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        cell.resume()
        print(f"Resumed: {args.id}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sandbox_snapshot(args):
    """Create a snapshot of a running sandbox."""
    from synapse.cell import Cell

    try:
        cell = Cell.connect(
            args.id,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        snap_id = cell.create_snapshot()
        print(json.dumps({"snapshot_id": snap_id}, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_version(args):
    """Show SDK + gateway versions."""
    from synapse import __version__
    import http.client
    from urllib.parse import urlparse

    print(f"  SDK version: {__version__}")

    try:
        api_url = _get_api_url()
        parsed = urlparse(api_url.rstrip("/"))
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        conn = http.client.HTTPConnection(host, port, timeout=3)
        conn.request("GET", "/v1/health")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())
        conn.close()
        print(f"  Gateway:     {body.get('version', 'unknown')} ({body.get('status', '?')})")
        print(f"  Endpoint:    {api_url}")
    except Exception:
        print("  Gateway:     not reachable")


# ─── Legacy commands (backward compat) ───────────────────────────

def cmd_execute_syn(args):
    """Run a .syn file on the preview gateway."""
    try:
        from synapse.client import Synapse, SynapseError
    except ImportError:
        print("Error: synapse.client not available for .syn running", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.file, "r") as f:
            code = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    api_key = _get_api_key()
    base_url = os.environ.get("SYNAPSE_BASE_URL", "http://127.0.0.1:8000")
    client = Synapse(api_key=api_key, base_url=base_url)
    try:
        res = client.execute_syn(code)
    except SynapseError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Result:     {res.result}")
    print(f"Arena pos:  {res.arena_pos}")
    print(f"Latency:    {res.latency_ms}ms")
    if res.assertions:
        for i, a in enumerate(res.assertions):
            status = "PASS" if a.get("pass") else "FAIL"
            print(f"Assertion {i}: {status} (expected={a.get('expected')}, got={a.get('got')})")


# ─── Template commands (Sprint C Phase C1) ───────────────────────

def cmd_template_list(args):
    """List all registered templates."""
    from synapse.cell import Cell
    try:
        templates = Cell.list_templates(
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        for t in templates:
            compiled = "compiled" if t.get("compiled") else "pending"
            pkgs = ", ".join(t.get("packages", [])[:3])
            if len(t.get("packages", [])) > 3:
                pkgs += "..."
            print(f"  {t['name']:30s} {t.get('runtime',''):12s} {compiled:10s} {pkgs}")
        print(f"\n{len(templates)} template(s)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_template_info(args):
    """Show details for a specific template."""
    from synapse.cell import Cell
    try:
        t = Cell.get_template(
            args.name,
            api_url=_get_api_url(),
            api_key=_get_api_key(),
        )
        if t is None:
            print(f"Template not found: {args.name}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(t, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_template_build(args):
    """Build and register a Cell template from a cell.yaml file.
    
    This command replaces the legacy Dockerfile transpiler workflow 
    with a deterministic, .syn-native YAML builder utilizing prebaking.
    """
    from synapse.template import Template, TemplateError

    api_url = _get_api_url()
    api_key = _get_api_key()
    
    if args.dry_run:
        print("--dry-run not supported for Template build natively yet.", file=sys.stderr)
        return
        
    try:
        res = Template.build(path=args.path, api_url=api_url, api_key=api_key)
        print(f"Template '{res.get('name')}' registered successfully.")
        print(json.dumps(res, indent=2))
    except TemplateError as e:
        print(f"Template Error: {e}", file=sys.stderr)
        sys.exit(1)

def cmd_template_delete(args):
    """Delete a registered template and its prebaked artifacts."""
    from synapse.template import Template, TemplateError
    
    try:
        Template.delete(name=args.name, api_url=_get_api_url(), api_key=_get_api_key())
        print(f"Template '{args.name}' deleted.", file=sys.stderr)
    except TemplateError as e:
        print(f"Template Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_template_create(args):
    """Register a new template from a JSON spec."""
    import http.client
    from urllib.parse import urlparse

    spec = {"name": args.name, "runtime": args.runtime}
    if args.description:
        spec["description"] = args.description
    if args.packages:
        spec["packages"] = args.packages.split(",")

    api_url = _get_api_url()
    api_key = _get_api_key()
    parsed = urlparse(api_url.rstrip("/"))
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if parsed.scheme == "https":
        import ssl
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=30)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    conn.request("POST", "/v1/templates", json.dumps(spec), headers)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()

    if resp.status == 200:
        print(json.dumps(json.loads(body), indent=2))
    else:
        print(f"Error: {body}", file=sys.stderr)
        sys.exit(1)


# ─── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="synapse",
        description="Synapse CLI — sandbox management and .syn running",
    )
    subparsers = parser.add_subparsers(dest="command", help="available commands")

    # ── auth ──────────────────────────────────────────────────
    auth_parser = subparsers.add_parser("auth", help="configure API credentials")
    auth_parser.add_argument("--api-key", help="API key (or enter interactively)")
    auth_parser.add_argument("--api-url", help="API URL (default: production)")
    auth_parser.set_defaults(func=cmd_auth)

    # ── sandbox ───────────────────────────────────────────────
    sandbox_parser = subparsers.add_parser("sandbox", help="manage Cell sandboxes")
    sandbox_sub = sandbox_parser.add_subparsers(dest="sandbox_command")

    # sandbox create
    create_p = sandbox_sub.add_parser("create", help="create a new sandbox")
    create_p.add_argument("--template", default="python3", help="sandbox template")
    create_p.add_argument("--persistent", action="store_true", help="persistent session")
    create_p.add_argument("--timeout", type=int, default=3600, help="timeout in seconds")
    create_p.set_defaults(func=cmd_sandbox_create)

    # sandbox list
    list_p = sandbox_sub.add_parser("list", help="list active sandboxes")
    list_p.add_argument("--limit", type=int, default=50, help="max results")
    list_p.add_argument("--state", default="running,paused", help="filter by state")
    list_p.set_defaults(func=cmd_sandbox_list)

    # sandbox info
    info_p = sandbox_sub.add_parser("info", help="get sandbox info")
    info_p.add_argument("id", help="sandbox ID")
    info_p.set_defaults(func=cmd_sandbox_info)

    # sandbox run
    run_p = sandbox_sub.add_parser("run", help="run code in a sandbox")
    run_p.add_argument("id", help="sandbox ID")
    run_p.add_argument("code", help="Python code (or path to .py file)")
    run_p.set_defaults(func=cmd_sandbox_run)

    # sandbox kill
    kill_p = sandbox_sub.add_parser("kill", help="kill a sandbox")
    kill_p.add_argument("id", help="sandbox ID")
    kill_p.set_defaults(func=cmd_sandbox_kill)

    # sandbox pause
    pause_p = sandbox_sub.add_parser("pause", help="pause a running sandbox")
    pause_p.add_argument("id", help="sandbox ID")
    pause_p.set_defaults(func=cmd_sandbox_pause)

    # sandbox resume
    resume_p = sandbox_sub.add_parser("resume", help="resume a paused sandbox")
    resume_p.add_argument("id", help="sandbox ID")
    resume_p.set_defaults(func=cmd_sandbox_resume)

    # sandbox snapshot
    snap_p = sandbox_sub.add_parser("snapshot", help="create a snapshot")
    snap_p.add_argument("id", help="sandbox ID")
    snap_p.set_defaults(func=cmd_sandbox_snapshot)

    # sandbox exec (alias for run)
    exec_p = sandbox_sub.add_parser("exec", help="execute code in a sandbox (alias for run)")
    exec_p.add_argument("id", help="sandbox ID")
    exec_p.add_argument("code", help="Python code (or path to .py file)")
    exec_p.set_defaults(func=cmd_sandbox_run)

    # ── template ───────────────────────────────────────────────
    template_parser = subparsers.add_parser("template", help="manage Wasm-native templates")
    template_sub = template_parser.add_subparsers(dest="template_command")

    # template list
    tpl_list = template_sub.add_parser("list", help="list registered templates")
    tpl_list.set_defaults(func=cmd_template_list)

    # template info
    tpl_info = template_sub.add_parser("info", help="show template details")
    tpl_info.add_argument("name", help="template name")
    tpl_info.set_defaults(func=cmd_template_info)

    # template create
    tpl_create = template_sub.add_parser("create", help="register a new template")
    tpl_create.add_argument("name", help="template name")
    tpl_create.add_argument("--runtime", default="python3", help="base runtime")
    tpl_create.add_argument("--description", help="template description")
    tpl_create.add_argument("--packages", help="comma-separated package list")
    tpl_create.set_defaults(func=cmd_template_create)

    # template build 
    tpl_build = template_sub.add_parser(
        "build",
        help="build and prebake a template from a cell.yaml file"
    )
    tpl_build.add_argument("-p", "--path", default=".", help="path to directory containing cell.yaml")
    tpl_build.add_argument("--dry-run", action="store_true",
                           help="dry-run is not supported for native template builds")
    tpl_build.set_defaults(func=cmd_template_build)

    # template delete
    tpl_delete = template_sub.add_parser("delete", help="delete a template and its rootfs")
    tpl_delete.add_argument("name", help="template name")
    tpl_delete.set_defaults(func=cmd_template_delete)

    # ── execute (legacy) ──────────────────────────────────────
    exec_parser = subparsers.add_parser("execute", help="run a .syn file (legacy)")
    exec_parser.add_argument("file", help="path to .syn file")
    exec_parser.set_defaults(func=cmd_execute_syn)

    # ── version ──────────────────────────────────────────
    version_parser = subparsers.add_parser("version", help="show SDK + gateway versions")
    version_parser.set_defaults(func=cmd_version)

    # ── parse + dispatch ──────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "sandbox" and not getattr(args, "sandbox_command", None):
        sandbox_parser.print_help()
        sys.exit(1)

    if args.command == "template" and not getattr(args, "template_command", None):
        template_parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
