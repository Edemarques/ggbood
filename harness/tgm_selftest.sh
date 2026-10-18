#!/bin/bash
# Self-test of harness/tgm.py: converts the public ridges-bench sample toolz__dict-helpers into the tgm layout and
# grades its reference suite locally (and, with --docker, inside the task's Docker images). Expect "reward": 1.
# usage: bash harness/tgm_selftest.sh [--docker]
#   RIDGES_BENCH  path of a ridges-bench clone (cloned to /opt/ridges-bench when missing)
#   TG_CA         CA bundle passed to docker builds (only behind a TLS-intercepting proxy)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RB=${RIDGES_BENCH:-/opt/ridges-bench}
[ -d "$RB/test-generation" ] || git clone -q --depth 1 https://github.com/ridgesai/ridges-bench "$RB"
O=$RB/test-generation/toolz__dict-helpers
TG_BENCH=$(mktemp -d)
export TG_BENCH TG_ROOT=${TG_SELFTEST_ROOT:-/opt/tgm-selftest}
trap 'rm -rf "$TG_BENCH"' EXIT
SHA=$(grep -o 'checkout [0-9a-f]\{40\}' "$O/environment/Dockerfile" | head -1 | cut -d' ' -f2)
python3 "$HERE/tgm.py" new toolz__dict-helpers --lang python --url https://github.com/pytoolz/toolz.git --sha "$SHA" \
  --var PY_SRC=. --desc "tgm self-test" > /dev/null
cp "$O"/tests/*.patch "$TG_BENCH/toolz__dict-helpers/tests/"
cp "$O/solution/solve.sh" "$TG_BENCH/toolz__dict-helpers/solution/"
cp "$O/instruction.md" "$TG_BENCH/toolz__dict-helpers/instruction.md"
python3 "$HERE/tgm.py" sync toolz__dict-helpers > /dev/null
python3 "$HERE/tgm.py" prepare toolz__dict-helpers
python3 "$HERE/tgm.py" solution toolz__dict-helpers
if [ "${1:-}" = --docker ]; then
  python3 "$HERE/tgm.py" docker toolz__dict-helpers ${TG_CA:+--ca "$TG_CA"}
fi
