
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

## Your job: make task voluptuous__schema-validation harder
A blind probe suite (/opt/tgm/voluptuous__schema-validation/probe_suite, written from the instruction alone) solved the task or caught every
mutant: "<paste the probe result JSON here>".
Add at least 3 NEW mutants that the probe suite does NOT catch but the reference suite does (extend the reference suite
if needed), and replace weak existing mutants (ones any smoke test catches) so that the total stays 12-16. Every mutant
must still satisfy TASK_AUTHORING.md section 1 (in-contract per the instruction, deterministic, non-equivalent, small;
not only visible through excluded aspects). Do not change the instruction to make it vaguer; you may add precise
in-scope items if a new mutant needs them, but then re-check every decoy against the new scope.
Verify: `tgm.py suite -j 4 voluptuous__schema-validation /opt/tgm/voluptuous__schema-validation/probe_suite` no longer catches all mutants; `tgm.py solution`
reward 1 three times; `tgm.py check`; docker_reward "1". Update ridges_hard/_notes/voluptuous__schema-validation.md. Clean up as the guide says.
Return the structured result describing the FINAL state.