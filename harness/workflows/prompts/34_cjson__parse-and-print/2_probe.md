
## Context
You are helping build a set of 35 HARD practice tasks for the Ridges "test-generation" category (Bittensor subnet 62)
in many programming languages. The user will run AI agents against them on their own server (with Docker, like the
Ridges CLI, or with harness/tgm.py). Everything lives in the git repository /root/ggbood. Do NOT git commit or
push; the coordinator does that.

Read first:
- /root/ggbood/harness/TASK_AUTHORING.md  (rules, difficulty bar, tooling: harness/tgm.py)
- the public sample /root/ridges-bench/test-generation/tenacity__retry-strategies/ (instruction.md,
  tests/*.patch, solution/solve.sh) — your task must look and read like it, only harder.

Always run: export TG_BENCH=/root/ggbood/ridges_hard TG_ROOT=/opt/tgm; cd /root/ggbood
The machine is shared with many other agents: use -j 4 at most (less for compiled languages) and
`nice -n 10` for docker and heavy builds. Disk is limited: clean up as the guide says.

## Your job: blind probe of task cjson__parse-and-print (c)
You simulate a strong competitor agent. Measure how hard the task is by writing the best regression suite you can
from the instruction alone.

You may read ONLY: /root/ggbood/ridges_hard/cjson__parse-and-print/instruction.md; the pristine repository
/opt/tgm/cjson__parse-and-print/clone (copy it to /opt/tgm/cjson__parse-and-print/probe_work and experiment there: run the library, read its
docs, source and own tests); harness/tgm_templates/c/README.md and instruction_env.md (how suites are run).
FORBIDDEN (reading any of these invalidates the measurement): ridges_hard/cjson__parse-and-print/tests/, ridges_hard/cjson__parse-and-print/solution/,
ridges_hard/_notes/, /opt/tgm/cjson__parse-and-print/matrix, /opt/tgm/cjson__parse-and-print/edit, /opt/tgm/cjson__parse-and-print/cells, any other task's files,
and git history/diffs of /root/ggbood.

Write the suite into /opt/tgm/cjson__parse-and-print/probe_suite/ (its contents are what would be submitted as regression_tests/).
Work like a careful competitor with ~30-40 minutes: pin down observable behavior including edge and boundary cases,
options and their interactions, errors, state; respect the "Not part of the contract" list. Make sure it passes on the
library as it is (run it in your probe_work copy with the language's normal tooling).
Then run EXACTLY ONCE:
  python3 harness/tgm.py prepare -j 4 cjson__parse-and-print && python3 harness/tgm.py suite -j 4 cjson__parse-and-print /opt/tgm/cjson__parse-and-print/probe_suite
Do not change the suite after seeing the result. Report the result (missed mutant names, failed decoys) and any
instruction text that was unclear or seemed wrong.