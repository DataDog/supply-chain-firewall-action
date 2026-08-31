# supply-chain-firewall-action

A GitHub Action that installs Datadog's [Supply Chain Firewall](https://github.com/DataDog/supply-chain-firewall) (SCFW) and configures it to transparently intercept supported package manager commands for all subsequent steps in a job.  When active, any command for a supported package manager is inspected with SCFW before being allowed to run.

The action installs the Go-based SCFW CLI (v4 or later) on Linux and macOS runners. Windows runners are not supported.

---

### Interested in SCFW for your business use case?

[Enroll as a design partner](https://docs.google.com/forms/d/1Xqh5h1n3-jC7au2t30fdTq732dkTJqt_cb7C7T-AkPc/edit).

---

> [!NOTE]
> To remain on the legacy Python version, continue targeting `DataDog/supply-chain-firewall-action@v1`. The `v1` release line will be deprecated, so migrate to the Go-based action when possible.

## Usage

```yaml
steps:
  - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

  - uses: DataDog/supply-chain-firewall-action@main
    with:
      package-managers: npm,pip
      dd-api-key: ${{ secrets.DD_API_KEY }}
      dd-app-key: ${{ secrets.DD_APP_KEY }}

  # npm and pip commands are now transparently intercepted by SCFW.
  # Malicious packages are blocked; clean installs proceed normally.
  - run: pip install -r requirements.txt
  - run: npm install
```

The API and application keys are required by the Go CLI for Code Security policy evaluation and reporting.

### With debug logging

```yaml
steps:
  - uses: DataDog/supply-chain-firewall-action@main
    with:
      dd-api-key: ${{ secrets.DD_API_KEY }}
      dd-app-key: ${{ secrets.DD_APP_KEY }}
      debug: 'true'
```

See [`examples/example.yml`](examples/example.yml) for a complete workflow you can copy into your own repo.

### With a cached `SCFW_HOME` directory

Caching `SCFW_HOME` avoids re-fetching verifier data on each run:

```yaml
steps:
  - uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
    with:
      path: ~/.scfw
      key: scfw-${{ runner.os }}

  - uses: DataDog/supply-chain-firewall-action@main
    with:
      scfw-home: ~/.scfw
      dd-api-key: ${{ secrets.DD_API_KEY }}
      dd-app-key: ${{ secrets.DD_APP_KEY }}
```

## How it works

1. **Install**: The action downloads the Go binary for the runner's operating system and architecture from the SCFW GitHub release, then verifies it against the release's SHA-256 checksum manifest.

2. **Wrap**: For each requested package manager, the action writes a thin wrapper script into a temporary directory and prepends that directory to `PATH`. All subsequent steps that invoke those package managers automatically go through Supply Chain Firewall.

   Each wrapper resolves the real binary at call time by removing its own directory from `PATH` before searching, then passes the resolved path to `scfw run --executable`. This ensures Supply Chain Firewall always calls the real binary and never re-invokes the wrapper.

   > :warning: Activating a Python virtual environment (e.g., `source .venv/bin/activate`) shadows the Supply Chain Firewall wrappers for the remainder of that step. Use `scfw run -- pip install ...` explicitly for any commands run inside virtual environments.

3. **Configure**: Relevant environment variables (`DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`, and optional SCFW settings) are written to `GITHUB_ENV` so they are available to all subsequent steps.

   > :warning: Environment variables written to `GITHUB_ENV` are accessible to all subsequent steps in the job, including any third-party actions that run after this one. If you supply `dd-api-key` or `dd-app-key`, audit the actions that follow in your workflow to ensure none are untrusted or compromised.

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `version` | The Go-based SCFW release to install. Use `"latest"` or pin to a v4+ release (e.g., `"4.0.0"`). | `latest` |
| `package-managers` | Comma-separated list of package managers to intercept. Supported: `npm`, `pip`, `poetry`. | `npm,pip,poetry` |
| `error-on-block` | Exit with a non-zero code when an installation is blocked, failing the workflow step. | `true` |
| `dd-api-key` | Datadog API key used for Code Security policy evaluation and reporting. Use `${{ secrets.DD_API_KEY }}`. | Required |
| `dd-app-key` | Datadog application key used for Code Security policy evaluation and reporting. Use `${{ secrets.DD_APP_KEY }}`. | Required |
| `debug` | When `"true"`, enables local SCFW debug logs. Debug output may contain API request and response bodies. | `false` |
| `dd-site` | Datadog site (e.g., `datadoghq.com`, `datadoghq.eu`, `us3.datadoghq.com`). | `datadoghq.com` |
| `scfw-home` | Directory for SCFW's local cache. Point this at a cached directory to speed up verifier data fetches across runs. | — |

## Outputs

| Output | Description |
|--------|-------------|
| `scfw-version` | The installed version of Supply Chain Firewall. |
