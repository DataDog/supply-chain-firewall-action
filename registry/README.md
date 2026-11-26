# Supply Chain Firewall registry action

Install a package manager and configure public npm or PyPI downloads to use the
Datadog Supply Chain Firewall registry. This separate action uses the backend
described in [stack #202827](https://github.com/ddoghq/dd-source/stack/202827).
It does not install the SCFW CLI or its command wrappers.

The action supports Linux and macOS. Run it after checkout and before installing
project dependencies. The selected executable is added to `PATH`, and registry
settings are exported for subsequent **steps in the same job**. GitHub Actions
jobs have separate environments; run this action in every job that needs it.
Do not pass credentials or credential-bearing configuration through job outputs,
caches, or artifacts.

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - uses: DataDog/supply-chain-firewall-action/registry@main
        with:
          package-manager: npm
          sat: ${{ secrets.DD_SAT }}
      - run: npm ci
```

## Inputs

| Input | Description | Default |
| --- | --- | --- |
| `package-manager` | One of `npm`, `pnpm`, `yarn`, `bun`, `pip`, `uv`, `poetry`. | Required |
| `sat` | Datadog service account token, passed as a GitHub Actions secret. API/application keys are not accepted by the registry. | Required |
| `dd_site` | Application hostname serving the registry, such as `app.datadoghq.com` or `app.datadoghq.eu`. An `https://` prefix is accepted. Paths and URL credentials are rejected. | `app.datadoghq.com` |
| `version` | Package-manager version, or `latest`. Unprefixed explicit versions are supported. `yarn` installs Yarn Berry; Yarn Classic versions are rejected. | `latest` |
| `node-version` | Node.js runtime installed for JavaScript clients. | `24` |
| `python-version` | Python runtime used for setup and Python clients; requires Python 3.11 or later. | `3.12` |
| `working-directory` | Existing project directory, relative to the workspace. Yarn Berry and Poetry write native source settings here. | `.` |

`dd_site` is the **application** hostname, including its regional prefix, rather
than the CLI action's `dd-site` value. Setup configures the requested hostname;
it does not check whether the PoC is deployed there or validate the SAT against
live Datadog ingress. Use a site on which the registry backend is available.

## Outputs

| Output | Description |
| --- | --- |
| `package-manager-version` | Installed client's `--version` output. |
| `registry-url` | Registry URL without credentials. |

## Clients and configuration

| Client | Installation and configuration | Subsequent step |
| --- | --- | --- |
| npm | Installs `npm` into a private prefix; temporary user npmrc with a path-scoped Bearer token. | `npm ci` |
| pnpm | Installs `pnpm` into a private prefix; same path-scoped npmrc. | `pnpm install --frozen-lockfile` |
| Yarn Berry | Installs `@yarnpkg/cli-dist`; sets `npmRegistryServer` and a path-scoped `npmRegistries` entry in the project's `.yarnrc.yml`, preserving unrelated settings. | `yarn install --immutable` |
| Bun | Installs the official `bun` npm distribution; uses the temporary npmrc. | `bun install --frozen-lockfile` |
| pip | Installs pip into a private virtual environment; exports an authenticated `PIP_INDEX_URL`, disables existing pip config files, and clears inherited extra-index settings. The environment's Python and pip are on `PATH`. | `pip install -r requirements.txt` |
| uv | Installs uv into a private tool environment; exports authenticated `UV_DEFAULT_INDEX` and `UV_INDEX_URL`, clearing inherited extra-index settings. | `uv sync --locked` |
| Poetry | Installs Poetry into a private tool environment; adds/updates a primary `scfw` source in `pyproject.toml` and exports separate HTTP Basic credentials. Requires an existing `pyproject.toml`. | `poetry install` |

JavaScript clients use `https://<dd_site>/api/v2/scfw/registry/npm/` with
Bearer authentication. Python clients use
`https://<dd_site>/api/v2/scfw/registry/pypi-proxy/simple/` with `__token__`
as the Basic username and the SAT as the password. The password is URL-encoded
for pip and uv. Direct `/pypi/` requires Bearer authentication and is not used.

The action downloads runtimes using pinned `actions/setup-node` and
`actions/setup-python`. It then installs the selected client from public npm or
PyPI into a unique runner temporary directory, before applying SAT configuration.
Client bootstrap downloads are outside registry inspection. Bun and native pnpm
need their official installation scripts; other JavaScript client installations
disable lifecycle scripts. Pin client and runtime versions for repeatable builds.

For Yarn Berry, run the action against each project that needs its registry map.
The token in `.yarnrc.yml` is an environment-variable reference, not the secret.
The action sets `YARN_IGNORE_PATH` so project `yarnPath` settings do not replace
the selected executable. The action does not migrate lockfiles.

Yarn Classic is rejected before client installation. Its bundled HTTP library
forwards Authorization on cross-origin 307 redirects, which would expose the SAT
to the public artifact host. Use Yarn Berry for registry mode.

For Poetry, the source change intentionally updates `pyproject.toml`. Projects
with other explicit sources are rejected so their registry routing can be
reconciled explicitly. Existing locks may contain public artifact URLs or become
stale after a source change: regenerate and review the lockfile with `poetry lock`
before relying on the registry. Source configuration for publishing does not
configure Poetry dependency installs.

For pip, a later `python -m venv .venv` retains registry environment settings;
`.venv/bin/python -m pip install ...` therefore uses the proxy too. For uv's pip
interface, first create the project environment with `uv venv`, then run
`uv pip install ...`.

### Multiple jobs

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        suite: [unit, integration]
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - uses: DataDog/supply-chain-firewall-action/registry@main
        with:
          package-manager: uv
          sat: ${{ secrets.DD_SAT }}
          dd_site: app.datadoghq.com
      - run: uv sync --locked
      - run: uv run pytest "tests/$SUITE"
        env:
          SUITE: ${{ matrix.suite }}
```

## Coverage and validation

Registry routing inspects requests that reach the gate. Local caches, direct
artifact URLs, Git/local dependencies, alternate indexes, scope-specific registry
settings, and existing lockfiles can bypass it. Review project configuration and
regenerate lockfiles through the registry when migrating. The PoC covers public
npm and PyPI packages; it does not host private packages. Clients still need public
download-host access for allowed redirects. npm audit requests can be forwarded
to public npm; use `--no-audit` when appropriate.

The SAT is masked and made available to subsequent steps through GitHub's job
environment. Only run trusted actions and scripts after this setup. Credentials
are scoped to the registry URL rather than exported as global npm authentication.
Python URLs containing credentials are masked as well. Do not print client
configuration or cache GitHub environment files.

Tests use a local HTTPS registry and a fake SAT to check real client downloads,
Bearer/Basic authentication, allowed redirects without credential forwarding,
and failed installations for blocked artifacts. These fixtures do not establish
live Datadog authentication, SCA authorization, deployment readiness, or report
delivery. Registry reports attribute requests to the SAT owner and observe an
ALLOW decision; they do not confirm installation completion or identify a CI run.
