
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

## Your job: fix task picomatch__glob-matching (javascript) after review
Review findings: "<paste the review result JSON here>"
Fix every blocking issue (and minor ones when cheap). If a mutant or decoy cannot be made valid, replace it with a new
valid one so the task keeps >= 12 mutants and >= 4 decoys with the required mix. Keep the reference suite catching all
mutants and passing all decoys. Re-validate: `tgm.py prepare`, `tgm.py solution` reward 1 three times, `tgm.py check`
problems [], docker_reward "1". Update ridges_hard/_notes/picomatch__glob-matching.md. Clean up as the guide says.
Return the structured result describing the FINAL state.