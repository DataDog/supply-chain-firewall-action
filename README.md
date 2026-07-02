# supply-chain-firewall-action

A GitHub Action that installs Datadog's [Supply Chain Firewall](https://github.com/DataDog/supply-chain-firewall) (SCFW) and configures it to transparently intercept package manager commands (`pip`, `npm`, `poetry`) for all subsequent steps in a job.  When active, any command for a supported package manager is inspected with SCFW before being allowed to run.

This action currently supports Linux and macOS runners. Windows support is not included.

## Usage

```yaml
steps:
  - uses: actions/checkout@v4

  - uses: DataDog/supply-chain-firewall-action@main
    with:
      version: '3.1.0'

  # pip, npm, and poetry are now intercepted by Supply Chain Firewall.
  # Malicious packages are blocked; clean installs proceed normally.
  - run: pip install -r requirements.txt
  - run: npm install
```

### With Datadog logging

```yaml
steps:
  - uses: DataDog/supply-chain-firewall-action@main
    with:
      dd-api-key: ${{ secrets.DD_API_KEY }}
      dd-api-logger: 'true'
      dd-log-level: ALLOW
```

See [`examples/example.yml`](examples/example.yml) for a complete workflow you can copy into your own repo.

### With a cached `SCFW_HOME` directory

Caching `SCFW_HOME` avoids re-fetching verifier data on each run:

```yaml
steps:
  - uses: actions/cache@v4
    with:
      path: ~/.scfw
      key: scfw-${{ runner.os }}

  - uses: DataDog/supply-chain-firewall-action@main
    with:
      scfw-home: ~/.scfw
```

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `version` | Version of `supply-chain-firewall` to install. Use `"latest"` or pin to a specific release (e.g., `"0.7.0"`). | `latest` |
| `package-managers` | Comma-separated list of package managers to intercept. Supported: `pip`, `npm`, `poetry`. | `pip,npm,poetry` |
| `error-on-block` | Exit with a non-zero code when an installation is blocked, failing the workflow step. | `true` |
| `dd-api-key` | Datadog API key for forwarding firewall events to the Datadog HTTP or Code Security API. Use `${{ secrets.DD_API_KEY }}`. | — |
| `dd-app-key` | Datadog application key for forwarding firewall events to the Datadog Code Security API. Use `${{ secrets.DD_APP_KEY }}`. | — |
| `dd-api-logger` | When `"true"`, enables SCFW's Datadog HTTP API logger. Requires `dd-api-key`. | `false` |
| `dd-codesec-logger` | When `"true"`, enables SCFW's Datadog Code Security logger. Requires `dd-api-key` and `dd-app-key`. | `false` |
| `dd-site` | Datadog site (e.g., `datadoghq.com`, `datadoghq.eu`, `us3.datadoghq.com`). Optionally used by both `dd-api-logger` and `dd-codesec-logger`. | `datadoghq.com` |
| `dd-log-level` | Controls which firewall events are forwarded to Datadog. `ALLOW` logs all events; `BLOCK` logs only blocked events. | `ALLOW` |
| `scfw-home` | Directory for SCFW's local cache. Point this at a cached directory to speed up verifier data fetches across runs. | — |
| `on-warning` | Action that SCFW should take on warning-level findings: `ALLOW` or `BLOCK`. Defaults to `BLOCK` in non-interactive environments. | — |
| `package-minimum-age` | Minimum age in hours a package must have before installation is allowed. | `24` |
| `dd-env` | Datadog environment tag attached to all forwarded firewall events. | `ci` |

## Outputs

| Output | Description |
|--------|-------------|
| `scfw-version` | The installed version of Supply Chain Firewall. |

## How it works

1. **Install**: Supply Chain Firewall is installed via `pipx` into an isolated Python environment so it does not interfere with the project's own dependencies.

2. **Wrap**: For each requested package manager, the action writes a thin wrapper script into a temporary directory and prepends that directory to `PATH`. All subsequent steps that invoke those package managers automatically go through Supply Chain Firewall.

   Each wrapper resolves the real binary at call time by removing its own directory from `PATH` before searching, then passes the resolved path to `scfw run --executable`. This ensures Supply Chain Firewall always calls the real binary and never re-invokes the wrapper.

   > [!WARNING]
   > Activating a Python virtual environment (e.g., `source .venv/bin/activate`) shadows the Supply Chain Firewall wrappers for the remainder of that step. Use `scfw run pip install ...` explicitly for any commands run inside virtual environments.

3. **Configure**: Relevant environment variables (`DD_API_KEY`, `SCFW_HOME`, etc.) are written to `GITHUB_ENV` so they are available to all subsequent steps.
