export const meta = {
  name: 'ridges-hard-tasks',
  description: 'Build hard multi-language test-generation tasks: language runner + pilot, build, blind probe, harden, adversarial review, fix',
  phases: [
    { title: 'Framework', detail: 'per-language runner templates + pilot task' },
    { title: 'Build', detail: 'one agent per task' },
    { title: 'Probe', detail: 'blind competitor-style suite' },
    { title: 'Harden', detail: 'only when the probe solved the task' },
    { title: 'Review', detail: 'adversarial validity review' },
    { title: 'Fix', detail: 'only when review found blocking issues' },
  ],
}

// Settings (all optional, pass them in the Workflow tool's args next to "langs"):
//   repo     path of this git repository                     (default /home/user/ggbood)
//   samples  path of a ridges-bench clone (public samples)   (default /home/user/ridgesai/ridges-bench)
//   ca       CA bundle for docker builds behind a TLS-intercepting proxy (default: none)
//   jobs     max -j for tgm.py                                (default 2)
const REPO = args.repo || '/home/user/ggbood'
const SAMPLES = args.samples || '/home/user/ridgesai/ridges-bench'
const CA = args.ca ? ` --ca ${args.ca}` : ''
const JOBS = args.jobs || 2
const ENV = `export TG_BENCH=${REPO}/ridges_hard TG_ROOT=/opt/tgm; cd ${REPO}`

const COMMON = `
## Context
You are helping build a set of 35 HARD practice tasks for the Ridges "test-generation" category (Bittensor subnet 62)
in many programming languages. The user will run AI agents against them on their own server (with Docker, like the
Ridges CLI, or with harness/tgm.py). Everything lives in the git repository ${REPO}. Do NOT git commit or
push; the coordinator does that.

Read first:
- ${REPO}/harness/TASK_AUTHORING.md  (rules, difficulty bar, tooling: harness/tgm.py)
- the public sample ${SAMPLES}/test-generation/tenacity__retry-strategies/ (instruction.md,
  tests/*.patch, solution/solve.sh) — your task must look and read like it, only harder.

Always run: ${ENV}
The machine is shared with many other agents: use -j ${JOBS} at most (less for compiled languages) and
\`nice -n 10\` for docker and heavy builds. Disk is limited: clean up as the guide says.
`

const BUILD_SCHEMA = {
  type: 'object',
  properties: {
    task: { type: 'string', description: 'final task directory name under ridges_hard/' },
    language: { type: 'string' },
    library: { type: 'string' },
    version: { type: 'string' },
    commit: { type: 'string' },
    used_alternate: { type: 'boolean' },
    mutants: { type: 'integer' },
    decoys: { type: 'integer' },
    solution_reward: { type: 'integer', description: 'reward of tgm.py solution on the final files' },
    solution_runs_all_reward1: { type: 'boolean', description: 'three consecutive solution runs all reward 1' },
    check_problems: { type: 'array', items: { type: 'string' } },
    docker_reward: { type: 'string', description: 'docker_reward from tgm.py docker on the final files, or "not run: <why>"' },
    max_cell_secs: { type: 'number' },
    summary: { type: 'string', description: 'scope and what makes it hard, 3-6 sentences' },
    concerns: { type: 'string', description: 'anything that is not fully right; empty if none' },
  },
  required: ['task', 'language', 'library', 'commit', 'mutants', 'decoys', 'solution_reward', 'solution_runs_all_reward1', 'docker_reward', 'summary', 'concerns'],
}

const FRAMEWORK_SCHEMA = {
  type: 'object',
  properties: {
    ...BUILD_SCHEMA.properties,
    template_ok: { type: 'boolean', description: 'the language template works locally AND in docker on the pilot' },
    template_notes: { type: 'string', description: 'conventions: test file names, how tests import the library, runner variables, cell times' },
  },
  required: [...BUILD_SCHEMA.required, 'template_ok', 'template_notes'],
}

