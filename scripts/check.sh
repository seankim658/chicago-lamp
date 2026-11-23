#!/usr/bin/env bash
# Run every check on the whole repository, or on chosen packages. Run with --help
# for usage.
#
# Step functions are called through run_step, which shellcheck cannot follow.
# shellcheck disable=SC2329
set -uo pipefail

# ---- settings ---------------------------------------------------------------
readonly PROJECT="chicago-lamp"
# Each package is a folder holding one of these manifests, named by its folder.
readonly MANIFEST_GLOB="packages/*/*/pyproject.toml"
# Lines of a failed step's log shown in the terminal.
readonly TAIL_LINES=60
# -----------------------------------------------------------------------------

usage() {
    cat <<'EOF'
usage: bash scripts/check.sh [--parallel] [--packages a,b] [--help]

Run every check on the whole repository, or with --packages on chosen packages,
and print one line per step with the time it took. The steps are lint, format,
mypy, deps, imports, licenses and tests.

Every step runs even after one fails. A failed step prints the last lines of its
output, and every step's full output stays in the log folder named at the end.
Dependencies are used in locked mode, so a check never rewrites the lock file,
and a lock file that is out of date fails the check. Run uv lock to update it.
The script exits 1 if any step failed and 2 on a usage error. A local pass is
never release evidence.

options
  --parallel        run the tests with one worker per CPU
  --packages a,b    check only these packages, each named by its folder, such as
                    lamp-collector. Lint, format, mypy, deps and tests are
                    narrowed to them. The imports and licenses steps still run
                    on the whole workspace. Packages that depend on the named ones are not
                    added, so a change to a shared package can break a dependent
                    that a scoped run never checks. The tests of scripts/checks
                    run only in a full check. A scoped run is never a full check.
  -h, --help        show this help and exit
EOF
}

usage_error() {
    echo "check.sh: $1 Run bash scripts/check.sh --help for usage." >&2
    exit 2
}

