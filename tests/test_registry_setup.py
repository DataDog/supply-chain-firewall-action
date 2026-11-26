"""Registry configuration and failure handling, without external downloads."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("registry_setup", ROOT / "registry/setup.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scfw registry test ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.project = self.directory / "project"
        self.project.mkdir()
        self.env = {
            **os.environ, "INPUT_PACKAGE_MANAGER": "npm", "INPUT_VERSION": "latest",
            "INPUT_DD_SITE": "app.datadoghq.com", "INPUT_SAT": "test-sat",
            "INPUT_WORKING_DIRECTORY": str(self.project), "RUNNER_TEMP": str(self.directory),
        }
        for name in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT"):
            path = self.directory / name
            path.touch()
            self.env[name] = str(path)
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def configure(self, manager, sat="test-sat"):
        with patch.object(setup, "run") as command:
            values, url = setup.configure(
                manager, "https://app.datadoghq.com", sat, self.directory,
                self.project, self.directory / manager, {},
            )
        return values, url, command

    def test_npm_credentials_are_path_scoped_and_not_in_configuration(self):
        for manager in ("npm", "pnpm", "bun"):
            with self.subTest(manager=manager):
                values, url, _ = self.configure(manager)
                config = Path(values["NPM_CONFIG_USERCONFIG"])
                self.assertEqual(url, "https://app.datadoghq.com/api/v2/scfw/registry/npm/")
                self.assertIn("//app.datadoghq.com/api/v2/scfw/registry/npm/:_authToken=${SCFW_REGISTRY_SAT}", config.read_text())
                self.assertNotIn("test-sat", config.read_text())
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                self.assertEqual(values["NPM_CONFIG_REGISTRY"], url)

    def test_yarn_berry_uses_native_per_registry_credentials(self):
        values, url, command = self.configure("yarn")
        self.assertEqual(command.call_count, 3)
        args = [call.args[0] for call in command.call_args_list]
        self.assertIn("npmRegistries[\"//app.datadoghq.com/api/v2/scfw/registry/npm\"].npmAuthToken", args[-1])
        self.assertEqual(args[-1][-1], "${SCFW_REGISTRY_SAT}")
        self.assertEqual(values["YARN_NPM_REGISTRY_SERVER"], url.rstrip("/"))
        self.assertNotIn("YARN_NPM_AUTH_TOKEN", values)

    def test_python_uses_basic_proxy_and_encodes_password(self):
        for manager, key in (("pip", "PIP_INDEX_URL"), ("uv", "UV_DEFAULT_INDEX")):
            with self.subTest(manager=manager):
                values, url, _ = self.configure(manager, sat="test:@/#%")
                self.assertEqual(url, "https://app.datadoghq.com/api/v2/scfw/registry/pypi-proxy/simple/")
                self.assertIn("__token__:test%3A%40%2F%23%25@", values[key])
                self.assertEqual(values["PIP_EXTRA_INDEX_URL" if manager == "pip" else "UV_EXTRA_INDEX_URL"], "")

    def test_poetry_configures_install_source_and_separate_auth(self):
        values, url, command = self.configure("poetry")
        self.assertEqual(command.call_args.args[0][1:], ["source", "add", "--priority=primary", "scfw", url])
        self.assertEqual(values["POETRY_HTTP_BASIC_SCFW_PASSWORD"], "test-sat")
        self.assertNotIn("test-sat", str(command.call_args.args))

    def test_bootstrap_does_not_inherit_credentials_or_registry_overrides(self):
        with patch.dict(os.environ, {"SCFW_REGISTRY_SAT": "old", "PIP_INDEX_URL": "old",
                                    "npm_config_registry": "old", "YARN_NPM_AUTH_TOKEN": "old"}):
            env = setup.bootstrap_environment()
        self.assertNotIn("INPUT_SAT", env)
        self.assertNotIn("SCFW_REGISTRY_SAT", env)
        self.assertNotIn("PIP_INDEX_URL", env)
        self.assertNotIn("npm_config_registry", env)
        self.assertNotIn("YARN_NPM_AUTH_TOKEN", env)

    def test_all_clients_are_installed_into_private_directory(self):
        for manager, version, package in (
            ("npm", "latest", "npm@latest"), ("pnpm", "10.0.0", "pnpm@10.0.0"),
            ("yarn", "4.9.2", "@yarnpkg/cli-dist@4.9.2"),
            ("bun", "latest", "bun@latest"), ("pip", "25.1.1", "pip==25.1.1"),
            ("uv", "latest", "uv"), ("poetry", "latest", "poetry"),
        ):
            with self.subTest(manager=manager, version=version):
                with patch.object(setup, "run", return_value="1.0.0") as command:
                    binary_path, _ = setup.install(manager, version, self.directory, {})
                commands = [call.args[0] for call in command.call_args_list]
                self.assertIn(package, commands[-2])
                self.assertEqual(commands[-1], [str(binary_path / manager), "--version"])
                if manager in setup.JS_MANAGERS:
                    self.assertEqual("--ignore-scripts" in commands[0], manager not in ("bun", "pnpm"))

    def test_yarn_classic_is_rejected_before_installation(self):
        for version in ("0.27.5", "1.22.22"):
            with self.subTest(version=version), patch.dict(os.environ, INPUT_PACKAGE_MANAGER="yarn", INPUT_VERSION=version):
                with patch.object(setup, "install") as install, self.assertRaisesRegex(ValueError, "Yarn Classic is not supported"):
                    setup.main()
                install.assert_not_called()
        self.assertEqual(Path(self.env["GITHUB_ENV"]).read_text(), "")

    def test_site_normalization_and_rejection(self):
        self.assertEqual(setup.registry_origin("app.datadoghq.com"), "https://app.datadoghq.com")
        self.assertEqual(setup.registry_origin("https://APP.datadoghq.eu/"), "https://app.datadoghq.eu")
        for site in ("http://example.com", "https://user:pass@example.com", "example.com/path",
                     "example.com?query", "example.com#fragment", "example.com\nINJECT=1", "", "host:99999"):
            with self.subTest(site=site), self.assertRaises(ValueError):
                setup.registry_origin(site)

    def test_invalid_inputs_do_not_install_or_publish(self):
        for key, value in (("INPUT_SAT", ""), ("INPUT_SAT", "a\nb"),
                           ("INPUT_PACKAGE_MANAGER", "npm,pip"), ("INPUT_VERSION", "--evil"),
                           ("INPUT_DD_SITE", "https://token@example.com")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with patch.object(setup, "install") as install, self.assertRaises(ValueError):
                    setup.main()
                install.assert_not_called()
        for name in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT"):
            self.assertEqual(Path(self.env[name]).read_text(), "")

    def test_failed_install_does_not_publish_partial_configuration(self):
        with patch.object(setup, "install", side_effect=RuntimeError("download failed")):
            with self.assertRaises(RuntimeError):
                setup.main()
        self.assertEqual(Path(self.env["GITHUB_ENV"]).read_text(), "")
        self.assertEqual(Path(self.env["GITHUB_PATH"]).read_text(), "")

    def test_poetry_rejects_existing_alternate_sources_before_install(self):
        (self.project / "pyproject.toml").write_text('[[tool.poetry.source]]\nname = "private"\nurl = "https://private.example/simple/"\n')
        with patch.dict(os.environ, INPUT_PACKAGE_MANAGER="poetry"), patch.object(setup, "install") as install:
            with self.assertRaisesRegex(ValueError, "Reconcile existing Poetry sources"):
                setup.main()
        install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
