#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
# Resolve the package without changing relative --config/--state arguments.
path_separator=":"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) path_separator=";"; repo_dir="$(cygpath -m "$repo_dir")" ;;
esac
export PYTHONPATH="$repo_dir${PYTHONPATH:+$path_separator$PYTHONPATH}"
if command -v py >/dev/null 2>&1 && py -3 -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  exec py -3 -m sentinel_app.full_onboarding "$@"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  exec python3 -m sentinel_app.full_onboarding "$@"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  exec python -m sentinel_app.full_onboarding "$@"
else
  echo "Install Python 3.10 or newer and reopen Git Bash." >&2
  exit 1
fi
