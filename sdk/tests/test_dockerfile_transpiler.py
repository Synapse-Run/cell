"""Tests for the Dockerfile -> .celltemplate transpiler.

Run: python3 -m pytest cell/sdk/tests/test_dockerfile_transpiler.py -v
Or:  python3 cell/sdk/tests/test_dockerfile_transpiler.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synapse.dockerfile_transpiler import (
    transpile_dockerfile,
    transpile_dockerfile_file,
    TranspileError,
    Warning as TWarning,
)


class TestHappyPath(unittest.TestCase):
    """Fixture 1: Simple FROM + pip install + COPY + CMD."""

    def test_simple_python_agent(self):
        src = """
FROM python:3.12-slim
RUN pip install requests httpx pydantic
COPY app.py /app/app.py
WORKDIR /app
ENV APP_MODE=production
CMD ["python", "app.py"]
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertEqual(spec["runtime"], "python3")
        self.assertEqual(sorted(spec["packages"]), ["httpx", "pydantic", "requests"])
        self.assertEqual(spec["files"], [{"src": "app.py", "dest": "/app/app.py"}])
        self.assertEqual(spec["working_directory"], "/app")
        self.assertEqual(spec["envs"], {"APP_MODE": "production"})
        self.assertEqual(spec["start_command"], "python app.py")
        self.assertEqual(len(warnings), 0)


class TestMultilinePipInstall(unittest.TestCase):
    """Fixture 2: Multi-line RUN pip install with backslash continuations."""

    def test_multiline_pip(self):
        src = """FROM python:3.11
RUN pip install \\
        langchain==0.3.0 \\
        openai>=1.0 \\
        pydantic[email] \\
        fastapi
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertIn("langchain==0.3.0", spec["packages"])
        self.assertIn("openai>=1.0", spec["packages"])
        self.assertIn("pydantic[email]", spec["packages"])
        self.assertIn("fastapi", spec["packages"])


class TestRequirementsFile(unittest.TestCase):
    """Fixture 3: RUN pip install -r requirements.txt reads the file."""

    def test_requirements_file_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            req_path = os.path.join(tmp, "requirements.txt")
            with open(req_path, "w") as f:
                f.write("requests==2.31.0\nhttpx\n# comment\n\ntqdm>=4.0\n")

            docker_path = os.path.join(tmp, "Dockerfile")
            with open(docker_path, "w") as f:
                f.write("FROM python:3.12\nRUN pip install -r requirements.txt\n")

            spec, warnings = transpile_dockerfile_file(docker_path)
            self.assertIn("requests==2.31.0", spec["packages"])
            self.assertIn("httpx", spec["packages"])
            self.assertIn("tqdm>=4.0", spec["packages"])


class TestNodeJS(unittest.TestCase):
    """Fixture 4: FROM node + npm install -> runtime=javascript."""

    def test_node_app(self):
        src = """
FROM node:18-alpine
RUN npm install express fastify
COPY . /app
WORKDIR /app
CMD ["node", "index.js"]
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertEqual(spec["runtime"], "javascript")
        self.assertEqual(sorted(spec["packages"]), ["express", "fastify"])
        self.assertEqual(spec["start_command"], "node index.js")


class TestAptInstallWarnings(unittest.TestCase):
    """Fixture 5+6: apt-get install git -> warning with migration hint."""

    def test_apt_git_warning(self):
        src = """
FROM python:3.11
RUN apt-get update && apt-get install -y git
RUN pip install requests
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertTrue(any("git" in w.message and "cell.git" in w.migration_hint for w in warnings))
        # Packages from pip install should still be captured
        self.assertIn("requests", spec.get("packages", []))

    def test_apt_ffmpeg_warning(self):
        src = """
FROM python:3.12
RUN apt-get install -y ffmpeg
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertTrue(any("ffmpeg" in w.message for w in warnings))
        self.assertTrue(any("enterprise" in w.migration_hint for w in warnings))


class TestCustomBaseImageError(unittest.TestCase):
    """Fixture 7: FROM tensorflow/tensorflow -> TranspileError."""

    def test_tensorflow_image_rejected(self):
        src = "FROM tensorflow/tensorflow:2.15.0\nCMD python"
        with self.assertRaises(TranspileError) as ctx:
            transpile_dockerfile(src)
        self.assertIn("custom base image", str(ctx.exception).lower())
        self.assertTrue(ctx.exception.migration_hint)

    def test_ubuntu_image_rejected(self):
        src = "FROM ubuntu:22.04\nRUN apt-get update"
        with self.assertRaises(TranspileError):
            transpile_dockerfile(src)


