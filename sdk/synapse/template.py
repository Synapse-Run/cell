"""
Templates Subsystem v1

Provides E2B-compatible `Template` builder class for managing .syn-native YAML 
templates and prebaking packages into the gateway rootfs mounts.
"""
import os
import re
import yaml
import json
import subprocess
import shutil
import urllib.request
import urllib.error
from typing import Dict, Optional, Any

class TemplateError(Exception):
    pass

class Template:
    """E2B-compatible Template builder and manager.
    
    Provides ergonomic APIs for defining, building, listing, and 
    deleting .syn-native YAML templates.
    """
    
    @classmethod
    def _gateway_request(cls, method: str, path: str, data: Optional[Dict[str, Any]] = None, api_key: Optional[str] = None, api_url: str = "http://127.0.0.1:8001") -> Any:
        # Strip trailing slashes
        api_url = api_url.rstrip("/")
        url = f"{api_url}{path}"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        req_data = None
        if data is not None:
            req_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
            
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                raise TemplateError(err_json.get("error", err_body))
            except json.JSONDecodeError:
                raise TemplateError(err_body)
        except urllib.error.URLError as e:
            raise TemplateError(f"Gateway connection error: {e}")

    @classmethod
    def build(cls, path: str = ".", api_url: str = "http://127.0.0.1:8001", api_key: Optional[str] = None):
        """Build and register a template from a cell.yaml.
        
        This translates to picking up the cell.yaml, executing local pip installs for 
        pre-baking packages into the target rootfs, and then registering the TemplateInfo 
        with the Gateway API.
        """
        yaml_path = os.path.join(path, "cell.yaml")
        if not os.path.exists(yaml_path):
            yaml_path = os.path.join(path, ".cell.yaml")
            if not os.path.exists(yaml_path):
                raise TemplateError(f"No cell.yaml found in {os.path.abspath(path)}")
        
        with open(yaml_path, "r", encoding="utf-8") as f:
            try:
                spec = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise TemplateError(f"YAML parsing error: {e}")
                
        name = spec.get("name")
        if not name:
            raise TemplateError("Template 'name' is required.")
            
        # Defense: Template name collision / format validation
        if not isinstance(name, str) or not re.match(r'^[a-zA-Z0-9_-]{1,64}$', name):
            raise TemplateError("Invalid template name. Use 1-64 alphanumeric characters, dashes, or underscores.")
            
        packages = spec.get("packages", [])
        
        # Defense: Packages field hardening
        for pkg in packages:
            # Rejects explicit URLs, git+, editable, and path traversals
            if not isinstance(pkg, str) or re.search(r'[\/\\]|\.\.|(?:^|\s)(-e|--editable)\b|git\+|https?:', pkg):
                raise TemplateError(f"Invalid package specifier '{pkg}': only standard PyPI names/versions are allowed.")
                
        # Defense: Files field hardening (lexical check similarly to JC-010)
        files = spec.get("files", [])
        for f in files:
            if not isinstance(f, str) or ".." in f.split(os.sep) or ".." in f.split("/"):
                raise TemplateError(f"Path traversal detected in file spec: {f}")
                
        # Resolve templates root exactly as backend: cells_root.parent / "templates"
        cells_dir = os.environ.get("CELL_DATA_DIR", "/tmp/synapse-cells")
        templates_root = os.path.join(os.path.dirname(cells_dir), "templates")
        
        # Security: ensure resolved path is sound
        resolved_templates_root = os.path.realpath(templates_root)
        rootfs_dir = os.path.realpath(os.path.join(resolved_templates_root, "rootfs", name))
        
        if not rootfs_dir.startswith(resolved_templates_root + os.sep):
             raise TemplateError("Path traversal detected resolving rootfs.")
             
        os.makedirs(rootfs_dir, exist_ok=True)
        
        if packages:
            print(f"[.cell] Prebaking packages for '{name}': {', '.join(packages)}")
            # Install packages into rootfs_dir
            cmd = ["python3", "-m", "pip", "install", "--target", rootfs_dir] + packages
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                raise TemplateError(f"Failed to prebake packages: {e.stderr.decode('utf-8')}")
        else:
            print(f"[.cell] No packages to prebake for '{name}'")
            
        # Register via the gateway
        print(f"[.cell] Registering template '{name}'...")
        res = cls._gateway_request("POST", "/v1/templates", data=spec, api_url=api_url, api_key=api_key)
        return res
        
    @classmethod
    def list(cls, api_url: str = "http://127.0.0.1:8001", api_key: Optional[str] = None):
        return cls._gateway_request("GET", "/v1/templates", api_url=api_url, api_key=api_key)
        
    @classmethod
    def delete(cls, name: str, api_url: str = "http://127.0.0.1:8001", api_key: Optional[str] = None):
        if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', name):
            raise TemplateError("Invalid template name.")
            
        cells_dir = os.environ.get("CELL_DATA_DIR", "/tmp/synapse-cells")
        templates_root = os.path.join(os.path.dirname(cells_dir), "templates")
        
        rootfs_dir = os.path.realpath(os.path.join(templates_root, "rootfs", name))
        if os.path.isdir(rootfs_dir) and rootfs_dir.startswith(os.path.realpath(templates_root) + os.sep):
            shutil.rmtree(rootfs_dir)
            
        return cls._gateway_request("DELETE", f"/v1/templates/{name}", api_url=api_url, api_key=api_key)