parallel=0
scoped=0
packages_option=""
while (($# > 0)); do
    case "$1" in
    -h | --help)
        usage
        exit 0
        ;;
    --parallel)
        parallel=1
        ;;
    --packages | --packages=*)
        ((scoped == 0)) || usage_error "--packages is given twice."
        scoped=1
        if [[ "$1" == --packages=* ]]; then
            packages_option="${1#--packages=}"
        else
            (($# > 1)) || usage_error "--packages needs a comma-separated list of packages."
            packages_option="$2"
            shift
        fi
        ;;
    *)
        usage_error "unknown option $1."
        ;;
    esac
    shift
done

cd "$(dirname "$0")/.." || exit 1

# ---- discovery --------------------------------------------------------------
# workspace_folders holds each package's path at the same index as its name.
workspace_packages=()
workspace_folders=()
for manifest in $MANIFEST_GLOB; do
    [[ -f "$manifest" ]] || continue
    folder="${manifest%/*}"
    workspace_packages+=("${folder##*/}")
    workspace_folders+=("$folder")
done

package_folder() {
    local index
    for index in "${!workspace_packages[@]}"; do
        if [[ "${workspace_packages[$index]}" == "$1" ]]; then
            echo "${workspace_folders[$index]}"
            return 0
        fi
    done
    return 1
}

contains() {
    local wanted="$1" candidate
    shift
    for candidate in "$@"; do
        [[ "$candidate" == "$wanted" ]] && return 0
    done
    return 1
}

# join_comma A B... prints its arguments separated by a comma and a space.
join_comma() {
    local IFS=","
    local text="$*"
    echo "${text//,/, }"
}

refuse_packages() {
    usage_error "$1 Packages: $(join_comma ${workspace_packages[@]+"${workspace_packages[@]}"})."
}

# choose_packages reads --packages into chosen, in the order given, with repeats
# dropped. It refuses an empty list, an empty name and an unknown name.
choose_packages() {
    local name names
    chosen=()
    [[ -n "$packages_option" ]] || refuse_packages "--packages needs at least one package."
    IFS="," read -r -a names <<<"$packages_option"
    for name in ${names[@]+"${names[@]}"}; do
        [[ -n "$name" ]] || refuse_packages "--packages $packages_option holds an empty name."
        contains "$name" ${workspace_packages[@]+"${workspace_packages[@]}"} ||
            refuse_packages "$name is not a package."
        contains "$name" ${chosen[@]+"${chosen[@]}"} || chosen+=("$name")
    done
    ((${#chosen[@]} > 0)) || refuse_packages "--packages needs at least one package."
}

has_test_files() {
    [[ -n "$(find "$1" -name 'test_*.py' -not -path '*/__pycache__/*' | head -n 1)" ]]
}

chosen=()
skipped=()
package_paths=()
test_paths=()
if ((scoped == 1)); then
    choose_packages
    for name in "${chosen[@]}"; do
        folder="$(package_folder "$name")"
        package_paths+=("$folder")
        if has_test_files "$folder"; then
            test_paths+=("$folder")
        fi
    done
    for name in ${workspace_packages[@]+"${workspace_packages[@]}"}; do
        contains "$name" "${chosen[@]}" || skipped+=("$name")
    done
fi

# ---- step runner ------------------------------------------------------------
# bash's time keyword reports elapsed seconds to two decimals, even in bash 3.2.
TIMEFORMAT='%2R'
# macOS sets TMPDIR with a trailing slash, so drop it before adding the name.
temp_root="${TMPDIR:-/tmp}"
log_dir="$(mktemp -d "${temp_root%/}/$PROJECT-check.XXXXXX")"
failed=()

# A scoped run marks every step line, so its output can never pass for a full run.
narrowed_note=""
whole_note=""
if ((scoped == 1)); then
    narrowed_note="[packages: $(join_comma "${chosen[@]}")]"
    whole_note="(whole workspace)"
fi

# run_step NAME NOTE COMMAND... runs COMMAND, logging its output to NAME.log and its
# elapsed time to NAME.time. A NOTE that is not empty ends the step's line.
run_step() {
    local name="$1"
    local note="$2"
    shift 2
    local log="$log_dir/$name.log"
    local timing="$log_dir/$name.time"
    local status elapsed
    { time "$@" >"$log" 2>&1; } 2>"$timing"
    status=$?
    elapsed="$(<"$timing")"
    if ((status == 0)); then
        echo "PASS  $name  $elapsed s${note:+  $note}"
        return 0
    fi
    echo "FAIL  $name  $elapsed s${note:+  $note}"
    tail -n "$TAIL_LINES" "$log" | sed 's/^/      /'
    failed+=("$name")
    return 1
}

# ---- steps ------------------------------------------------------------------
# Each step function takes the paths to check. A full run passes ".", except to
# a tool that works one package at a time, which gets every package folder.

# Every tool runs in the locked environment of the whole workspace.
uv_run() { uv run --locked --all-packages "$@"; }

lint() { uv_run ruff check --output-format concise "$@"; }
format_check() { uv_run ruff format --check "$@"; }

# A full run passes ".", which means "use the configured paths".
type_check_mypy() {
    if [[ "$*" == "." ]]; then uv_run mypy; else uv_run mypy "$@"; fi
}

# deptry reads the manifest in its working directory, so it runs inside each package.
dependency_check() {
    local status=0 package
    for package in "$@"; do
        echo "== $package"
        (cd "$package" && uv_run deptry --no-ansi src) || status=1
    done
    return "$status"
}

import_contracts() {
    local contracts="$log_dir/importlinter.ini"
    uv_run python scripts/checks/import_contracts.py "$contracts" &&
        uv_run lint-imports "$contracts"
}

# Only runtime dependencies ship, so dependency groups and the workspace's own
# packages are left out.
licenses() {
    uv export --quiet --locked --all-packages --no-default-groups --no-emit-workspace \
        --no-hashes --no-header --no-annotate |
        uv_run python scripts/checks/license_check.py
}

run_test_command() { uv_run pytest "$@"; }

test_options=(-q -rs)
if ((parallel == 1)); then
    test_options+=(-n auto --dist loadscope)
fi

run_scoped_steps() {
    run_step lint "$narrowed_note" lint "${package_paths[@]}"
    run_step format "$narrowed_note" format_check "${package_paths[@]}"
    run_step mypy "$narrowed_note" type_check_mypy "${package_paths[@]}"
    run_step deps "$narrowed_note" dependency_check "${package_paths[@]}"
}

run_full_steps() {
    run_step lint "" lint .
    run_step format "" format_check .
    run_step mypy "" type_check_mypy .
    run_step deps "" dependency_check ${workspace_folders[@]+"${workspace_folders[@]}"}
}

run_tests() {
    if ((scoped == 1 && ${#test_paths[@]} == 0)); then
        echo "SKIP  tests  none of the chosen packages has a test file  $narrowed_note"
    elif run_step tests "$narrowed_note" run_test_command \
        "${test_options[@]}" ${test_paths[@]+"${test_paths[@]}"}; then
        tail -n 1 "$log_dir/tests.log" | sed 's/^/      /'
        skip_reasons "$log_dir/tests.log"
    fi
}

# Print each reason tests skipped for, with its count, most first, so a missing
# fixture never hides inside a passing run. This reads pytest's -rs lines, such
# as "SKIPPED [3] path: reason".
skip_reasons() {
    awk 'match($0, /^SKIPPED \[[0-9]+\] [^ ]+: /) {
        count[substr($0, RLENGTH + 1)] += substr($0, 10) + 0
    }
    END { for (reason in count) printf "%d\t%s\n", count[reason], reason }' "$1" |
        sort -t "$(printf '\t')" -k1,1nr -k2 |
        awk -F '\t' '{ printf "      %d skipped: %s\n", $1, $2 }'
}

run_steps() {
    if ((scoped == 1)); then
        run_scoped_steps
    else
        run_full_steps
    fi
    run_step imports "$whole_note" import_contracts
    run_step licenses "$whole_note" licenses
    run_tests
}

# ---- summary ----------------------------------------------------------------
# time reports on the group's standard error, which goes to total.time. The steps'
# own standard error is passed through on fd 3, so nothing they print is lost.
total_time="$log_dir/total.time"
{ time run_steps 2>&3; } 3>&2 2>"$total_time"

echo
echo "Total  $(<"$total_time") s"
echo "Logs: $log_dir"
status=0
if ((${#failed[@]} > 0)); then
    echo "Failed: ${failed[*]}"
    status=1
elif ((scoped == 1)); then
    echo "All checks in this scoped run passed"
else
    echo "All checks passed"
fi
if ((scoped == 1)); then
    echo "SCOPED RUN, not a full check"
    echo "  Covered: $(join_comma "${chosen[@]}")"
    if ((${#skipped[@]} > 0)); then
        echo "  Skipped: $(join_comma "${skipped[@]}")"
    else
        echo "  Skipped: none"
    fi
    echo "  Not run: tests of scripts/checks"
fi
exit "$status"
