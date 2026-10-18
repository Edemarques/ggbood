
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

## Your job: (1) create the rust language runner template, then (2) build the first rust task with it.

### (1) Template: harness/tgm_templates/rust/
Read harness/tgm.py and the Python template harness/tgm_templates/python/ first: the contract is in tgm.py's docstring.
Create in harness/tgm_templates/rust/:
- run_cell.sh REPO OUT_XML — runs as user nobody, cwd=REPO, REPO read-only, HOME=TMPDIR=a writable scratch dir, offline,
  env from tests/sample.env plus TG_TESTS_DIR (dir holding the template's runner files) and TG_TOOLS. Runs the files in
  REPO/regression_tests and writes JUnit XML with one <testcase classname=... name=...> per test (ids must be stable
  across runs: no timings/random/paths of temp dirs in ids). Exit 0 = all passed, 1 = some failed, anything else = no
  verdict (build/compile error, nothing collected, crash of the runner itself).
- prepare_state.sh REPO — runs as root with network for every library copy (pristine, each mutant, each decoy) and for
  /app in the agent image; installs dependencies, prebuilds, may fill $TG_TOOLS if missing. Must be idempotent.
- base.Dockerfile — FROM + toolchain lines shared by the verifier and agent images (see the language hints); prefer official
  images that already ship git+python3 (fewer apt steps, builds behind restrictive proxies). Set ENV TG_TOOLS if you use it.
- env.Dockerfile (optional) — extra ENV lines for the agent image.
- instruction_env.md — the "## Environment" and "## Test hygiene and CI requirements" sections for this language,
  mirroring the Python template's rules (regression_tests only, standalone, offline, unprivileged, read-only library
  copy, TMPDIR/HOME, file naming, no work at load time where relevant, stable test set, determinism, runtime limit),
  with placeholders like {LIB} that tgm.py fills from sample.env.
- README.md — conventions: test file names, how tests import the library, runner variables for `tgm.py new --var`,
  typical cell time.
- any helper scripts (they are copied to tests/ and environment/ automatically; refer to them via $TG_TESTS_DIR).
Language hints: Base image: rust:1.88 (has git, python3). Rust 1.97 locally (RUSTUP_HOME=/root/.rustup is passed to cells).
The repo copy is read-only, so do not put files into the crate: e.g. generate a wrapper crate in $HOME whose
Cargo.toml depends on the library by path and declares [[test]] targets pointing at $REPO/regression_tests/*.rs, with
CARGO_TARGET_DIR under $HOME, offline, dependencies vendored by prepare_state.sh (cargo vendor + source replacement
config) — or any equivalent design. Prebuild what you can at prepare time so cells are fast. Parse libtest output
(`test path ... ok|FAILED`) into JUnit with a python3 helper. Tests may use only std and the library itself.
Compile errors -> exit 2. Set CELL_TIMEOUT generously (e.g. 240) but keep cells fast.
Validate the runner hard: a passing suite -> 0, a failing test -> 1, a compile/load error -> not 0/1, an empty suite ->
not 0/1, stable ids across two runs, works as nobody offline, and in docker via the task's images.
You may make small, backward-compatible fixes to harness/tgm.py if something generic is missing (say so in
template_notes); never break the Python template (re-run `bash harness/tgm_selftest.sh` -> reward 1).

### (2) Pilot task (a full, hard task like every other)
Language: rust
Library: shlex (comex/rust-shlex) — https://github.com/comex/rust-shlex
Suggested task name: shlex__shell-lexing
Category/type: parser / lexing and quoting
Scope idea: split, Shlex iterator (line_no, had_error), quote/try_quote/join/try_join and the Quoter builder (allow_nul etc.), QuoteError variants, the bytes module equivalents; comments, escapes inside double/single quotes, newlines, non-ASCII, empty strings
Alternate library if the primary does not work: unicode-segmentation (https://github.com/unicode-rs/unicode-segmentation)

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
Set template_ok=true only if the template works locally and in docker on this task.