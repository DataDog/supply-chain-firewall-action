"""Exercise setup and generated wrappers without downloading unreleased SCFW builds."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scfw test ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.env = os.environ.copy()
        self.env.pop("SCFW_PROXY_ENABLED", None)
        self.env.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(self.directory / "runner"),
            "GITHUB_ACTION_PATH": str(ROOT),
            "SCFW_VERSION": "4.2.0",
            "SCFW_PACKAGE_MANAGERS": "uv",
            "SCFW_ERROR_ON_BLOCK": "true",
            "INPUT_DEBUG": "false",
            "INPUT_DD_API_KEY": "test-api-key",
            "INPUT_DD_APP_KEY": "test-app-key",
            "INPUT_DD_SITE": "datadoghq.com",
            "INPUT_SCFW_HOME": "",
            "SCFW_TEST_VERSION": "4.2.0",
            "SCFW_TEST_LOG": str(self.directory / "invocation.json"),
            "SCFW_TEST_DOWNLOADS": str(self.directory / "downloads"),
        })
        Path(self.env["RUNNER_TEMP"]).mkdir()
        for name in ("GITHUB_PATH", "GITHUB_ENV", "GITHUB_OUTPUT"):
            path = self.directory / name
            path.touch()
            self.env[name] = str(path)

        # The installed stub records the SCFW invocation and executes the real PM.
        binary = f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import subprocess
import sys

if sys.argv[1:] == ["--version"]:
    print("scfw version " + os.environ["SCFW_TEST_VERSION"])
    sys.exit(0)
Path(os.environ["SCFW_TEST_LOG"]).write_text(json.dumps({
    "args": sys.argv[1:],
    "proxy": os.environ.get("SCFW_PROXY_ENABLED"),
}))
separator = sys.argv.index("--")
executable = sys.argv[sys.argv.index("--executable") + 1]
sys.exit(subprocess.run([executable, *sys.argv[separator + 2:]]).returncode)
'''
        checksum = hashlib.sha256(binary.encode()).hexdigest()
        self.write_executable("curl", f"#!{sys.executable}\n" + f'''
from pathlib import Path
import sys

url = next(arg for arg in sys.argv if arg.startswith("https://"))
output = Path(sys.argv[sys.argv.index("--output") + 1])
with Path({self.env['SCFW_TEST_DOWNLOADS']!r}).open("a") as log:
    log.write(url + "\\n")
if url.endswith("/scfw_SHA256SUMS"):
    output.write_text("".join(
        {checksum!r} + "  scfw-" + os_name + "-" + arch + "\\n"
        for os_name in ("linux", "darwin") for arch in ("amd64", "arm64")
    ))
else:
    output.write_text({binary!r})
''')

    def write_executable(self, name, contents):
        path = self.bin / name
        path.write_text(contents)
        path.chmod(0o755)
        return path

    def setup_action(self, version="4.2.0", installed_version=None, managers="uv", **options):
        self.env.update({
            "SCFW_VERSION": version,
            "SCFW_TEST_VERSION": installed_version or version,
            "SCFW_PACKAGE_MANAGERS": managers,
            **options,
        })
        return subprocess.run(
            ["bash", str(ROOT / "scripts/setup.sh")],
            env=self.env, text=True, capture_output=True,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_wrapper(self, pm, *args, **options):
        # GITHUB_PATH prepends each entry, with the last written entry first.
        paths = Path(self.env["GITHUB_PATH"]).read_text().splitlines()
        env = {**self.env, **options}
        env["PATH"] = os.pathsep.join([*reversed(paths), env["PATH"]])
        return subprocess.run([pm, *args], env=env, text=True, capture_output=True)

    def test_uv_minimum_version(self):
        for version in ("4.0.0", "4.1.0", "4.1.99", "4.2.0-rc.1"):
            with self.subTest(version=version):
                result = self.setup_action(version)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("uv requires SCFW 4.2.0 or later", result.stderr)
                self.assertFalse(Path(self.env["SCFW_TEST_DOWNLOADS"]).exists())

    def test_supported_uv_versions(self):
        for version in ("4.2.0", "4.2.1", "4.2.1-rc.1", "4.10.0", "5.0.0"):
            with self.subTest(version=version):
                self.assert_success(self.setup_action(version))
                self.assertIn(
                    f"scfw-version={version}",
                    Path(self.env["GITHUB_OUTPUT"]).read_text(),
                )

    def test_latest_checks_downloaded_version(self):
        for version in ("4.1.0", "4.2.0-rc.1", "unknown"):
            with self.subTest(version=version):
                result = self.setup_action("latest", installed_version=version)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("uv requires SCFW 4.2.0 or later", result.stderr)
                self.assertEqual(Path(self.env["GITHUB_PATH"]).read_text(), "")
                self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), "")
                self.assertFalse((Path(self.env["RUNNER_TEMP"]) / "scfw-wrappers/uv").exists())
        self.assert_success(self.setup_action("latest", installed_version="4.2.0"))
        self.assertIn(
            "/releases/latest/download/", Path(self.env["SCFW_TEST_DOWNLOADS"]).read_text(),
        )

    def test_older_versions_still_support_existing_managers(self):
        self.assert_success(self.setup_action("4.0.0", managers="npm,pip,poetry"))
        wrappers = Path(self.env["RUNNER_TEMP"]) / "scfw-wrappers"
        self.assertEqual({path.name for path in wrappers.iterdir()}, {"npm", "pip", "poetry"})
        self.assertNotIn("SCFW_PROXY_ENABLED", Path(self.env["GITHUB_ENV"]).read_text())

    def test_uv_wrapper_enables_proxy_and_preserves_arguments_and_exit_status(self):
        self.assert_success(self.setup_action(managers="npm, uv "))
        # uv may be installed after the action; resolve it at invocation time.
        uv = self.write_executable("uv", '#!/bin/bash\nprintf "%s\\n" "$@"\nexit 23\n')
        result = self.run_wrapper("uv", "pip", "install", "a package", "--no-cache")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout, "pip\ninstall\na package\n--no-cache\n")
        invocation = json.loads(Path(self.env["SCFW_TEST_LOG"]).read_text())
        self.assertEqual(invocation, {
            "args": ["run", "--executable", str(uv), "--error-on-block", "--",
                     "uv", "pip", "install", "a package", "--no-cache"],
            "proxy": "1",
        })
        self.assertNotIn("SCFW_PROXY_ENABLED", Path(self.env["GITHUB_ENV"]).read_text())

        self.write_executable("npm", "#!/bin/bash\nexit 0\n")
        self.assert_success(self.run_wrapper("npm", "--version"))
        invocation = json.loads(Path(self.env["SCFW_TEST_LOG"]).read_text())
        self.assertIsNone(invocation["proxy"])

    def test_uv_wrapper_applies_optional_flags_and_overrides_disabled_proxy(self):
        self.assert_success(self.setup_action(INPUT_DEBUG="true", SCFW_ERROR_ON_BLOCK="false"))
        self.write_executable("uv", "#!/bin/bash\nexit 0\n")
        self.assert_success(self.run_wrapper("uv", "sync", SCFW_PROXY_ENABLED="0"))
        invocation = json.loads(Path(self.env["SCFW_TEST_LOG"]).read_text())
        self.assertEqual(invocation["proxy"], "1")
        self.assertEqual(invocation["args"][3:], ["--log-level", "DEBUG", "--", "uv", "sync"])


if __name__ == "__main__":
    unittest.main()
