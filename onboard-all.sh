#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v py >/dev/null 2>&1 && py -3 -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  exec py -3 "$script_dir/full_onboarding.py" "$@"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  exec python3 "$script_dir/full_onboarding.py" "$@"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  exec python "$script_dir/full_onboarding.py" "$@"
else
  echo "Install Python 3.10 or newer and reopen Git Bash." >&2
  exit 1
fi

