"""Qualify real clients against an HTTPS registry fixture, using no Datadog secret.

Run with: python3 tests/registry_integration.py npm latest
Client installation downloads from the public npm/PyPI registry. Package resolution
then uses only the local fixture, including an authenticated gate and public redirect.
"""

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SAT = "ddsat_fixture_not_a_real_credential"
BASE = "/api/v2/scfw/registry"


def artifacts():
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode="w:gz") as archive:
        data = json.dumps({"name": "scfw-fixture", "version": "1.0.0"}).encode()
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("scfw_fixture.py", "fixture = True\n")
        prefix = "scfw_fixture-1.0.0.dist-info/"
        archive.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: scfw-fixture\nVersion: 1.0.0\n")
        archive.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nGenerator: scfw-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(prefix + "RECORD", "")
    return {"scfw-fixture-1.0.0.tgz": package.getvalue(),
            "scfw_fixture-1.0.0-py3-none-any.whl": wheel.getvalue()}


def main(manager, version):
    with tempfile.TemporaryDirectory(prefix="scfw-registry-integration-") as temporary:
        directory = Path(temporary)
        cert = directory / "cert.pem"
        key = directory / "key.pem"
        cert_config = directory / "cert.conf"
        cert_config.write_text("[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n"
                               "[server]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
                               "basicConstraints=critical,CA:FALSE\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
                        "-config", str(cert_config), "-extensions", "server"],
                       check=True, capture_output=True)
        files = artifacts()
        requests = []
        failures = []
        block = False

        class Registry(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                auth = self.headers.get("Authorization")
                requests.append((self.path, auth))
                if self.path.startswith("/public/"):
                    if auth:
                        failures.append("Credential sent to public artifact origin")
                    name = self.path.rsplit("/", 1)[-1]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(files[name])))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(files[name])
                    return
                basic = "Basic " + base64.b64encode(f"__token__:{SAT}".encode()).decode()
                expected = basic if "/pypi-proxy/" in self.path else f"Bearer {SAT}"
                if auth != expected:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="scfw"')
                    self.end_headers()
                    return
                if self.path.endswith(".tgz") or self.path.endswith(".whl"):
                    self.send_response(403 if block else 307)
                    if not block:
                        self.send_header("Location", f"https://127.0.0.1:{self.server.server_port}/public/{self.path.rsplit('/', 1)[-1]}")
                    self.end_headers()
                    return
                if self.path == f"{BASE}/npm/scfw-fixture":
                    data = json.dumps({
                        "name": "scfw-fixture", "dist-tags": {"latest": "1.0.0"},
                        "time": {"created": "2020-01-01T00:00:00.000Z", "modified": "2020-01-01T00:00:00.000Z",
                                 "1.0.0": "2020-01-01T00:00:00.000Z"},
                        "versions": {"1.0.0": {"name": "scfw-fixture", "version": "1.0.0",
                            "dist": {"tarball": f"{origin}{BASE}/npm/scfw-fixture/-/scfw-fixture-1.0.0.tgz",
                                     "shasum": hashlib.sha1(files["scfw-fixture-1.0.0.tgz"]).hexdigest()}}},
                    }).encode()
                    content_type = "application/json"
                elif self.path == f"{BASE}/pypi-proxy/simple/scfw-fixture/":
                    data = (f'<a href="{origin}{BASE}/pypi-proxy/files/scfw_fixture-1.0.0-py3-none-any.whl">'
                            'scfw_fixture-1.0.0-py3-none-any.whl</a>').encode()
                    content_type = "text/html"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        origin = f"https://localhost:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            project = directory / "project"
            project.mkdir()
            (project / "package.json").write_text('{"name":"fixture-project","private":true}\n')
            (project / "pyproject.toml").write_text(
                '[project]\nname = "fixture-project"\nversion = "1.0.0"\n'
                'requires-python = ">=3.11"\ndependencies = ["scfw-fixture==1.0.0"]\n')
            env = {**os.environ, "INPUT_PACKAGE_MANAGER": manager, "INPUT_VERSION": version,
                   "INPUT_SAT": SAT, "INPUT_DD_SITE": origin, "RUNNER_TEMP": str(directory),
                   "INPUT_WORKING_DIRECTORY": str(project)}
            for name in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT"):
                env[name] = str(directory / name)
                Path(env[name]).touch()
            subprocess.run([sys.executable, str(ROOT / "registry/setup.py")], env=env, check=True)
            for line in Path(env["GITHUB_ENV"]).read_text().splitlines():
                name, value = line.split("=", 1)
                env[name] = value
            env["PATH"] = os.pathsep.join([*reversed(Path(env["GITHUB_PATH"]).read_text().splitlines()), env["PATH"]])
            env.update(NODE_EXTRA_CA_CERTS=str(cert), PIP_CERT=str(cert), SSL_CERT_FILE=str(cert),
                       REQUESTS_CA_BUNDLE=str(cert), UV_NATIVE_TLS="true", PIP_DISABLE_PIP_VERSION_CHECK="1",
                       YARN_HTTPS_CA_FILE_PATH=str(cert), YARN_ENABLE_TELEMETRY="0",
                       YARN_ENABLE_IMMUTABLE_INSTALLS="false", YARN_ENABLE_GLOBAL_CACHE="false", YARN_ENABLE_MIRROR="false",
                       YARN_CACHE_FOLDER=str(directory / "yarn-cache"),
                       POETRY_VIRTUALENVS_IN_PROJECT="true", POETRY_CACHE_DIR=str(directory / "poetry-cache"))
            commands = {
                "npm": ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", "--cache", str(directory / "npm-cache"), "scfw-fixture@1.0.0"],
                "pnpm": ["pnpm", "add", "--ignore-scripts", "--store-dir", str(directory / "pnpm-store"), "scfw-fixture@1.0.0"],
                "yarn": ["yarn", "add", "scfw-fixture@1.0.0"],
                "bun": ["bun", "add", "--ignore-scripts", "--cache-dir", str(directory / "bun-cache"), "scfw-fixture@1.0.0"],
                "pip": ["pip", "install", "--no-cache-dir", "scfw-fixture==1.0.0"],
                "uv": ["uv", "sync", "--no-cache"],
                "poetry": ["poetry", "install", "--no-root", "--no-interaction"],
            }
            result = subprocess.run(commands[manager], env=env, cwd=project, text=True, capture_output=True, timeout=180)
            if result.returncode:
                raise AssertionError(f"Client install failed:\n{result.stdout}\n{result.stderr}".replace(SAT, "[redacted]"))
            assert any(path.startswith("/public/") for path, _ in requests), requests
            assert not failures, failures
            print(f"PASS: {manager} {version} authenticated at the gate and followed the public redirect without credentials")

            # A fresh project/cache prevents an already installed artifact bypassing the gate.
            block = True
            requests.clear()
            blocked_project = directory / "blocked-project"
            blocked_project.mkdir()
            (blocked_project / "package.json").write_text('{"name":"blocked-project","private":true}\n')
            (blocked_project / "pyproject.toml").write_text((project / "pyproject.toml").read_text())
            if (project / ".yarnrc.yml").exists():
                (blocked_project / ".yarnrc.yml").write_text((project / ".yarnrc.yml").read_text())
            blocked_env = {**env, "YARN_CACHE_FOLDER": str(directory / "blocked-yarn-cache"),
                           "POETRY_CACHE_DIR": str(directory / "blocked-poetry-cache")}
            command = [arg.replace(str(directory / "npm-cache"), str(directory / "blocked-npm-cache"))
                       .replace(str(directory / "pnpm-store"), str(directory / "blocked-pnpm-store"))
                       .replace(str(directory / "bun-cache"), str(directory / "blocked-bun-cache"))
                       for arg in commands[manager]]
            if manager == "pip":
                command.extend(["--force-reinstall"])
            result = subprocess.run(command, env=blocked_env, cwd=blocked_project, text=True, capture_output=True, timeout=180)
            assert result.returncode != 0, "Client succeeded after a BLOCK verdict"
            assert any(path.endswith((".tgz", ".whl")) for path, _ in requests), requests
            assert not any(path.startswith("/public/") for path, _ in requests), requests
            print(f"PASS: {manager} {version} failed installation after the gate blocked the artifact")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main(*sys.argv[1:])
