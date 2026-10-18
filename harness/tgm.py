"""Multi-language test-generation tasks: run them locally, and author new ones.

A task follows the ridges-bench layout (instruction.md, task.toml, environment/, tests/, solution/). The grading
rule is the one of the public samples: the submitted suite (files under regression_tests/) must pass on the
library as it is, pass on every decoy (behavior-preserving rewrite) and fail on every mutant (small behavioral
bug); reward is 1 only if all of that holds. The language-specific part of a task is two scripts in tests/:
  prepare_state.sh REPO      run as root (network allowed) once per library copy, e.g. install dependencies;
  run_cell.sh REPO OUT_XML   run as `nobody` in REPO, offline, with HOME/TMPDIR pointing at a scratch dir;
                             runs regression_tests/ and writes a JUnit XML report; exit 0 = all passed,
                             1 = some failed, anything else = no verdict.
Every patch (tests/mutant_N.patch, tests/decoy_N.patch) is a `git apply -p1` patch against the repository root.
tests/sample.env holds LANGUAGE, MUTANTS, DECOYS, CELL_TIMEOUT and the runner settings.

Running tasks
  tgm.py prepare  [TASK ...]           clone each repo at its commit and build every library copy
  tgm.py solution [TASK ...]           grade the task's reference suite (solution/solve.sh)
  tgm.py verify   TASK PATCH           grade a patch
  tgm.py suite    TASK DIR             grade the test files in DIR as if they were regression_tests/
  tgm.py agent    AGENT TASK OUT_DIR   run an agent (agent_main) on the task, then grade its patch
Authoring
  tgm.py new   TASK --lang L --url URL --sha SHA [--var K=V ...] [--desc TEXT]   scaffold a task
  tgm.py edit  TASK STATE              make an editable copy of the repository for a mutant or decoy
  tgm.py patch TASK STATE              turn that copy's changes into tests/STATE.patch
  tgm.py sync  TASK                    refresh sample.env, Dockerfiles and shared scripts from the patches
  tgm.py check TASK                    lint the task layout
  tgm.py docker TASK [PATCH]           build the task images and grade PATCH (default: the reference suite)
                                       with tests/test.sh inside the verifier image, like the Ridges CLI does;
                                       --ca FILE adds a CA certificate to the build (for TLS-intercepting proxies);
                                       the task images are removed afterwards unless TG_KEEP_IMAGES=1

Options: -j N runs N library copies in parallel (default TG_JOBS or 1); --keep keeps the cell directories.
Environment: TG_BENCH (task directory, default ../ridges_hard next to this file), TG_ROOT (work dir, default
/opt/tgm), TG_TOOLS (shared tools such as test-runner jars, default /opt/tgm-tools; prepare_state.sh fills it,
runners only read it), TG_PYTHON (Python with pytest for Python tasks, default /opt/venv313-base/bin/python if present),
AGENT_TIMEOUT (default 1800).
"""
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "tgm_templates")
BENCH = os.path.abspath(os.environ.get("TG_BENCH") or os.path.join(HERE, "..", "ridges_hard"))
ROOT = os.environ.get("TG_ROOT", "/opt/tgm")
sys.path.insert(0, os.path.join(TEMPLATES, "common"))
from junit_summary import summary  # noqa: E402

# Environment passed through to the runner (toolchain locations); everything else is dropped.
PASS_ENV = ("PATH", "LANG", "LC_ALL", "GOROOT", "GOTOOLCHAIN", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "JAVA_HOME",
            "TG_PYTHON", "TG_TOOLS")
TOOLS = os.environ.get("TG_TOOLS", "/opt/tgm-tools")  # shared, read-only-at-run-time tools (jars, tsx, ...)


def tasks(names):
    return names or sorted(d for d in os.listdir(BENCH)
                           if os.path.isfile(os.path.join(BENCH, d, "tests", "sample.env")))


def task_dir(task, *parts):
    return os.path.join(BENCH, task, *parts)


def read_env(task):
    env = {}
    with open(task_dir(task, "tests", "sample.env")) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def states(env):
    return env.get("MUTANTS", "").split(), env.get("DECOYS", "").split()


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def source(task):
    """(url, sha) from the verifier Dockerfile."""
    with open(task_dir(task, "tests", "Dockerfile")) as fh:
        text = fh.read()
    url = re.search(r"git clone (\S+) /app", text).group(1)
    sha = re.search(r"checkout ([0-9a-f]{40})", text).group(1)
    return url, sha


