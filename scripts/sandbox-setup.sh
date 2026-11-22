#!/usr/bin/env bash
# Set up a disposable Linux sandbox to build and test this repository.
#
# Install the uv version pyproject.toml requires and the workspace's locked
# dependencies. With --check, also run scripts/check.sh with every argument after
# --check, in this same process. It replaces system files, so run it only as root
# in a throwaway sandbox, never on a workstation or in CI. Running it again is safe.
#
# usage: bash scripts/sandbox-setup.sh [--check [CHECK ARGS...]]
set -euo pipefail

cd "$(dirname "$0")/.."

fail() {
    echo "sandbox-setup: $1" >&2
    exit 1
}

require_disposable_linux() {
    if [[ "$EUID" -ne 0 ]]; then
        fail "the setup replaces system files, so it runs as root, not as user $EUID"
    fi
    if [[ "$(uname -s)" != "Linux" ]]; then
        fail "the setup is for a Linux sandbox, and this system is $(uname -s)"
    fi
}

# Install uv at the version [tool.uv] required-version allows, and make it the uv
# first on PATH.
install_toolchain() {
    local required installed on_path
    required="$(python3 -c 'import tomllib
with open("pyproject.toml", "rb") as file:
    print(tomllib.load(file)["tool"]["uv"]["required-version"])')"
    python3 -m pip install --quiet --break-system-packages --root-user-action=ignore "uv${required}"
    installed="$(python3 -c 'import uv; print(uv.find_uv_bin())')"
    on_path="$(command -v uv || true)"
    [[ -n "$on_path" ]] || fail "pip installed uv at $installed, which is not on PATH ($PATH)"
    if [[ "$(readlink -f "$on_path")" != "$(readlink -f "$installed")" ]]; then
        ln -sf "$installed" "$on_path"
    fi
    echo "uv: $(uv --version), meeting required-version $required"
}

# Install every workspace package and dev tool exactly as uv.lock pins them. The
# lock file is never created or changed here.
install_dependencies() {
    [[ -f uv.lock ]] || fail "uv.lock is missing, so there is nothing locked to install"
    uv sync --locked --all-packages --quiet
    echo "dependencies: synced from uv.lock with $(uv run --locked python --version)"
}

check=0
check_arguments=()
while (($# > 0)); do
    case "$1" in
        --check)
            check=1
            shift
            check_arguments=("$@")
            break
            ;;
        *) fail "unknown option $1; the only option is --check [CHECK ARGS...]" ;;
    esac
done

require_disposable_linux
install_toolchain
install_dependencies
if ((check == 1)); then
    echo
    exec bash scripts/check.sh ${check_arguments[@]+"${check_arguments[@]}"}
fi
echo
echo "Run every check in one command with"
echo "  bash scripts/sandbox-setup.sh --check"