class TestMultiStageBuild(unittest.TestCase):
    """Fixture 8: Multi-stage build -> flatten + warn."""

    def test_multistage_flattens(self):
        src = """
FROM python:3.11 AS builder
RUN pip install wheel
FROM python:3.11
RUN pip install requests
COPY --from=builder /app /app
"""
        spec, warnings = transpile_dockerfile(src)
        # Should flatten: runtime=python3, packages merged from both stages
        self.assertEqual(spec["runtime"], "python3")
        self.assertIn("requests", spec["packages"])
        # Should warn about multi-stage
        self.assertTrue(any("multi-stage" in w.message.lower() for w in warnings))


class TestExposeWarning(unittest.TestCase):
    """Fixture 9: EXPOSE port -> warning."""

    def test_expose_warns(self):
        src = """
FROM python:3.12
EXPOSE 8080
CMD ["python", "-m", "http.server"]
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertTrue(any("EXPOSE" in w.message for w in warnings))


class TestEnvWorkdirUserLabel(unittest.TestCase):
    """Fixture 10: ENV, WORKDIR, USER, LABEL mapping."""

    def test_all_metadata_directives(self):
        src = """
FROM python:3.12
LABEL maintainer=mike@freshfield.ai
LABEL version=0.1.0
ENV PYTHONPATH=/custom API_KEY=secret
WORKDIR /srv
USER appuser
CMD ["python"]
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertEqual(spec["envs"]["PYTHONPATH"], "/custom")
        self.assertEqual(spec["envs"]["API_KEY"], "secret")
        self.assertEqual(spec["working_directory"], "/srv")
        self.assertEqual(spec["user"], "appuser")
        self.assertEqual(spec["metadata"]["maintainer"], "mike@freshfield.ai")
        self.assertEqual(spec["metadata"]["version"], "0.1.0")


class TestCommentsAndBlankLines(unittest.TestCase):
    """Parser edge case: comments and blank lines ignored."""

    def test_comments_stripped(self):
        src = """
# This is a comment
FROM python:3.12

# Another comment
RUN pip install requests  # inline comment (treated as part of args)

CMD ["python"]
"""
        spec, warnings = transpile_dockerfile(src)
        # requests should be captured; inline comment likely ends up as a pkg
        # (that's fine — pip itself would reject it). Our job is just the mapping.
        self.assertIn("requests", spec["packages"])


class TestEmptyDockerfile(unittest.TestCase):
    """Empty Dockerfile -> TranspileError."""

    def test_empty_file(self):
        with self.assertRaises(TranspileError):
            transpile_dockerfile("")

    def test_only_comments(self):
        with self.assertRaises(TranspileError):
            transpile_dockerfile("# just a comment\n# another\n")


class TestEntrypointCmdMerge(unittest.TestCase):
    """ENTRYPOINT + CMD merge into start_command."""

    def test_entrypoint_plus_cmd(self):
        src = """
FROM python:3.12
ENTRYPOINT ["python", "-u"]
CMD ["app.py"]
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertIn("python", spec["start_command"])
        self.assertIn("-u", spec["start_command"])
        self.assertIn("app.py", spec["start_command"])


class TestDryRunEndToEnd(unittest.TestCase):
    """End-to-end: real-world-style Dockerfile produces a valid TemplateInfo JSON."""

    def test_langchain_style_dockerfile(self):
        src = """
# LangChain agent, production
FROM python:3.12-slim
LABEL app=chat-agent version=1.2.3
RUN pip install --no-cache-dir \\
        langchain-core>=0.3.0 \\
        langchain-openai \\
        openai \\
        fastapi \\
        uvicorn[standard] \\
        python-dotenv
COPY agent/ /app/agent/
COPY main.py /app/main.py
WORKDIR /app
ENV OPENAI_API_KEY=placeholder LOG_LEVEL=info
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
        spec, warnings = transpile_dockerfile(src)
        self.assertEqual(spec["runtime"], "python3")
        self.assertIn("langchain-core>=0.3.0", spec["packages"])
        self.assertIn("langchain-openai", spec["packages"])
        self.assertIn("openai", spec["packages"])
        self.assertIn("fastapi", spec["packages"])
        self.assertIn("uvicorn[standard]", spec["packages"])
        self.assertEqual(spec["working_directory"], "/app")
        self.assertEqual(spec["envs"]["LOG_LEVEL"], "info")
        self.assertEqual(spec["metadata"]["app"], "chat-agent")
        self.assertIn("uvicorn", spec["start_command"])
        # EXPOSE should warn
        self.assertTrue(any("EXPOSE" in w.message for w in warnings))

        # Produces a spec round-trippable through JSON (REST wire format)
        import json
        spec_json = json.dumps(spec)
        reloaded = json.loads(spec_json)
        self.assertEqual(reloaded["runtime"], "python3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
