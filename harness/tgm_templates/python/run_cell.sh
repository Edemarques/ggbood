#!/bin/bash
# Python runner: run_cell.sh REPO OUT_JUNIT  (cwd = REPO, run as an unprivileged user, offline)
# Exit 0 = all tests passed, 1 = some failed, anything else = no verdict (collection error, no tests, ...).
# Settings from sample.env: PY_SRC (import root inside the repository, default ".").
REPO="$1"; OUT="$2"
PY="${TG_PYTHON:-python3}"
export PYTHONPATH="$REPO/${PY_SRC:-.}" PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
exec "$PY" -m pytest "$REPO/regression_tests" -q -p no:cacheprovider -c /dev/null --rootdir="$REPO" \
  --confcutdir="$REPO/regression_tests" --junitxml="$OUT"
