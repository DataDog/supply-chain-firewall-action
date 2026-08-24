#!/bin/bash
set -eo pipefail

validate_inputs() {
  local major_version
  local pm
  local -a package_managers

  if [ -z "$INPUT_DD_API_KEY" ]; then
    echo "dd-api-key is required" >&2
    exit 1
  fi

  if [ -z "$INPUT_DD_APP_KEY" ]; then
    echo "dd-app-key is required" >&2
    exit 1
  fi

  if [ "$SCFW_VERSION" != "latest" ]; then
    if ! [[ "$SCFW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
      echo "version must be 'latest' or a semantic version without a leading 'v'" >&2
      exit 1
    fi
    major_version="${SCFW_VERSION%%.*}"
    if (( 10#$major_version < 4 )); then
      echo "version must be 'latest' or a Go-based SCFW release (v4+)" >&2
      exit 1
    fi
  fi

  case "$SCFW_ERROR_ON_BLOCK" in
    true|false) ;;
    *) echo "error-on-block must be either 'true' or 'false'" >&2; exit 1 ;;
  esac

  case "$INPUT_DEBUG" in
    true|false) ;;
    *) echo "debug must be either 'true' or 'false'" >&2; exit 1 ;;
  esac

  case "$INPUT_ON_WARNING" in
    ""|ALLOW|allow|BLOCK|block) ;;
    *) echo "on-warning must be either 'ALLOW' or 'BLOCK'" >&2; exit 1 ;;
  esac

  IFS=',' read -ra package_managers <<< "$SCFW_PACKAGE_MANAGERS"
  for pm in "${package_managers[@]}"; do
    pm="${pm// /}"
    case "$pm" in
      pip|npm|poetry) ;;
      *) echo "Unsupported package manager: '$pm'" >&2; exit 1 ;;
    esac
  done
}

verify_checksum() {
  local asset="$1"
  local binary_path="$2"
  local checksums_path="$3"
  local expected_checksum
  local actual_checksum

  expected_checksum="$(awk -v asset="$asset" '$2 == asset || $2 == "*" asset { print $1 }' "$checksums_path")"
  if [ -z "$expected_checksum" ]; then
    echo "No checksum found for $asset" >&2
    exit 1
  fi

  if command -v sha256sum &>/dev/null; then
    actual_checksum="$(sha256sum "$binary_path" | awk '{print $1}')"
  else
    actual_checksum="$(shasum -a 256 "$binary_path" | awk '{print $1}')"
  fi

  if [ "$actual_checksum" != "$expected_checksum" ]; then
    echo "Checksum verification failed for $asset" >&2
    exit 1
  fi
}

install_scfw() {
  local os
  local arch
  local asset
  local release_base_url
  local install_dir="$RUNNER_TEMP/scfw-bin"
  local binary_path="$install_dir/scfw"
  local checksums_path="$install_dir/scfw_SHA256SUMS"
  local installed_version

  case "$(uname -s)" in
    Linux) os="linux" ;;
    Darwin) os="darwin" ;;
    *) echo "Unsupported operating system: $(uname -s)" >&2; exit 1 ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) arch="amd64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac

  if [ "$SCFW_VERSION" = "latest" ]; then
    release_base_url="https://github.com/DataDog/supply-chain-firewall/releases/latest/download"
  else
    release_base_url="https://github.com/DataDog/supply-chain-firewall/releases/download/v${SCFW_VERSION}"
  fi

  asset="scfw-${os}-${arch}"
  mkdir -p "$install_dir"

  echo "Installing Supply Chain Firewall ${SCFW_VERSION} for ${os}/${arch}"
  curl --fail --silent --show-error --location \
    "$release_base_url/$asset" \
    --output "$binary_path"
  curl --fail --silent --show-error --location \
    "$release_base_url/scfw_SHA256SUMS" \
    --output "$checksums_path"

  verify_checksum "$asset" "$binary_path" "$checksums_path"
  echo "Verified SHA-256 checksum for $asset"
  chmod +x "$binary_path"

  installed_version="$("$binary_path" --version)"
  installed_version="${installed_version#scfw version }"
  echo "$install_dir" >> "$GITHUB_PATH"
  echo "scfw-version=$installed_version" >> "$GITHUB_OUTPUT"
  echo "Supply Chain Firewall installed: $installed_version"
}

create_package_manager_wrappers() {
  local wrapper_dir="$RUNNER_TEMP/scfw-wrappers"
  local pm
  local scfw_extra_flags=""
  local -a package_managers

  if [ "$INPUT_DEBUG" = "true" ]; then
    scfw_extra_flags="--log-level DEBUG"
    echo "SCFW debug logging is enabled for intercepted package manager commands"
  fi

  if [ "$SCFW_ERROR_ON_BLOCK" = "true" ]; then
    scfw_extra_flags="${scfw_extra_flags:+$scfw_extra_flags }--error-on-block"
  fi

  mkdir -p "$wrapper_dir"
  IFS=',' read -ra package_managers <<< "$SCFW_PACKAGE_MANAGERS"
  for pm in "${package_managers[@]}"; do
    pm="${pm// /}"
    sed \
      -e "s|SCFW_PM_NAME|$pm|g" \
      -e "s|SCFW_EXTRA_FLAGS |${scfw_extra_flags:+$scfw_extra_flags }-- |g" \
      "$GITHUB_ACTION_PATH/scripts/pm-wrapper.sh.template" \
      > "$wrapper_dir/$pm"
    chmod +x "$wrapper_dir/$pm"
    echo "Created wrapper: $pm (real binary resolved at call time)"
  done

  echo "$wrapper_dir" >> "$GITHUB_PATH"
}

configure_scfw_environment() {
  echo "Configuring Supply Chain Firewall (site: ${INPUT_DD_SITE:-datadoghq.com})"
  echo "DD_API_KEY=$INPUT_DD_API_KEY" >> "$GITHUB_ENV"
  echo "DD_APP_KEY=$INPUT_DD_APP_KEY" >> "$GITHUB_ENV"
  echo "DD_SITE=${INPUT_DD_SITE:-datadoghq.com}" >> "$GITHUB_ENV"

  if [ -n "$INPUT_SCFW_HOME" ]; then
    mkdir -p "$INPUT_SCFW_HOME"
    echo "SCFW_HOME=$INPUT_SCFW_HOME" >> "$GITHUB_ENV"
    echo "Using persistent SCFW cache directory: $INPUT_SCFW_HOME"
  fi

  if [ -n "$INPUT_ON_WARNING" ]; then
    echo "SCFW_ON_WARNING=$INPUT_ON_WARNING" >> "$GITHUB_ENV"
  fi

  echo "Supply Chain Firewall configuration complete"
}

main() {
  validate_inputs
  install_scfw
  create_package_manager_wrappers
  configure_scfw_environment
}

main "$@"
