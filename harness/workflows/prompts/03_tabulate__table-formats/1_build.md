
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

## Your job: build ONE complete task
Language: python
Library: python-tabulate — https://github.com/astanin/python-tabulate
Suggested task name: tabulate__table-formats
Category/type: text formatting / rendering
Scope idea: tabulate() with many tablefmt values, headers (firstrow, keys, dict/list inputs, dataclasses), numalign/stralign/colalign, floatfmt/intfmt per column, missingval, disable_numparse, showindex, maxcolwidths/maxheadercolwidths wrapping, multiline cells, rowalign, simple_separated_format
Alternate library if the primary does not work: texttable (https://github.com/foutaise/texttable)

The language runner template is harness/tgm_templates/python/ (read its README.md and instruction_env.md: they
explain test-file conventions, how tests import the library and the runner variables for --var). Do not edit it; if
it is broken for your library, report that in "concerns" and work around it only inside your task's tests/ files.

Steps (see TASK_AUTHORING.md for every rule):
1. Clone the library somewhere scratch (e.g. /opt/tgm/scratch-<name>), pick the commit of its latest release tag (full
   40-hex sha), check it works with the language runner (deps available, builds/imports offline after prepare).
2. Study the library, its docs and docstrings deeply. Choose a broad but cohesive public API scope.
3. `python3 harness/tgm.py new <TASK> --lang <lang> --url <url> --sha <sha> [--var ...] --desc "..."`
4. Write instruction.md (sample style; fill the language Environment/hygiene section; be precise about scope and
   about what is out of contract).
5. Write the reference suite in solution/solve.sh (heredocs). Record expected values by running the library.
6. Write 12-16 mutants and 4-5 decoys with `tgm.py edit` / `tgm.py patch`, meeting the required mix.
7. `tgm.py prepare`, then `tgm.py solution` until reward 1; then run solution two more times (all reward 1).
   Check every mutant is killed for the intended reason and every decoy passes. Also sanity-check that the decoys
   really would break an over-specified suite (e.g. a test asserting a message text fails on the decoy).
8. `tgm.py check <TASK>` -> problems [] ; `nice -n 10 python3 harness/tgm.py docker <TASK>`
   -> docker_reward "1".
9. Write ridges_hard/_notes/<TASK>.md. Clean up: rm -rf /opt/tgm/<TASK>/matrix /opt/tgm/<TASK>/edit (keep clone).
Return the structured result describing the FINAL state.