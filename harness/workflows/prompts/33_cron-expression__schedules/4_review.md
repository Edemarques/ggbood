
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

## Your job: adversarial review of task cron-expression__schedules (php). Do NOT modify any task file.
Builder's report (paste it here): {"task":"cron-expression__schedules","language":"php"}
Blind probe result: "<paste the probe result JSON here>"

Read everything in ridges_hard/cron-expression__schedules/ and ridges_hard/_notes/cron-expression__schedules.md. Run `tgm.py prepare -j 4`,
`tgm.py solution -j 4` and `tgm.py check` yourself. Then judge, item by item:
- Each mutant: (a) is the changed behavior inside the contract the instruction defines (not only visible through
  message text, string representations, excluded features, timing, randomness, private names)? (b) deterministic and
  observable through the public API? (c) not equivalent to the original on every input? (d) not so blatant that any
  smoke test catches it? (e) does it compile/import?
- Each decoy: does it change ANY in-scope behavior for ANY input? Do not trust the reference suite: write a quick
  differential test (original vs decoy library copies in /opt/tgm/cron-expression__schedules/matrix/<state>/repo, many inputs incl. edge
  cases, errors raised or not) for every decoy. Is everything it changes listed under "Not part of the contract"?
- Instruction: correct version/commit/paths; scope precise and consistent with the mutants (every mutant's behavior is
  clearly in scope) and decoys; no hints of mutants; Environment/hygiene section correct for this language and actually
  true for the images and runner; nothing promised that is not provided.
- Difficulty: at least 12 mutants and 4 decoys, required category mix (TASK_AUTHORING.md section 1).
- If the probe failed on the original library or on a decoy, decide whether that was the probe's fault (over-
  specification, which is intended) or a defect of the task (ambiguous/misleading instruction, a decoy that changes
  in-scope behavior, nondeterminism).
Report blocking issues (would make the task unfair, unsolvable, invalid or below the bar) and minor ones, each with a
concrete fix. verdict "ok" only if there are no blocking issues.