def default_python():
    for p in ("/opt/venv313-base/bin/python",):
        if os.path.exists(p):
            return p
    return shutil.which("python3") or "python3"


def runner_env(task, home):
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.setdefault("TG_PYTHON", default_python())
    env.setdefault("TG_TOOLS", TOOLS)
    if "RUSTUP_HOME" not in env and os.path.isdir("/root/.rustup"):
        env["RUSTUP_HOME"] = "/root/.rustup"
    env.update(read_env(task))
    env.update(HOME=home, TMPDIR=home)
    return env


def chmod_tree(path, mode="a+rX"):
    subprocess.run(["chmod", "-R", mode, path], check=False)


def ensure_root():
    os.makedirs(os.path.join(ROOT, "cells"), exist_ok=True)
    os.chmod(ROOT, 0o755)
    os.chmod(os.path.join(ROOT, "cells"), 0o755)


# ---------------------------------------------------------------- preparing the library copies

def clone(task):
    url, sha = source(task)
    repo = os.path.join(ROOT, task, "clone")
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != sha:
        shutil.rmtree(repo, ignore_errors=True)
        os.makedirs(repo)
        sh(["git", "init", "-q"], cwd=repo)
        sh(["git", "remote", "add", "origin", url], cwd=repo)
        sh(["git", "fetch", "-q", "--depth", "1", "origin", sha], cwd=repo)
        sh(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo)
        sh(["git", "config", "user.email", "ridges@example.com"], cwd=repo)
        sh(["git", "config", "user.name", "Ridges"], cwd=repo)
        sh(["git", "submodule", "update", "--init", "--depth", "1", "-q"], cwd=repo)
    return repo, sha


