"""Install and configure a registry client independently of the SCFW CLI action."""

import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import quote, urlsplit


MANAGERS = {"npm", "pnpm", "yarn", "bun", "pip", "uv", "poetry"}
JS_MANAGERS = {"npm", "pnpm", "yarn", "bun"}


def mask(value):
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::add-mask::{escaped}", flush=True)


def append_values(file, values):
    with Path(file).open("a") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("Action environment values must be single-line")
            stream.write(f"{key}={value}\n")


def registry_origin(site):
    value = site if "://" in site else f"https://{site}"
    parsed = urlsplit(value)
    # Hostnames and optional ports also allow enterprise/test ingress hosts.
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parsed.netloc)):
        raise ValueError("dd_site must be an HTTPS application hostname without credentials or a path")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("dd_site has an invalid port")
    return f"https://{parsed.netloc.lower()}"


def bootstrap_environment():
    # Never give the SAT to package-manager installers or their lifecycle scripts.
    prefixes = ("INPUT_", "SCFW_", "NPM_CONFIG_", "PNPM_", "YARN_", "BUN_", "PIP_", "UV_", "POETRY_")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(prefixes)}
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def run(command, *, env, cwd):
    # Do not echo configuration commands/results: clients may expand credentials.
    result = subprocess.run(command, env=env, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} {command[1]} failed (exit {result.returncode})")
    return result.stdout.strip()


def install(manager, version, directory, env):
    if manager in JS_MANAGERS:
        package = manager
        if manager == "yarn":
            package = "@yarnpkg/cli-dist"
        empty = directory / "empty.npmrc"
        empty.touch(mode=0o600)
        # npm disallows using the same file for userconfig and globalconfig.
        global_config = directory / "global.npmrc"
        global_config.touch(mode=0o600)
        command = ["npm", "install", "--global", "--prefix", str(directory / "client"),
                   "--registry=https://registry.npmjs.org/", "--userconfig", str(empty),
                   "--globalconfig", str(global_config), "--no-audit", "--no-fund"]
        # Bun and native pnpm distributions use postinstall to install their binaries.
        if manager not in ("bun", "pnpm"):
            command.append("--ignore-scripts")
        run([*command, f"{package}@{version}"], env=env, cwd=directory)
        bin_directory = directory / "client" / "bin"
    else:
        venv = directory / "client"
        run([sys.executable, "-m", "venv", str(venv)], env=env, cwd=directory)
        bin_directory = venv / "bin"
        requirement = manager if version == "latest" else f"{manager}=={version}"
        run([str(bin_directory / "python"), "-m", "pip", "--isolated", "install",
             "--disable-pip-version-check", "--no-input", "--index-url", "https://pypi.org/simple/",
             "--upgrade", requirement], env=env, cwd=directory)
    installed = run([str(bin_directory / manager), "--version"], env=env, cwd=directory)
    if "\n" in installed or "\r" in installed:
        raise RuntimeError("Unexpected multi-line package-manager version")
    return bin_directory, installed


