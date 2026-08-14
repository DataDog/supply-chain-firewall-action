#!/bin/bash
set -eo pipefail

install_scfw() {
  # Install pipx if not available
  if ! command -v pipx &>/dev/null; then
    pip install --quiet pipx
    python -m pipx ensurepath
  fi

  if [ "$SCFW_VERSION" = "latest" ]; then
    pipx install scfw --quiet
  else
    pipx install "scfw==$SCFW_VERSION" --quiet
  fi

  # Ensure pipx-managed binaries are on PATH for subsequent steps
  local pipx_bin_dir
  local installed_version
  pipx_bin_dir="$(pipx environment --value PIPX_BIN_DIR)"
  echo "$pipx_bin_dir" >> "$GITHUB_PATH"

  installed_version="$("$pipx_bin_dir/scfw" --version)"
  echo "scfw-version=$installed_version" >> "$GITHUB_OUTPUT"
  echo "Supply Chain Firewall installed: $installed_version"
}

create_package_manager_wrappers() {
  local wrapper_dir="$RUNNER_TEMP/scfw-wrappers"
  local error_on_block_flag
  local pm
  local -a package_managers

  mkdir -p "$wrapper_dir"
  IFS=',' read -ra package_managers <<< "$SCFW_PACKAGE_MANAGERS"

  for pm in "${package_managers[@]}"; do
    pm="${pm// /}"  # strip whitespace

    case "$pm" in
      pip|npm|poetry) ;;
      *) echo "Unsupported package manager: '$pm'" >&2; exit 1 ;;
    esac

    error_on_block_flag=""
    if [ "$SCFW_ERROR_ON_BLOCK" = "true" ]; then
      error_on_block_flag="--error-on-block"
    fi

    sed \
      -e "s|SCFW_PM_NAME|$pm|g" \
      -e "s|SCFW_EXTRA_FLAGS |${error_on_block_flag:+$error_on_block_flag }|g" \
      "$GITHUB_ACTION_PATH/scripts/pm-wrapper.sh.template" \
      > "$wrapper_dir/$pm"
    chmod +x "$wrapper_dir/$pm"
    echo "Created wrapper: $pm (real binary resolved at call time)"
  done

  echo "$wrapper_dir" >> "$GITHUB_PATH"
}

configure_scfw_environment() {
  if [ -n "$INPUT_DD_API_KEY" ]; then
    echo "DD_API_KEY=$INPUT_DD_API_KEY" >> "$GITHUB_ENV"
  fi

  if [ -n "$INPUT_DD_APP_KEY" ]; then
    echo "DD_APP_KEY=$INPUT_DD_APP_KEY" >> "$GITHUB_ENV"
  fi

  if [ "$INPUT_DD_API_LOGGER" = "true" ]; then
    echo "SCFW_DD_API_LOGGER_ENABLED=1" >> "$GITHUB_ENV"
  fi

  if [ "$INPUT_DD_CODESEC_LOGGER" = "true" ]; then
    echo "SCFW_DD_CODESEC_LOGGER_ENABLED=1" >> "$GITHUB_ENV"
  fi

  echo "DD_SITE=$INPUT_DD_SITE" >> "$GITHUB_ENV"
  echo "SCFW_DD_LOG_LEVEL=$INPUT_DD_LOG_LEVEL" >> "$GITHUB_ENV"
  echo "DD_ENV=$INPUT_DD_ENV" >> "$GITHUB_ENV"

  if [ -n "$INPUT_DD_LOG_ATTRIBUTES" ]; then
    echo "SCFW_DD_LOG_ATTRIBUTES=$INPUT_DD_LOG_ATTRIBUTES" >> "$GITHUB_ENV"
  fi

  if [ -n "$INPUT_SCFW_HOME" ]; then
    mkdir -p "$INPUT_SCFW_HOME"
    echo "SCFW_HOME=$INPUT_SCFW_HOME" >> "$GITHUB_ENV"
  fi

  if [ -n "$INPUT_ON_WARNING" ]; then
    echo "SCFW_ON_WARNING=$INPUT_ON_WARNING" >> "$GITHUB_ENV"
  fi

  if [ -n "$INPUT_PACKAGE_MINIMUM_AGE" ]; then
    echo "SCFW_PACKAGE_MINIMUM_AGE=$INPUT_PACKAGE_MINIMUM_AGE" >> "$GITHUB_ENV"
  fi
}

main() {
  install_scfw
  create_package_manager_wrappers
  configure_scfw_environment
}

main "$@"