def stamp(task, state):
    h = hashlib.sha256(source(task)[1].encode())
    for f in [task_dir(task, "tests", n) for n in task_runner_files(task)] + (
            [task_dir(task, "tests", state + ".patch")] if state != "pristine" else []):
        with open(f, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def prepare_state(task, state, repo):
    dest = os.path.join(ROOT, task, "matrix", state)
    want = stamp(task, state)
    stamp_file = os.path.join(dest, ".stamp")
    if os.path.isfile(stamp_file) and open(stamp_file).read() == want:
        return "cached"
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    target = os.path.join(dest, "repo")
    subprocess.run(["cp", "-a", repo, target], check=True)
    if state != "pristine":
        r = subprocess.run(["git", "apply", "-p1", task_dir(task, "tests", state + ".patch")], cwd=target,
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("%s does not apply: %s" % (state, r.stderr.strip()))
    env = dict(os.environ, **{k: v for k, v in read_env(task).items()})
    env["TG_TESTS_DIR"] = task_dir(task, "tests")
    env.setdefault("TG_TOOLS", TOOLS)
    os.makedirs(TOOLS, exist_ok=True)
    r = subprocess.run(["bash", task_dir(task, "tests", "prepare_state.sh"), target], cwd=target, env=env,
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("prepare_state.sh failed for %s:\n%s%s" % (state, r.stdout[-3000:], r.stderr[-3000:]))
    shutil.rmtree(os.path.join(target, ".git"), ignore_errors=True)
    chmod_tree(dest)
    with open(stamp_file, "w") as fh:
        fh.write(want)
    return "built"


def prepare(task, jobs=1):
    ensure_root()
    repo, sha = clone(task)
    os.makedirs(os.path.join(repo, "regression_tests"), exist_ok=True)
    mutants, decoys = states(read_env(task))
    all_states = ["pristine"] + mutants + decoys
    # Drop copies of states that no longer exist.
    mdir = os.path.join(ROOT, task, "matrix")
    if os.path.isdir(mdir):
        for s in os.listdir(mdir):
            if s not in all_states:
                shutil.rmtree(os.path.join(mdir, s), ignore_errors=True)
    with concurrent.futures.ThreadPoolExecutor(max(1, jobs)) as ex:
        done = list(ex.map(lambda s: prepare_state(task, s, repo), all_states))
    os.chmod(os.path.join(ROOT, task), 0o755)
    return "%s: %s at %s, %d mutants, %d decoys (%d rebuilt)" % (
        task, read_env(task).get("LANGUAGE", "?"), sha[:9], len(mutants), len(decoys), done.count("built"))


# ---------------------------------------------------------------- grading

def run_cell(task, state, suite_dir, keep=False):
    """Run the suite against one library copy; returns (rc, summary or None, seconds)."""
    work = tempfile.mkdtemp(prefix="cell-%s-" % state, dir=os.path.join(ROOT, "cells"))
    os.chmod(work, 0o755)
    tmp, out = os.path.join(work, "tmp"), os.path.join(work, "out")
    os.makedirs(tmp)
    os.makedirs(out)
    repo = os.path.join(work, "repo")
    subprocess.run(["cp", "-a", os.path.join(ROOT, task, "matrix", state, "repo"), repo], check=True)
    shutil.rmtree(os.path.join(repo, "regression_tests"), ignore_errors=True)
    shutil.copytree(suite_dir, os.path.join(repo, "regression_tests"))
    subprocess.run(["chown", "-R", "root:root", repo], check=False)
    chmod_tree(repo, "a+rX,go-w")
    subprocess.run(["chown", "-R", "nobody", tmp, out], check=True)
    tg = os.path.join(work, "tg")
    os.makedirs(tg)
    for name in task_runner_files(task):
        shutil.copy2(task_dir(task, "tests", name), os.path.join(tg, name))
    chmod_tree(tg, "a+rX")
    runner = os.path.join(tg, "run_cell.sh")
    os.chmod(runner, 0o755)
    env = runner_env(task, tmp)
    env["TG_TESTS_DIR"] = tg
    timeout = float(env.get("CELL_TIMEOUT") or 60)
    junit = os.path.join(out, "junit.xml")
    cmd = "cd %s && exec env -i %s bash %s %s %s" % (
        repo, " ".join("%s=%s" % (k, _q(v)) for k, v in env.items()), runner, repo, junit)
    t0 = time.time()
    proc = subprocess.Popen(["su", "-s", "/bin/bash", "nobody", "-c", cmd], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc, output = 124, b""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait()
    secs = time.time() - t0
    s = summary(junit) if os.path.isfile(junit) else None
    if keep:
        with open(os.path.join(work, "runner_output.txt"), "wb") as fh:
            fh.write(output or b"")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return rc, s, secs, (output or b"").decode(errors="replace")[-4000:]


def _q(v):
    return "'" + str(v).replace("'", "'\\''") + "'"


def grade_suite(task, suite_dir, jobs=1, keep=False, only=None):
    ensure_root()
    mutants, decoys = states(read_env(task))
    result = {"task": task, "reward": 0, "rows": {}, "problem": None}
    base = run_cell(task, "pristine", suite_dir, keep)
    result["pristine_secs"] = round(base[2], 1)
    if base[0] != 0 or not base[1]:
        result["problem"] = "suite does not pass on the original library (rc=%s)" % base[0]
        result["output"] = base[3]
        if base[1]:
            result["failed_on_original"] = base[1]["failed_ids"][:20]
        return result
    others = [s for s in decoys + mutants if not only or s in only]
    with concurrent.futures.ThreadPoolExecutor(max(1, jobs)) as ex:
        cells = dict(zip(others, ex.map(lambda s: run_cell(task, s, suite_dir, keep), others)))
    cells["pristine"] = base

    def verdict(state, want):
        rc, s, _, _ = cells[state]
        if rc not in (0, 1) or not s or s["tests"] != base[1]["tests"] or s["sha"] != base[1]["sha"]:
            return "invalid (rc=%s)" % rc
        if want == "green":
            return "ok" if rc == 0 and s["failed"] == 0 else "wrong: failed %s" % s["failed_ids"][:3]
        return "ok" if s["failed"] >= 1 else "wrong"

    rows = {"original_passes": "ok"}
    for s in decoys:
        if s in cells:
            rows[s + "_passes"] = verdict(s, "green")
    for s in mutants:
        if s in cells:
            rows[s + "_caught"] = verdict(s, "red")
    result["rows"] = rows
    result["tests"] = base[1]["tests"]
    result["reward"] = int(all(v == "ok" for v in rows.values()) and not only)
    result["mutants_caught"] = "%d/%d" % (sum(rows.get(s + "_caught") == "ok" for s in mutants), len(mutants))
    result["decoys_passed"] = "%d/%d" % (sum(rows.get(s + "_passes") == "ok" for s in decoys), len(decoys))
    result["max_cell_secs"] = round(max(c[2] for c in cells.values()), 1)
    return result


def app_copy(task):
    repo, _ = clone(task)
    app = tempfile.mkdtemp(prefix="app-", dir=os.path.join(ROOT, "cells"))
    shutil.rmtree(app)
    subprocess.run(["cp", "-a", repo, app], check=True)
    os.makedirs(os.path.join(app, "regression_tests"), exist_ok=True)
    return app


def verify(task, patch_text, jobs=1, keep=False):
    ensure_root()
    app = app_copy(task)
    try:
        if not patch_text.strip():
            return {"task": task, "reward": 0, "problem": "submitted patch is missing or empty"}
        patch = app + ".patch"
        with open(patch, "w") as fh:
            fh.write(patch_text)
        if subprocess.run(["git", "apply", "--check", patch], cwd=app, capture_output=True).returncode:
            return {"task": task, "reward": 0, "problem": "patch does not apply"}
        numstat = subprocess.run(["git", "apply", "--numstat", "-z", patch], cwd=app, capture_output=True).stdout
        paths = [rec.split(b"\t")[-1].decode() for rec in numstat.split(b"\0") if rec]
        bad = [p for p in paths if not p.startswith("regression_tests/")]
        if bad:
            return {"task": task, "reward": 0, "problem": "paths outside regression_tests: %s" % bad}
        subprocess.run(["git", "apply", patch], cwd=app, check=True)
        os.remove(patch)
        return grade_suite(task, os.path.join(app, "regression_tests"), jobs, keep)
    finally:
        shutil.rmtree(app, ignore_errors=True)


def suite_patch(app):
    subprocess.run(["git", "add", "-A", "regression_tests"], cwd=app, check=True)
    return subprocess.run(["git", "diff", "--cached", "--binary"], cwd=app, capture_output=True, text=True).stdout


def solution(task, jobs=1, keep=False):
    ensure_root()
    app = app_copy(task)
    try:
        script = open(task_dir(task, "solution", "solve.sh")).read().replace("/app", app)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        if r.returncode:
            return {"task": task, "reward": 0, "problem": "solve.sh failed: " + r.stderr[-2000:]}
        patch = suite_patch(app)
    finally:
        shutil.rmtree(app, ignore_errors=True)
    return verify(task, patch, jobs, keep)


def suite(task, directory, jobs=1, keep=False):
    ensure_root()
    app = app_copy(task)
    try:
        shutil.rmtree(os.path.join(app, "regression_tests"), ignore_errors=True)
        shutil.copytree(directory, os.path.join(app, "regression_tests"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", "target"))
        patch = suite_patch(app)
    finally:
        shutil.rmtree(app, ignore_errors=True)
    return verify(task, patch, jobs, keep)


def agent(agent_path, task, out, jobs=1):
    """Run the agent with the repository checked out (like the task image), then grade its patch."""
    out = os.path.abspath(out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    os.chmod(out, 0o755)
    app = os.path.join(out, "app")
    repo, _ = clone(task)
    subprocess.run(["cp", "-a", os.path.join(ROOT, task, "matrix", "pristine", "repo"), app], check=True)
    shutil.copytree(os.path.join(repo, ".git"), os.path.join(app, ".git"))
    os.makedirs(os.path.join(app, "regression_tests"), exist_ok=True)
    with open(task_dir(task, "instruction.md")) as fh:
        statement = fh.read().replace("/app", app)
    with open(os.path.join(out, "statement.md"), "w") as fh:
        fh.write(statement)
    wall = float(os.environ.get("AGENT_TIMEOUT") or 1800)
    env = dict(os.environ, AGENT_TIMEOUT=str(int(wall)), TG_DEBUG_DIR=os.path.join(out, "debug"),
               TG_TRANSCRIPT=os.path.join(out, "transcript.txt"))
    tenv = read_env(task)
    if tenv.get("LANGUAGE") == "python":
        env["PYTHONPATH"] = os.path.join(app, tenv.get("PY_SRC", "."))
    t0 = time.time()
    with open(os.path.join(out, "log.txt"), "w") as log:
        proc = subprocess.Popen([env.get("TG_PYTHON") or default_python(), os.path.join(HERE, "ridges_agent_driver.py"),
                                 agent_path, os.path.join(out, "statement.md"), os.path.join(out, "patch.diff")],
                                cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=wall + 60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    secs = time.time() - t0
    patch_path = os.path.join(out, "patch.diff")
    patch = open(patch_path).read() if os.path.isfile(patch_path) else ""
    result = verify(task, patch, jobs)
    result.update({"agent": os.path.basename(agent_path), "agent_secs": round(secs), "agent_rc": proc.returncode})
    with open(os.path.join(out, "result.json"), "w") as fh:
        json.dump(result, fh, indent=1)
    return result


# ---------------------------------------------------------------- authoring

def _natural(name):
    m = re.match(r"(\D+)_(\d+)$", name)
    return (m.group(1), int(m.group(2))) if m else (name, 0)


def _template(lang, name):
    p = os.path.join(TEMPLATES, lang, name)
    if not os.path.exists(p):
        raise SystemExit("missing template %s" % p)
    return p


AUTHORING_ONLY = ("base.Dockerfile", "env.Dockerfile", "instruction_env.md", "README.md")


def runner_files(lang):
    d = os.path.join(TEMPLATES, lang)
    return sorted(f for f in os.listdir(d) if f not in AUTHORING_ONLY and os.path.isfile(os.path.join(d, f)))


def task_runner_files(task):
    return runner_files(read_env(task)["LANGUAGE"])


def _fill(text, env):
    return re.sub(r"\{([A-Z_]+)\}", lambda m: env.get(m.group(1), m.group(0)), text)


def sync(task):
    """Refresh sample.env state lists, the Dockerfiles and the shared scripts."""
    env = read_env(task)
    lang = env["LANGUAGE"]
    names = sorted((f[:-6] for f in os.listdir(task_dir(task, "tests")) if f.endswith(".patch")), key=_natural)
    env["MUTANTS"] = " ".join(n for n in names if n.startswith("mutant_"))
    env["DECOYS"] = " ".join(n for n in names if n.startswith("decoy_"))
    order = ["LANGUAGE", "MUTANTS", "DECOYS", "CELL_TIMEOUT"]
    with open(task_dir(task, "tests", "sample.env"), "w") as fh:
        for k in order + sorted(k for k in env if k not in order):
            if k in env:
                fh.write('%s="%s"\n' % (k, env[k]) if " " in env[k] or not env[k] else "%s=%s\n" % (k, env[k]))
    for name in ("test.sh", "junit_summary.py"):
        shutil.copy2(os.path.join(TEMPLATES, "common", name), task_dir(task, "tests", name))
    os.makedirs(task_dir(task, "environment"), exist_ok=True)
    for name in runner_files(lang):
        shutil.copy2(_template(lang, name), task_dir(task, "tests", name))
        shutil.copy2(_template(lang, name), task_dir(task, "environment", name))
    shutil.copy2(task_dir(task, "tests", "sample.env"), task_dir(task, "environment", "sample.env"))
    url, sha = source(task)
    fill = dict(env, URL=url, SHA=sha, STATES=" ".join(names), RUNNER_FILES=" ".join(runner_files(lang)))
    base = _fill(open(_template(lang, "base.Dockerfile")).read(), fill).rstrip() + "\n"
    env_lines = ""
    if os.path.exists(os.path.join(TEMPLATES, lang, "env.Dockerfile")):
        env_lines = _fill(open(_template(lang, "env.Dockerfile")).read(), fill).rstrip() + "\n"
    with open(task_dir(task, "tests", "Dockerfile"), "w") as fh:
        fh.write(base + _fill(VERIFIER_DOCKERFILE, fill))
    with open(task_dir(task, "environment", "Dockerfile"), "w") as fh:
        fh.write(base + _fill(ENV_DOCKERFILE, fill) + env_lines + "RUN mkdir -p /app/regression_tests\nWORKDIR /app\n")
    for p in (task_dir(task, "tests"), task_dir(task, "environment")):
        for f in os.listdir(p):
            if f.endswith(".sh"):
                os.chmod(os.path.join(p, f), 0o755)
    return "%s: %d mutants, %d decoys" % (task, len(env["MUTANTS"].split()), len(env["DECOYS"].split()))


VERIFIER_DOCKERFILE = """COPY test.sh sample.env junit_summary.py {RUNNER_FILES} /tests/
RUN chmod 755 /tests && chmod 755 /tests/*.sh
RUN git clone {URL} /matrix/clone && git -C /matrix/clone checkout {SHA} \\
    && git -C /matrix/clone submodule update --init --depth 1
COPY *.patch /matrix/
RUN set -e; set -a; . /tests/sample.env; set +a; export TG_TESTS_DIR=/tests; for s in pristine {STATES}; do \\
      mkdir -p /matrix/$s && cp -a /matrix/clone /matrix/$s/repo; \\
      if [ "$s" != pristine ]; then git -C /matrix/$s/repo apply -p1 /matrix/$s.patch; fi; \\
      (cd /matrix/$s/repo && bash /tests/prepare_state.sh /matrix/$s/repo); rm -rf /matrix/$s/repo/.git; \\
    done; rm -rf /matrix/clone /matrix/*.patch
RUN git clone {URL} /app && git -C /app checkout {SHA} \\
    && git -C /app config user.email "verifier@example.com" && git -C /app config user.name "verifier" \\
    && test -z "$(git -C /app status --porcelain)" && mkdir -p /app/regression_tests
WORKDIR /app
"""

ENV_DOCKERFILE = """COPY sample.env {RUNNER_FILES} /usr/local/lib/tg/
RUN git clone {URL} /app && git -C /app checkout {SHA} \\
    && git -C /app submodule update --init --depth 1 && test -z "$(git -C /app status --porcelain)"
RUN set -a; . /usr/local/lib/tg/sample.env; set +a; export TG_TESTS_DIR=/usr/local/lib/tg; \\
    cd /app && bash /usr/local/lib/tg/prepare_state.sh /app
"""

TASK_TOML = """schema_version = "1.3"
artifacts = ["/logs/agent/patch.diff"]

[task]
name = "ridges-hard/{TASK}"
description = "{DESC}"

[[task.authors]]
name = "Ridges"

[metadata]
author_name = "Ridges"
author_email = "unknown"
difficulty = "hard"
category = "test-generation"
tags = ["{LANGUAGE}", "{LIBTAG}", "test-generation", "hard-practice"]

[verifier]
timeout_sec = {VERIFIER_TIMEOUT}
user = "root"
environment_mode = "separate"

[verifier.environment]
workdir = "/app"
build_timeout_sec = 2400.0
cpus = 2
memory_mb = 4096
storage_mb = 10240

[agent]
timeout_sec = 1800.0
user = "root"

[environment]
workdir = "/app"
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 4096
storage_mb = 8192
network_mode = "public"

[solution]
"""

SOLVE_SH = """#!/bin/bash
set -euo pipefail
cd /app
mkdir -p regression_tests
# Write the reference suite below, one heredoc per file, e.g.:
# cat > regression_tests/test_example.py <<'EOF'
# ...
# EOF
"""


def new(task, lang, url, sha, variables, desc):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("--sha must be a full 40-character commit hash")
    if not os.path.isdir(os.path.join(TEMPLATES, lang)):
        raise SystemExit("no templates for language %r (have: %s)" % (lang, ", ".join(sorted(
            d for d in os.listdir(TEMPLATES) if d != "common"))))
    d = task_dir(task)
    if os.path.exists(d):
        raise SystemExit("%s already exists" % d)
    for sub in ("tests", "environment", "solution"):
        os.makedirs(os.path.join(d, sub))
    env = {"LANGUAGE": lang, "MUTANTS": "", "DECOYS": "", "CELL_TIMEOUT": variables.pop("CELL_TIMEOUT", "60")}
    env.update(variables)
    with open(task_dir(task, "tests", "sample.env"), "w") as fh:
        for k, v in env.items():
            fh.write('%s="%s"\n' % (k, v))
    with open(task_dir(task, "tests", "Dockerfile"), "w") as fh:  # replaced by sync
        fh.write("RUN git clone %s /app && git -C /app checkout %s\n" % (url, sha))
    sync(task)
    timeout = max(900, int(float(env["CELL_TIMEOUT"]) * 30))
    with open(task_dir(task, "task.toml"), "w") as fh:
        fh.write(_fill(TASK_TOML, {"TASK": task, "DESC": desc.replace('"', "'"), "LANGUAGE": lang,
                                    "LIBTAG": task.split("__")[0], "VERIFIER_TIMEOUT": "%.1f" % timeout}))
    with open(task_dir(task, "solution", "solve.sh"), "w") as fh:
        fh.write(SOLVE_SH)
    os.chmod(task_dir(task, "solution", "solve.sh"), 0o755)
    env_md = os.path.join(TEMPLATES, lang, "instruction_env.md")
    with open(task_dir(task, "instruction.md"), "w") as fh:
        fh.write("# TITLE\n\nINTRO (repository, version, commit, what the maintainers are about to refactor)\n\n"
                 "## What the suite must do\n\n## Scope\n\n## Not part of the contract\n\n")
        if os.path.exists(env_md):
            fh.write(_fill(open(env_md).read(), env))
    return "created %s" % d


def edit(task, state):
    repo, _ = clone(task)
    dest = os.path.join(ROOT, task, "edit", state)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(["cp", "-a", repo, dest], check=True)
    p = task_dir(task, "tests", state + ".patch")
    if os.path.exists(p):
        sh(["git", "apply", "-p1", p], cwd=dest)
    return dest


def make_patch(task, state):
    dest = os.path.join(ROOT, task, "edit", state)
    diff = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff"], cwd=dest, capture_output=True,
                          text=True, check=True).stdout
    if not diff.strip():
        raise SystemExit("no changes to tracked files in %s" % dest)
    diff = "\n".join(l for l in diff.splitlines() if not l.startswith("index ")) + "\n"
    with open(task_dir(task, "tests", state + ".patch"), "w") as fh:
        fh.write(diff)
    sync(task)
    return "wrote tests/%s.patch (%d lines)" % (state, diff.count("\n"))


def check(task):
    problems = []
    for f in ["instruction.md", "task.toml", "environment/Dockerfile", "environment/sample.env", "tests/Dockerfile",
              "tests/test.sh", "tests/junit_summary.py", "tests/sample.env", "solution/solve.sh"] + [
              "tests/" + n for n in task_runner_files(task)] + ["environment/" + n for n in task_runner_files(task)]:
        if not os.path.isfile(task_dir(task, f)):
            problems.append("missing " + f)
    env = read_env(task)
    mutants, decoys = states(env)
    for s in mutants + decoys:
        if not os.path.isfile(task_dir(task, "tests", s + ".patch")):
            problems.append("missing patch for " + s)
    text = open(task_dir(task, "instruction.md")).read() if os.path.isfile(task_dir(task, "instruction.md")) else ""
    for head in ("## What the suite must do", "## Scope", "## Not part of the contract", "## Environment",
                 "## Test hygiene and CI requirements"):
        if head not in text:
            problems.append("instruction.md lacks '%s'" % head)
    for marker in ("# TITLE", "INTRO (", "{LIB}", "{PY_SRC}"):
        if marker in text:
            problems.append("instruction.md still has the placeholder " + marker)
    return {"task": task, "language": env.get("LANGUAGE"), "mutants": len(mutants), "decoys": len(decoys),
            "problems": problems}


def _ca_shim(dockerfile, ca_name):
    """Insert CA-trust lines after the FROM line (only for builds behind a TLS-intercepting proxy)."""
    lines = dockerfile.splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("FROM "))
    shim = ["COPY %s /usr/local/share/ca-certificates/tgm-proxy.crt" % ca_name,
            "RUN (command -v update-ca-certificates >/dev/null || (apt-get update && apt-get install -y "
            "--no-install-recommends ca-certificates) || (apk add --no-cache ca-certificates)) "
            "&& update-ca-certificates",
            "ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt "
            "PIP_CERT=/etc/ssl/certs/ca-certificates.crt NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/tgm-proxy.crt "
            "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt CARGO_HTTP_CAINFO=/etc/ssl/certs/ca-certificates.crt "
            "COMPOSER_CAFILE=/etc/ssl/certs/ca-certificates.crt",
            # apt through an HTTPS-only proxy: switch the Debian/Ubuntu mirrors to https.
            "RUN for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do [ -f \"$f\" ] && sed -i "
            "-e 's|http://deb.debian.org|https://deb.debian.org|g' -e 's|http://security.debian.org|https://security.debian.org|g' "
            "-e 's|http://archive.ubuntu.com|https://archive.ubuntu.com|g' -e 's|http://security.ubuntu.com|https://security.ubuntu.com|g' "
            "\"$f\"; done; true"]
    return "\n".join(lines[:i + 1] + shim + lines[i + 1:]) + "\n"


def _docker_build(context, tag, ca):
    build = tempfile.mkdtemp(prefix="dctx-", dir=os.path.join(ROOT, "cells"))
    shutil.rmtree(build)
    shutil.copytree(context, build)
    if ca:
        shutil.copy2(ca, os.path.join(build, "tgm-proxy.crt"))
        with open(os.path.join(build, "Dockerfile")) as fh:
            text = fh.read()
        with open(os.path.join(build, "Dockerfile"), "w") as fh:
            fh.write(_ca_shim(text, "tgm-proxy.crt"))
    cmd = ["docker", "build", "--network", "host", "-t", tag]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        cmd += ["--build-arg", "HTTPS_PROXY=" + proxy, "--build-arg", "https_proxy=" + proxy]
    r = subprocess.run(cmd + [build], capture_output=True, text=True)
    shutil.rmtree(build, ignore_errors=True)
    if r.returncode:
        raise RuntimeError("docker build of %s failed:\n%s" % (tag, (r.stdout + r.stderr)[-6000:]))


def docker(task, patch_text=None, ca=None):
    """Build environment/ and tests/ images, then run tests/test.sh on PATCH inside the verifier image."""
    ensure_root()
    tag = "tgm-" + re.sub(r"[^a-z0-9_.-]", "-", task.lower())
    t0 = time.time()
    _docker_build(task_dir(task, "environment"), tag + ":env", ca)
    _docker_build(task_dir(task, "tests"), tag + ":verifier", ca)
    built = time.time() - t0
    if patch_text is None:
        app = app_copy(task)
        try:
            script = open(task_dir(task, "solution", "solve.sh")).read().replace("/app", app)
            subprocess.run(["bash", "-c", script], check=True, capture_output=True)
            patch_text = suite_patch(app)
        finally:
            shutil.rmtree(app, ignore_errors=True)
    logs = tempfile.mkdtemp(prefix="dlogs-", dir=os.path.join(ROOT, "cells"))
    os.makedirs(os.path.join(logs, "agent"))
    with open(os.path.join(logs, "agent", "patch.diff"), "w") as fh:
        fh.write(patch_text)
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", "%s:/logs" % logs, tag + ":verifier",
                        "bash", "/tests/test.sh"], capture_output=True, text=True)
    reward_file = os.path.join(logs, "verifier", "reward.txt")
    reward = open(reward_file).read().strip() if os.path.isfile(reward_file) else None
    junit = os.path.join(logs, "verifier", "junit.xml")
    s = summary(junit) if os.path.isfile(junit) else None
    if not os.environ.get("TG_KEEP_IMAGES"):
        subprocess.run(["docker", "image", "rm", "-f", tag + ":env", tag + ":verifier"], capture_output=True)
        subprocess.run(["docker", "builder", "prune", "-f"], capture_output=True)
    result = {"task": task, "docker_reward": reward, "rc": r.returncode, "build_secs": round(built),
              "grade_secs": round(time.time() - t0 - built),
              "failed_rows": s["failed_ids"] if s else None, "stderr": r.stderr[-2000:]}
    shutil.rmtree(logs, ignore_errors=True)
    return result


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    jobs = int(os.environ.get("TG_JOBS") or 1)
    keep = False
    if "-j" in args:
        i = args.index("-j")
        jobs = int(args[i + 1])
        del args[i:i + 2]
    if "--keep" in args:
        keep = True
        args.remove("--keep")
    cmd, args = args[0], args[1:]
    if cmd == "prepare":
        for t in tasks(args):
            print(prepare(t, jobs), flush=True)
    elif cmd == "solution":
        for t in tasks(args):
            r = solution(t, jobs, keep)
            print(json.dumps({k: r.get(k) for k in ("task", "reward", "tests", "mutants_caught", "decoys_passed",
                                                     "max_cell_secs", "problem")}), flush=True)
            if not r["reward"]:
                print("   ", json.dumps({k: v for k, v in r.get("rows", {}).items() if v != "ok"}), flush=True)
                if r.get("output"):
                    print(r["output"], flush=True)
    elif cmd == "verify":
        print(json.dumps(verify(args[0], open(args[1]).read(), jobs, keep), indent=1))
    elif cmd == "suite":
        print(json.dumps(suite(args[0], args[1], jobs, keep), indent=1))
    elif cmd == "agent":
        print(json.dumps(agent(os.path.abspath(args[0]), args[1], args[2], jobs), indent=1))
    elif cmd == "new":
        task = args[0]
        opts = {"--var": []}
        i = 1
        while i < len(args):
            if args[i] == "--var":
                opts["--var"].append(args[i + 1])
            else:
                opts[args[i]] = args[i + 1]
            i += 2
        variables = dict(v.split("=", 1) for v in opts["--var"])
        print(new(task, opts["--lang"], opts["--url"], opts["--sha"], variables, opts.get("--desc", "")))
    elif cmd == "edit":
        print(edit(args[0], args[1]))
    elif cmd == "patch":
        print(make_patch(args[0], args[1]))
    elif cmd == "sync":
        for t in tasks(args):
            print(sync(t))
    elif cmd == "docker":
        ca = None
        if "--ca" in args:
            i = args.index("--ca")
            ca = args[i + 1]
            del args[i:i + 2]
        patch = open(args[1]).read() if len(args) > 1 else None
        print(json.dumps(docker(args[0], patch, ca), indent=1))
    elif cmd == "check":
        for t in tasks(args):
            print(json.dumps(check(t)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
