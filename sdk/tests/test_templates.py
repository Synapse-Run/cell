"""
Templates Subsystem v1 Regression Tests

Security hardening & lifecycle tests for the .syn-native Template builder.
"""
import os
import tempfile
import yaml
import pytest
from synapse.template import Template, TemplateError

def test_template_name_collision_rejected():
    # Names with invalid characters should be rejected immediately
    with tempfile.TemporaryDirectory() as d:
        spec = {"name": "invalid/name", "runtime": "python3"}
        with open(os.path.join(d, "cell.yaml"), "w") as f:
            yaml.dump(spec, f)
            
        with pytest.raises(TemplateError, match="Invalid template name"):
            Template.build(path=d)

def test_template_packages_malicious_rejected():
    # Editable, paths, and URLs should be rejected
    malicious = [
        "-e .",
        "--editable git+https://evil.com/pkg",
        "requests @ git+https://github.com/psf/requests.git",
        "../../etc/passwd",
        "/tmp/some-package.whl",
        "http://evil.com/malware.whl",
        "https://evil.com/malware.whl"
    ]
    
    with tempfile.TemporaryDirectory() as d:
        for m in malicious:
            spec = {"name": "test-pkg", "runtime": "python3", "packages": [m]}
            with open(os.path.join(d, "cell.yaml"), "w") as f:
                yaml.dump(spec, f)
                
            with pytest.raises(TemplateError, match=r"Invalid package specifier"):
                Template.build(path=d)

def test_template_files_path_traversal_rejected():
    # Path traversal in files block
    with tempfile.TemporaryDirectory() as d:
        spec = {"name": "test-files", "runtime": "python3", "files": ["../../../etc/passwd"]}
        with open(os.path.join(d, "cell.yaml"), "w") as f:
            yaml.dump(spec, f)
            
        with pytest.raises(TemplateError, match="Path traversal detected"):
            Template.build(path=d)

def test_template_lifecycle_happy_path():
    # Assume gateway is running (Military Audit runs with real gateway)
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:8001/v1/templates", timeout=1)
    except Exception:
        pytest.skip("Gateway not running, skipping lifecycle test.")
        
    with tempfile.TemporaryDirectory() as d:
        spec = {"name": "test-happy-path", "runtime": "python3", "packages": []}
        with open(os.path.join(d, "cell.yaml"), "w") as f:
            yaml.dump(spec, f)
            
        res = Template.build(path=d)
        assert res.get("name") == "test-happy-path"
        
        # List correctly surfaces it
        ts = Template.list()
        assert any(t.get("name") == "test-happy-path" for t in ts)
        
        # Cleanup
        Template.delete("test-happy-path")
        
        # List shouldn't contain it
        ts = Template.list()
        assert not any(t.get("name") == "test-happy-path" for t in ts)