const PROBE_SCHEMA = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    passed_original: { type: 'boolean' },
    reward: { type: 'integer' },
    tests: { type: 'integer' },
    mutants_caught: { type: 'string' },
    decoys_passed: { type: 'string' },
    missed_mutants: { type: 'array', items: { type: 'string' } },
    failed_decoys: { type: 'array', items: { type: 'string' } },
    comments: { type: 'string', description: 'anything in the instruction that was unclear or seemed wrong' },
  },
  required: ['task', 'passed_original', 'reward', 'mutants_caught', 'decoys_passed', 'missed_mutants', 'failed_decoys', 'comments'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    verdict: { type: 'string', enum: ['ok', 'needs_fix'] },
    blocking: {
      type: 'array',
      items: { type: 'object', properties: { item: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['item', 'problem', 'fix'] },
    },
    minor: {
      type: 'array',
      items: { type: 'object', properties: { item: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['item', 'problem', 'fix'] },
    },
  },
  required: ['task', 'verdict', 'blocking', 'minor'],
}

function specText(s) {
  return `Language: ${s.lang}
Library: ${s.library} — ${s.url}
Suggested task name: ${s.name}
Category/type: ${s.type}
Scope idea: ${s.focus}
Alternate library if the primary does not work: ${s.alt}`
}

const BUILD_STEPS = `
Steps (see TASK_AUTHORING.md for every rule):
1. Clone the library somewhere scratch (e.g. /opt/tgm/scratch-<name>), pick the commit of its latest release tag (full
   40-hex sha), check it works with the language runner (deps available, builds/imports offline after prepare).
2. Study the library, its docs and docstrings deeply. Choose a broad but cohesive public API scope.
3. \`python3 harness/tgm.py new <TASK> --lang <lang> --url <url> --sha <sha> [--var ...] --desc "..."\`
4. Write instruction.md (sample style; fill the language Environment/hygiene section; be precise about scope and
   about what is out of contract).
5. Write the reference suite in solution/solve.sh (heredocs). Record expected values by running the library.
6. Write 12-16 mutants and 4-5 decoys with \`tgm.py edit\` / \`tgm.py patch\`, meeting the required mix.
7. \`tgm.py prepare\`, then \`tgm.py solution\` until reward 1; then run solution two more times (all reward 1).
   Check every mutant is killed for the intended reason and every decoy passes. Also sanity-check that the decoys
   really would break an over-specified suite (e.g. a test asserting a message text fails on the decoy).
8. \`tgm.py check <TASK>\` -> problems [] ; \`nice -n 10 python3 harness/tgm.py docker <TASK>${CA}\`
   -> docker_reward "1".
9. Write ridges_hard/_notes/<TASK>.md. Clean up: rm -rf /opt/tgm/<TASK>/matrix /opt/tgm/<TASK>/edit (keep clone).
Return the structured result describing the FINAL state.`

function buildPrompt(s) {
  return `${COMMON}
## Your job: build ONE complete task
${specText(s)}

The language runner template is harness/tgm_templates/${s.lang}/ (read its README.md and instruction_env.md: they
explain test-file conventions, how tests import the library and the runner variables for --var). Do not edit it; if
it is broken for your library, report that in "concerns" and work around it only inside your task's tests/ files.
${BUILD_STEPS}`
}

const LANG_HINTS = {
  javascript: `Base image: node:22 (Debian; has git and python3; no apt-get needed). Node 22 is installed locally (/opt/node22).
Suggested runner: \`node --test --test-reporter=junit --test-reporter-destination=$OUT\` over regression_tests/**/*.test.{js,mjs,cjs}
(make the file glob explicit; node's default glob may pick other files). Tests import the library by a relative path from
regression_tests/ (e.g. require('../index.js')) or by the package's own name if its package.json "exports" allows
self-reference — document one way. Library dependencies (if any) are installed by prepare_state.sh (npm ci / npm install
--omit=dev --ignore-scripts) into the copy; cells are offline and the repo copy is read-only.`,
  typescript: `Base image: node:22 (has git, python3). Tests are TypeScript files regression_tests/**/*.test.ts run with
node --test through a TS loader: install tsx (pin a version) into $TG_TOOLS (default /opt/tgm-tools; base.Dockerfile must
install it at the same path and set ENV TG_TOOLS) and load it with --import by absolute path. Tests import the library's
TypeScript sources by relative path (e.g. '../src/index.ts' or what tsx resolves). Library deps via prepare_state.sh.
Coordinate nothing with the JavaScript agent; keep your own template dir (copy ideas freely).`,
  go: `Base image: golang:1.24 (has git, python3). Go 1.24 locally. Tests live in regression_tests/ as an external test
package (e.g. \`package regression_tests\`, files *_test.go) that imports the library by its module path; run
\`go test -json ./regression_tests/...\` from the module root and convert to JUnit with a python3 helper. Module deps:
prepare_state.sh downloads/vendors them (e.g. \`go mod vendor\` in the copy, or a GOMODCACHE inside the copy); cells run
offline (GOPROXY=off, GOFLAGS=-mod=vendor or -mod=mod with the prepared cache), GOCACHE under $HOME. Go 1.24's
GOTOOLCHAIN must not try to download (set GOTOOLCHAIN=local). Compile errors / no tests -> exit 2.`,
  rust: `Base image: rust:1.88 (has git, python3). Rust 1.97 locally (RUSTUP_HOME=/root/.rustup is passed to cells).
The repo copy is read-only, so do not put files into the crate: e.g. generate a wrapper crate in $HOME whose
Cargo.toml depends on the library by path and declares [[test]] targets pointing at $REPO/regression_tests/*.rs, with
CARGO_TARGET_DIR under $HOME, offline, dependencies vendored by prepare_state.sh (cargo vendor + source replacement
config) — or any equivalent design. Prebuild what you can at prepare time so cells are fast. Parse libtest output
(\`test path ... ok|FAILED\`) into JUnit with a python3 helper. Tests may use only std and the library itself.
Compile errors -> exit 2. Set CELL_TIMEOUT generously (e.g. 240) but keep cells fast.`,
  java: `Base image: maven:3.9-eclipse-temurin-21 (Ubuntu 24.04; has git; NO python3 -> \`apt-get install -y python3\`). JDK 21 locally. Tests are JUnit 5 (Jupiter) sources under
regression_tests/ (any package), compiled against the library's classes; run with junit-platform-console-standalone
(download a pinned jar from Maven Central into $TG_TOOLS at prepare time; base.Dockerfile puts it at the same path and
sets ENV TG_TOOLS). Compile the library once per copy in prepare_state.sh (plain javac over the source root given by a
runner variable such as JAVA_SRC, or mvn -o -q compile), so cells only compile the tests. Use the console launcher's
XML reports. Compile errors -> exit 2.`,
  ruby: `Base image: ruby:3.3 (has git, python3). Ruby 3.3 locally (/opt/ruby-3.3.6). Tests are Minitest files
regression_tests/**/*_test.rb (or test_*.rb) run by a helper script of your template that loads minitest, adds the
library's lib/ to $LOAD_PATH, requires the test files and writes JUnit XML through a small custom reporter. Gem
dependencies of the library are installed by prepare_state.sh into a directory inside the copy (GEM_HOME/GEM_PATH) so
cells run offline. Load errors / no tests -> exit 2.`,
  php: `Base image: composer:2 (Alpine; has php, composer, git, python3). PHP 8.3 + composer locally.
prepare_state.sh runs composer install in the copy (network allowed at prepare time). PHPUnit: use the library's own require-dev PHPUnit if it has one, otherwise install a pinned PHPUnit into
$TG_TOOLS. Tests are PHPUnit test classes regression_tests/**/*Test.php run with --no-configuration, the composer
autoloader as bootstrap and --log-junit. Make sure exit codes map to 0/1/other as required.`,
  c: `Base image: gcc:14 (has git, python3, make, cmake). gcc 13 locally. Suggested convention: every
regression_tests/test_*.c is a standalone program with its own main() that returns 0 when all its checks pass;
the runner compiles each one together with the library sources (runner variables such as C_SOURCES / CFLAGS /
INCLUDES in sample.env) into $HOME, runs each with a per-program timeout, and writes one JUnit testcase per file
(classname = file name). A test program that crashes or returns non-zero is a failed testcase; a test file that does
not compile -> exit 2 (no verdict). Document that authors should split checks into several files for clarity.`,
  cpp: `Base image: gcc:14 (has git, python3, g++, cmake). g++ 13 locally. Same convention as the C runner but C++17
programs regression_tests/test_*.cpp compiled with g++ together with the library sources (runner variables in
sample.env). One JUnit testcase per file; crash/non-zero exit = failed testcase; compile error -> exit 2.`,
}

function frameworkPrompt(lang, s) {
  return `${COMMON}
## Your job: (1) create the ${lang} language runner template, then (2) build the first ${lang} task with it.

### (1) Template: harness/tgm_templates/${lang}/
Read harness/tgm.py and the Python template harness/tgm_templates/python/ first: the contract is in tgm.py's docstring.
Create in harness/tgm_templates/${lang}/:
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
- README.md — conventions: test file names, how tests import the library, runner variables for \`tgm.py new --var\`,
  typical cell time.
- any helper scripts (they are copied to tests/ and environment/ automatically; refer to them via $TG_TESTS_DIR).
Language hints: ${LANG_HINTS[lang]}
Validate the runner hard: a passing suite -> 0, a failing test -> 1, a compile/load error -> not 0/1, an empty suite ->
not 0/1, stable ids across two runs, works as nobody offline, and in docker via the task's images.
You may make small, backward-compatible fixes to harness/tgm.py if something generic is missing (say so in
template_notes); never break the Python template (re-run \`bash harness/tgm_selftest.sh\` -> reward 1).

### (2) Pilot task (a full, hard task like every other)
${specText(s)}
${BUILD_STEPS}
Set template_ok=true only if the template works locally and in docker on this task.`
}

function probePrompt(b) {
  return `${COMMON}
## Your job: blind probe of task ${b.task} (${b.language})
You simulate a strong competitor agent. Measure how hard the task is by writing the best regression suite you can
from the instruction alone.

You may read ONLY: ${REPO}/ridges_hard/${b.task}/instruction.md; the pristine repository
/opt/tgm/${b.task}/clone (copy it to /opt/tgm/${b.task}/probe_work and experiment there: run the library, read its
docs, source and own tests); harness/tgm_templates/${b.language}/README.md and instruction_env.md (how suites are run).
FORBIDDEN (reading any of these invalidates the measurement): ridges_hard/${b.task}/tests/, ridges_hard/${b.task}/solution/,
ridges_hard/_notes/, /opt/tgm/${b.task}/matrix, /opt/tgm/${b.task}/edit, /opt/tgm/${b.task}/cells, any other task's files,
and git history/diffs of ${REPO}.

Write the suite into /opt/tgm/${b.task}/probe_suite/ (its contents are what would be submitted as regression_tests/).
Work like a careful competitor with ~30-40 minutes: pin down observable behavior including edge and boundary cases,
options and their interactions, errors, state; respect the "Not part of the contract" list. Make sure it passes on the
library as it is (run it in your probe_work copy with the language's normal tooling).
Then run EXACTLY ONCE:
  python3 harness/tgm.py prepare -j ${JOBS} ${b.task} && python3 harness/tgm.py suite -j ${JOBS} ${b.task} /opt/tgm/${b.task}/probe_suite
Do not change the suite after seeing the result. Report the result (missed mutant names, failed decoys) and any
instruction text that was unclear or seemed wrong.`
}

function hardenPrompt(b, p) {
  return `${COMMON}
## Your job: make task ${b.task} harder
A blind probe suite (/opt/tgm/${b.task}/probe_suite, written from the instruction alone) solved the task or caught every
mutant: ${JSON.stringify(p)}.
Add at least 3 NEW mutants that the probe suite does NOT catch but the reference suite does (extend the reference suite
if needed), and replace weak existing mutants (ones any smoke test catches) so that the total stays 12-16. Every mutant
must still satisfy TASK_AUTHORING.md section 1 (in-contract per the instruction, deterministic, non-equivalent, small;
not only visible through excluded aspects). Do not change the instruction to make it vaguer; you may add precise
in-scope items if a new mutant needs them, but then re-check every decoy against the new scope.
Verify: \`tgm.py suite -j ${JOBS} ${b.task} /opt/tgm/${b.task}/probe_suite\` no longer catches all mutants; \`tgm.py solution\`
reward 1 three times; \`tgm.py check\`; docker_reward "1". Update ridges_hard/_notes/${b.task}.md. Clean up as the guide says.
Return the structured result describing the FINAL state.`
}

function reviewPrompt(b, p, h) {
  return `${COMMON}
## Your job: adversarial review of task ${b.task} (${b.language}). Do NOT modify any task file.
Builder's report: ${JSON.stringify(h || b)}
Blind probe result: ${JSON.stringify(p)}

Read everything in ridges_hard/${b.task}/ and ridges_hard/_notes/${b.task}.md. Run \`tgm.py prepare -j ${JOBS}\`,
\`tgm.py solution -j ${JOBS}\` and \`tgm.py check\` yourself. Then judge, item by item:
- Each mutant: (a) is the changed behavior inside the contract the instruction defines (not only visible through
  message text, string representations, excluded features, timing, randomness, private names)? (b) deterministic and
  observable through the public API? (c) not equivalent to the original on every input? (d) not so blatant that any
  smoke test catches it? (e) does it compile/import?
- Each decoy: does it change ANY in-scope behavior for ANY input? Do not trust the reference suite: write a quick
  differential test (original vs decoy library copies in /opt/tgm/${b.task}/matrix/<state>/repo, many inputs incl. edge
  cases, errors raised or not) for every decoy. Is everything it changes listed under "Not part of the contract"?
- Instruction: correct version/commit/paths; scope precise and consistent with the mutants (every mutant's behavior is
  clearly in scope) and decoys; no hints of mutants; Environment/hygiene section correct for this language and actually
  true for the images and runner; nothing promised that is not provided.
- Difficulty: at least 12 mutants and 4 decoys, required category mix (TASK_AUTHORING.md section 1).
- If the probe failed on the original library or on a decoy, decide whether that was the probe's fault (over-
  specification, which is intended) or a defect of the task (ambiguous/misleading instruction, a decoy that changes
  in-scope behavior, nondeterminism).
Report blocking issues (would make the task unfair, unsolvable, invalid or below the bar) and minor ones, each with a
concrete fix. verdict "ok" only if there are no blocking issues.`
}

function fixPrompt(b, r) {
  return `${COMMON}
## Your job: fix task ${b.task} (${b.language}) after review
Review findings: ${JSON.stringify(r)}
Fix every blocking issue (and minor ones when cheap). If a mutant or decoy cannot be made valid, replace it with a new
valid one so the task keeps >= 12 mutants and >= 4 decoys with the required mix. Keep the reference suite catching all
mutants and passing all decoys. Re-validate: \`tgm.py prepare\`, \`tgm.py solution\` reward 1 three times, \`tgm.py check\`
problems [], docker_reward "1". Update ridges_hard/_notes/${b.task}.md. Clean up as the guide says.
Return the structured result describing the FINAL state.`
}

async function taskChain(spec, buildThunk) {
  const b = await buildThunk()
  if (!b || !b.task) return { spec: spec.name, language: spec.lang, status: 'build_failed', build: b }
  const p = await agent(probePrompt(b), { schema: PROBE_SCHEMA, label: `probe:${b.task}`, phase: 'Probe' })
  let h = null
  if (p && p.passed_original && (p.reward === 1 || (p.missed_mutants || []).length === 0)) {
    h = await agent(hardenPrompt(b, p), { schema: BUILD_SCHEMA, label: `harden:${b.task}`, phase: 'Harden' })
  }
  const r = await agent(reviewPrompt(b, p, h), { schema: REVIEW_SCHEMA, label: `review:${b.task}`, phase: 'Review' })
  let f = null
  if (r && (r.verdict === 'needs_fix' || (r.blocking || []).length)) {
    f = await agent(fixPrompt(b, r), { schema: BUILD_SCHEMA, label: `fix:${b.task}`, phase: 'Fix' })
  }
  return { task: b.task, language: spec.lang, build: b, probe: p, harden: h, review: r, fix: f }
}

async function langChain(L) {
  const fw = L.framework
    ? agent(frameworkPrompt(L.lang, L.tasks[0]), { schema: FRAMEWORK_SCHEMA, label: `framework:${L.lang}`, phase: 'Framework' })
    : Promise.resolve(null)
  const chains = L.tasks.map((spec, i) => {
    if (L.framework && i === 0) return taskChain(spec, () => fw)
    return (async () => {
      if (L.framework) {
        const done = await fw
        if (!done || !done.template_ok) return { spec: spec.name, language: L.lang, status: 'skipped: framework failed', framework: done }
      }
      return taskChain(spec, () => agent(buildPrompt(spec), { schema: BUILD_SCHEMA, label: `build:${spec.name}`, phase: 'Build' }))
    })()
  })
  return Promise.all(chains)
}

const results = (await Promise.all(args.langs.map(langChain))).flat()
log(`finished ${results.length} task chains`)
return results