def configure(manager, origin, sat, directory, project, executable, env):
    base = f"{origin}/api/v2/scfw/registry"
    npm_url = f"{base}/npm/"
    python_url = f"{base}/pypi-proxy/simple/"
    values = {}
    if manager in JS_MANAGERS:
        config = directory / "npmrc"
        config.write_text(f"registry={npm_url}\n"
                          f"{npm_url.removeprefix('https:')}:_authToken=${{SCFW_REGISTRY_SAT}}\n")
        config.chmod(0o600)
        values.update(SCFW_REGISTRY_SAT=sat, NPM_CONFIG_USERCONFIG=str(config),
                      NPM_CONFIG_REGISTRY=npm_url)
        if manager == "yarn":
            # Yarn Berry needs its own per-registry map. Keep existing project settings.
            child_env = {**env, **values, "YARN_IGNORE_PATH": "1"}
            scoped = json.dumps(npm_url.removeprefix("https:").rstrip("/"))
            for key, value in (
                ("npmRegistryServer", npm_url.rstrip("/")),
                (f"npmRegistries[{scoped}].npmAlwaysAuth", "true"),
                (f"npmRegistries[{scoped}].npmAuthToken", "${SCFW_REGISTRY_SAT}"),
            ):
                run([str(executable), "config", "set", key, value], env=child_env, cwd=project)
            values["YARN_NPM_REGISTRY_SERVER"] = npm_url.rstrip("/")
            values["YARN_IGNORE_PATH"] = "1"
        return values, npm_url

    encoded = quote(sat, safe="")
    authenticated_url = python_url.replace("https://", f"https://__token__:{encoded}@", 1)
    mask(encoded)
    mask(authenticated_url)
    if manager == "pip":
        values.update(PIP_INDEX_URL=authenticated_url, PIP_EXTRA_INDEX_URL="",
                      PIP_CONFIG_FILE=os.devnull)
    elif manager == "uv":
        values.update(UV_DEFAULT_INDEX=authenticated_url, UV_INDEX_URL=authenticated_url,
                      UV_EXTRA_INDEX_URL="", UV_INDEX="")
    else:
        # Poetry's publishing repository setting does not affect dependency resolution.
        # source add updates pyproject.toml while retaining unrelated project settings.
        run([str(executable), "source", "add", "--priority=primary", "scfw", python_url],
            env=env, cwd=project)
        values.update(POETRY_HTTP_BASIC_SCFW_USERNAME="__token__",
                      POETRY_HTTP_BASIC_SCFW_PASSWORD=sat)
    return values, python_url


def main():
    manager = os.environ.get("INPUT_PACKAGE_MANAGER", "").strip()
    version = os.environ.get("INPUT_VERSION", "latest").strip()
    sat = os.environ.get("INPUT_SAT", "")
    if sat:
        mask(sat)
    if platform.system() not in ("Linux", "Darwin"):
        raise ValueError("The registry action supports Linux and macOS runners")
    if manager not in MANAGERS:
        raise ValueError("package-manager must be npm, pnpm, yarn, bun, pip, uv, or poetry")
    if version != "latest" and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.]?[A-Za-z0-9.-]+)?", version):
        raise ValueError("version must be latest or an explicit package-manager version")
    if manager == "yarn" and version.startswith(("0.", "1.")):
        raise ValueError("Yarn Classic is not supported: it forwards credentials on 307 redirects; use Yarn Berry")
    if not sat or any(char.isspace() or ord(char) < 32 for char in sat):
        raise ValueError("sat must be a nonempty, single-line token without whitespace")
    origin = registry_origin(os.environ.get("INPUT_DD_SITE", "app.datadoghq.com").strip())
    project = Path(os.environ.get("INPUT_WORKING_DIRECTORY", ".")).resolve()
    if not project.is_dir() or any(char in str(project) for char in "\r\n"):
        raise ValueError("working-directory must name an existing project directory")
    if manager == "poetry":
        with (project / "pyproject.toml").open("rb") as stream:
            sources = tomllib.load(stream).get("tool", {}).get("poetry", {}).get("source", [])
        if any(source.get("name") != "scfw" for source in sources):
            raise ValueError("Reconcile existing Poetry sources before selecting the SCFW registry")
    # Temporary files are never written to the checkout (except native project settings).
    directory = Path(tempfile.mkdtemp(prefix="scfw-registry-", dir=os.environ["RUNNER_TEMP"]))
    env = bootstrap_environment()
    bin_directory, installed = install(manager, version, directory, env)
    values, url = configure(manager, origin, sat, directory, project,
                            bin_directory / manager, env)
    append_values(os.environ["GITHUB_ENV"], values)
    with Path(os.environ["GITHUB_PATH"]).open("a") as stream:
        stream.write(f"{bin_directory}\n")
    append_values(os.environ["GITHUB_OUTPUT"], {
        "package-manager-version": installed, "registry-url": url,
    })
    print(f"Configured {manager} ({installed}) to use {url}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        # Avoid traceback and command output that could include expanded credentials.
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
