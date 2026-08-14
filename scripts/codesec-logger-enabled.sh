#!/bin/bash
set -eo pipefail

if [ -z "$SCFW_VERSION" ] || [ "$SCFW_VERSION" = "latest" ]; then
  SCFW_VERSION="4.0.0"
fi
readonly SCFW_VERSION
readonly RELEASE_BASE_URL="https://github.com/DataDog/supply-chain-firewall/releases/download/v${SCFW_VERSION}"

warn_deprecated_inputs() {
  echo "::warning::The error-on-block, on-warning, package-minimum-age, dd-log-level, dd-env, dd-log-attributes, and dd-api-logger inputs are deprecated since SCFW v4.0.0 and are ignored by the Code Security path."
}

validate_required_inputs() {
  if [ -z "$INPUT_DD_API_KEY" ]; then
    echo "dd-api-key is required when dd-codesec-logger is enabled" >&2
    exit 1
  fi

  if [ -z "$INPUT_DD_APP_KEY" ]; then
    echo "dd-app-key is required when dd-codesec-logger is enabled" >&2
    exit 1
  fi
}

install_scfw() {
  local os
  local arch
  local asset
  local install_dir="$RUNNER_TEMP/scfw-bin"
  local binary_path="$install_dir/scfw"
  local checksums_path="$install_dir/supply-chain-firewall_SHA256SUMS"

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

  asset="supply-chain-firewall-${os}-${arch}"
  mkdir -p "$install_dir"

  curl --fail --silent --show-error --location \
    "$RELEASE_BASE_URL/$asset" \
    --output "$binary_path"
  curl --fail --silent --show-error --location \
    "$RELEASE_BASE_URL/supply-chain-firewall_SHA256SUMS" \
    --output "$checksums_path"

  verify_checksum "$asset" "$binary_path" "$checksums_path"
  chmod +x "$binary_path"

  echo "$install_dir" >> "$GITHUB_PATH"
  echo "scfw-version=$SCFW_VERSION" >> "$GITHUB_OUTPUT"
  echo "Supply Chain Firewall installed: $SCFW_VERSION ($os/$arch)"
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

configure_scfw() {
  local scfw="$RUNNER_TEMP/scfw-bin/scfw"
  local pm
  local -a package_managers
  local -a configure_args

  IFS=',' read -ra package_managers <<< "$SCFW_PACKAGE_MANAGERS"
  for pm in "${package_managers[@]}"; do
    pm="${pm// /}"
    configure_args+=("--alias-$pm")
  done

  configure_args+=("--dd-api-key=$INPUT_DD_API_KEY")
  configure_args+=("--dd-app-key=$INPUT_DD_APP_KEY")
  if [ -n "$INPUT_DD_SITE" ]; then
    configure_args+=("--dd-site=$INPUT_DD_SITE")
  fi
  if [ -n "$INPUT_SCFW_HOME" ]; then
    mkdir -p "$INPUT_SCFW_HOME"
    configure_args+=("--scfw-home=$INPUT_SCFW_HOME")
  fi

  "$scfw" configure "${configure_args[@]}"
}

main() {
  warn_deprecated_inputs
  validate_required_inputs
  install_scfw
  configure_scfw
}

main "$@"
