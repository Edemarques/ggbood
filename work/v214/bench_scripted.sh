#!/bin/bash
# usage: bench_scripted.sh AGENT.py OUTROOT CASESDIR [TASK...]  -- scripted run + verifier on each task
AGENT=$1; OUT=$2; CASES=$3; shift 3
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
B=${TG_BENCH:-/home/user/ridgesai/ridges-bench/test-generation}
TASKS=${@:-$(ls $B | grep __)}
mkdir -p $OUT
for t in $TASKS; do
  case $t in inflect__*) PY=/opt/venv313-inflect/bin/python;; python-slugify__*) PY=/opt/venv313-slugify/bin/python;; *) PY=/opt/venv313-base/bin/python;; esac
  AGENT_TIMEOUT=${AGENT_TIMEOUT:-900} $PY $HERE/run_scripted.py $AGENT $t $CASES/cases_$t.py $OUT/$t > $OUT/$t.log 2>&1
  r=$(TG_BENCH=$B python3 $REPO/harness/ridges_local.py verify $t $OUT/$t/patch.diff | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['reward'], d.get('mutants_caught'), d.get('problem') or '', [k for k,v in d['rows'].items() if v!='ok'])")
  echo "$t $(grep '^STATS' $OUT/$t.log | cut -c7-) VERIFY $r"
done
