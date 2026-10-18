# Authoring hard test-generation tasks (`ridges_hard/`)

These tasks follow the Ridges "test generation" category (public samples: `ridges-bench/test-generation`), but in
many languages and deliberately harder. An agent gets a repository at a fixed commit and an instruction that
defines a public API contract; it must write a regression suite under `regression_tests/`. The suite is graded
against the library as it is (must pass), against **decoys** (behavior-preserving rewrites; must pass) and against
**mutants** (small behavioral bugs; must fail). Reward is 1 only if all of that holds. No partial credit.

Read one public sample end to end before starting, e.g.
`<ridges-bench clone>/test-generation/tenacity__retry-strategies/` (`git clone https://github.com/ridgesai/ridges-bench`) (instruction.md, tests/*.patch,
solution/solve.sh). Your task must look and read like that sample, only harder.

## 1. Difficulty bar (must be clearly harder than the public samples)

The public samples have 2-8 mutants, 2-3 decoys and a narrow scope. Every task here must have:

- **12-16 mutants** (minimum 12) and **4-5 decoys** (minimum 4).
- A **broad but cohesive scope**: one public API area with many functions/options/flags and interactions between
  them (roughly 15-40 public entry points or option combinations), documented in the repo's docs/docstrings.
- Mutants that a competent but hurried suite misses. Required mix per task:
  - >= 3 boundary mutants: off-by-one, `<` vs `<=`, inclusive/exclusive ends, empty/one-element/limit inputs,
    exactly-at-threshold values, the last element/iteration, rounding at .5;
  - >= 2 interaction mutants: visible only when two documented options/features are combined;
  - >= 2 error-contract mutants: an invalid input that must raise now silently returns something, raises in a case
    that must succeed, or raises a *different documented* error class (only where the contract names the class);
  - >= 2 state/sequence mutants: second call, re-use after reset/clear, iteration after mutation, cache
    invalidation, order of callbacks/events where documented;
  - >= 1 default-value mutant: a documented default argument/option silently changed;
  - >= 1 mutant in a documented-but-rarely-tested corner (an option mentioned once in the docs, a docstring
    example, a flag combination in the changelog);
  - the rest: precision/rounding, unicode/whitespace/case, negative/huge/zero values, None/null handling.
- Every mutant must be: one small local change (usually 1-6 lines), still compile/import, change **in-contract**
  observable behavior **deterministically**, not be equivalent to the original, and not be detectable only through
  error-message text, `repr`/`toString`, timing, randomness, logging, private names or anything else the instruction
  excludes. No "everything breaks" mutants (broken import, every call raising, returning None everywhere): those are
  caught by any smoke test. Prefer mutants that keep the README examples passing.
- Decoys must preserve **every** in-scope behavior, and each must be designed to break a plausible
  over-specified suite. Use at least four different kinds across the decoys:
  1. reword error messages and change `str`/`repr`/`toString`/`Debug`/`inspect` output (declared out of contract);
  2. rename/restructure private helpers, internal attributes, module/file layout (keep the public import paths);
  3. change something the instruction explicitly declares unspecified (e.g. exception class for "assert only that
     it raises", order of an unordered result, warning text, log output, an excluded feature's behavior);
  4. an equivalent algorithm rewrite (different code shape, identical results on all in-scope inputs);
  5. caching/memoization/laziness that does not change results.
  A decoy may only change what "Not part of the contract" lists. Reason carefully about every input class, not just
  the reference suite: a decoy that changes in-scope behavior makes the task unsolvable.
- The reference suite (solution/solve.sh) must catch **all** mutants, pass on the original and **all** decoys,
  be deterministic (grade it 3 times), and run quickly (test run < 30 s; whole cell < CELL_TIMEOUT).

## 2. instruction.md

Mirror the tenacity sample's structure and tone:

- `# <Title>`, then an intro paragraph: library, version, short commit, which files the maintainers are about to
  refactor, what the suite is for.
- `## What the suite must do`: copy the two bullets and the "Cover normal behavior, edge and boundary cases, ..."
  sentence from the sample verbatim.
- `## Scope`: precise list of the in-contract API (functions, classes, methods, options, their arguments and
  defaults), where it is documented, how tests may call things (e.g. "pass your own clock"), what to import.
- `## Not part of the contract`: message texts and string representations; excluded features; anything
  nondeterministic; for errors outside the named classes, "assert that a call fails, not which error"; and the
  sample's paragraph "The documentation describes the intended behavior. Where the documentation disagrees ...
  current behavior governs ..." verbatim. Everything any decoy changes must be covered here.
- `## Environment` and `## Test hygiene and CI requirements`: start from the language template
  (`harness/tgm_templates/<lang>/instruction_env.md`, inserted by `tgm.py new`) and fill in the placeholders.
- Never hint at the mutants. No grading talk beyond what the sample says.

## 3. Choosing the library

- Public GitHub repository, pinned to a full 40-hex commit (prefer a release tag's commit; record the version).
- Not one of the public sample libraries (boltons, inflect, jmespath, loguru, natsort, python-slugify, tenacity,
  textdistance, toolz, tqdm). Each task in this set uses a different library.
- Runtime dependencies fetched by `prepare_state.sh` at prepare/build time; cells run **offline**. Prefer libraries
  with zero or few runtime dependencies. Python: never pytest's own dependencies (pytest, pluggy, iniconfig,
  packaging, pygments), they would shadow the runner.
- If the primary library does not work (import/build fails, deps unavailable, too small), use the alternate you
  were given, and say so in your result.

## 4. Tooling: `harness/tgm.py`

```bash
cd <this repository>
export TG_BENCH=$PWD/ridges_hard TG_ROOT=/opt/tgm
python3 harness/tgm.py new LIB__focus --lang LANG --url https://github.com/OWNER/REPO.git --sha <40-hex> \
    --var KEY=VALUE ... --desc "Write a regression test suite for ..."     # scaffold (runner vars: see template)
python3 harness/tgm.py edit TASK mutant_1        # prints an editable copy of the repo; edit files there
python3 harness/tgm.py patch TASK mutant_1       # -> tests/mutant_1.patch (+ sync)
python3 harness/tgm.py prepare -j 2 TASK         # build every library copy (cached per patch)
python3 harness/tgm.py suite -j 2 TASK DIR       # grade a directory of test files (while iterating)
python3 harness/tgm.py solution -j 2 TASK        # grade solution/solve.sh -> must be reward 1
python3 harness/tgm.py check TASK                # layout lint -> problems must be []
nice -n 10 python3 harness/tgm.py docker TASK   # real images -> docker_reward "1" (add --ca FILE behind a TLS-intercepting proxy)
```

`solution/solve.sh` writes the suite with one heredoc per file (`cat > regression_tests/x <<'EOF'`), like the
sample. Patches are `git apply -p1` patches against the repository root; always create them with `edit`/`patch`.
Re-run `prepare` after changing patches. `--keep` keeps cell directories (with `runner_output.txt`) for debugging.

## 5. Notes file (required)

Write `ridges_hard/_notes/<TASK>.md` (outside the task directory, never shipped to agents): library/version/commit,
scope summary, and a table of every mutant (file, one-line change, category from section 1, which reference test
kills it) and every decoy (what it changes, which over-specified assertion it defeats, why it is in-contract-safe).

## 6. Machine constraints

- Several agents usually share the machine: keep `-j` small (2-4), use `nice -n 10` for docker/compiles, one heavy job
  at a time per agent.
- Behind a restrictive proxy (e.g. the Claude cloud sandbox, where `deb.debian.org` is blocked), prefer base images that
  already ship git and python3, and pass `--ca <bundle>` to `tgm.py docker`. On a normal server none of this matters.
- Disk is limited: `tgm.py docker` removes its images afterwards; do not keep extra images or copies.
- Only touch your own task directory, its notes file and `/opt/tgm/<TASK>`. Do not edit `harness/tgm.py`,
  `harness/tgm_templates/` or other tasks (language-framework agents may edit their own language's template).
