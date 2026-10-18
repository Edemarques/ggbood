#!/bin/bash
# Multi-language verifier for test-generation tasks (same rules as the public ridges-bench samples).
# Runs the submitted regression suite against the unmodified library and against modified copies:
#   - mutants: small behavioral bugs; the suite must FAIL on each one;
#   - decoys:  behavior-preserving rewrites; the suite must PASS on each one.
# Reward is 1 only if the suite passes on the original, passes on every decoy and fails on every mutant.
# The language-specific part lives in /tests/run_cell.sh (REPO OUT_JUNIT; exit 0 = all passed, 1 = some failed,
# anything else = no verdict) and /tests/prepare_state.sh (run at image build time for every library copy).
set -uo pipefail

# shellcheck source=/dev/null
set -a; source /tests/sample.env; set +a   # LANGUAGE, MUTANTS, DECOYS, CELL_TIMEOUT, runner settings
CELL_TIMEOUT="${CELL_TIMEOUT:-60}"
export TG_TESTS_DIR=/tests

mkdir -p /logs/verifier
chmod 700 /logs /logs/verifier
CELLS=/var/lib/verifier-cells
rm -rf "$CELLS"; mkdir -m 700 -p "$CELLS"
rm -f /logs/verifier/junit.xml
echo 0 > /logs/verifier/reward.txt

cd /app
if [ ! -s /logs/agent/patch.diff ]; then
  echo "submitted patch is missing or empty" >&2
  exit 1
fi
cp /logs/agent/patch.diff /logs/verifier/graded.patch
git apply --check /logs/verifier/graded.patch || exit 1

# Only files under regression_tests/ may be added or changed.
badpath=0
while IFS=$'\t' read -r -d '' _added _removed path; do
  case "$path" in
    regression_tests/*) ;;
    *) echo "path outside regression_tests: $path" >&2; badpath=1 ;;
  esac
done < <(git apply --numstat -z /logs/verifier/graded.patch)
[ "$badpath" -eq 0 ] || exit 1
git apply /logs/verifier/graded.patch || exit 1

# The suite runs as an unprivileged user and sees only its own copy of the library.
chmod -R go-rwx /matrix /app 2>/dev/null || true
chmod -R a+rX /tests; chmod 755 /tests/*.sh

declare -A CELL_RC
run_cell() {
  local state="$1"
  CELL_RC[$state]=invalid
  rm -rf /work
  find -P /tmp /var/tmp /dev/shm -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null || true
  mkdir -p /work/tmp /work/out
  cp -a "/matrix/${state}/repo" /work/repo
  rm -rf /work/repo/regression_tests
  cp -r /app/regression_tests /work/repo/regression_tests
  chown -R root:root /work/repo
  chmod -R a+rX,go-w /work/repo
  chown -R nobody /work/tmp /work/out
  chmod 755 /work
  timeout -k 5 "$CELL_TIMEOUT" su -s /bin/bash nobody -c \
    "cd /work/repo && HOME=/work/tmp TMPDIR=/work/tmp /tests/run_cell.sh /work/repo /work/out/junit.xml" > /dev/null 2>&1
  local rc=$?
  CELL_RC[$state]=$rc
  su -s /bin/bash nobody -c "kill -9 -1" > /dev/null 2>&1 || true
  [ -f /work/out/junit.xml ] && cp /work/out/junit.xml "$CELLS/cell-${state}.xml"
  return $rc
}

cell_counts() { python3 /tests/junit_summary.py "$CELLS/cell-$1.xml" 2>/dev/null; }

declare -A verdict
classify() { # state, expected (green|red)
  local state="$1" want="$2" t f ids rc
  rc="${CELL_RC[$state]:-}"
  # Only a normal runner exit is a verdict: 0 (all passed) or 1 (tests failed).
  if [ "$rc" != 0 ] && [ "$rc" != 1 ]; then verdict[$state]=invalid; return; fi
  read -r t f ids <<< "$(cell_counts "$state")"
  if [ -z "${t:-}" ] || [ "$t" != "$BASE_TESTS" ] || [ "${ids:-}" != "$BASE_IDS" ]; then
    verdict[$state]=invalid; return
  fi
  if [ "$want" = green ]; then
    [ "$rc" = 0 ] && [ "$f" -eq 0 ] && verdict[$state]=ok || verdict[$state]=wrong
  else
    [ "$f" -ge 1 ] && verdict[$state]=ok || verdict[$state]=wrong
  fi
}

run_cell pristine
read -r BASE_TESTS _ BASE_IDS <<< "$(cell_counts pristine)"
if [ -z "${BASE_TESTS:-}" ] || [ "$BASE_TESTS" -lt 1 ]; then
  echo "no valid test report on the original library" >&2
  exit 1
fi
classify pristine green
for s in $(printf '%s\n' $DECOYS $MUTANTS | shuf); do run_cell "$s"; done
for s in $DECOYS; do classify "$s" green; done
for s in $MUTANTS; do classify "$s" red; done

cp "$CELLS"/cell-*.xml /logs/verifier/ 2>/dev/null || true

failures=0; rows=""
row() { # name, state
  local v="${verdict[$2]}"
  if [ "$v" = ok ]; then
    rows="${rows}  <testcase classname=\"matrix\" name=\"$1\"/>"$'\n'
  else
    failures=$((failures + 1))
    rows="${rows}  <testcase classname=\"matrix\" name=\"$1\"><failure message=\"cell verdict $v\"/></testcase>"$'\n'
  fi
}
row original_passes pristine
for s in $DECOYS; do row "${s}_passes" "$s"; done
for s in $MUTANTS; do row "${s}_caught" "$s"; done
n=$(printf '%s' "$rows" | grep -c '<testcase')
{
  echo '<?xml version="1.0" encoding="utf-8"?>'
  printf '<testsuite name="sample-matrix" tests="%d" failures="%d" errors="0" skipped="0">\n' "$n" "$failures"
  printf '%s' "$rows"
  echo '</testsuite>'
} > /logs/verifier/junit.xml

if [ "$failures" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; exit 0; fi
exit 1
