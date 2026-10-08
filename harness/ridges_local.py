"""Run ridges-bench test-generation tasks locally, without Docker.

The verifier follows the task's tests/test.sh: the submitted suite runs as `nobody` with a 60 s limit against
the original library, every decoy (must pass) and every mutant (must fail); reward is 1 only if all hold.

usage:
  ridges_local.py prepare  [TASK ...]              clone each repo at its commit, build the variant matrix
  ridges_local.py solution [TASK ...]              verify the task's reference suite (checks this harness)
  ridges_local.py verify   TASK PATCH              verify a patch
  ridges_local.py agent    AGENT TASK OUT_DIR      run an agent on the task, then verify its patch

Environment: TG_BENCH (default /home/user/ridgesai/ridges-bench/test-generation), TG_ROOT (default /opt/tg),
AGENT_TIMEOUT (default 1800); the agent's model settings (OPENROUTER_BASE_URL, OPENROUTER_API_KEY, TG_MODEL, ...)
are passed through unchanged.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

BENCH = os.environ.get("TG_BENCH", "/home/user/ridgesai/ridges-bench/test-generation")
ROOT = os.environ.get("TG_ROOT", "/opt/tg")
HERE = os.path.dirname(os.path.abspath(__file__))
VENVS = {"inflect__english-inflection": "/opt/venv313-inflect", "python-slugify__slug-options": "/opt/venv313-slugify"}


def tasks(names):
    return names or sorted(d for d in os.listdir(BENCH) if os.path.isdir(os.path.join(BENCH, d)))


def python_for(task):
    return os.path.join(VENVS.get(task, "/opt/venv313-base"), "bin", "python")


def sample_env(task):
    env = {}
    with open(os.path.join(BENCH, task, "tests", "sample.env")) as fh:
        for line in fh:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v.strip('"')
    return env["IMPORT_NAME"], env["MUTANTS"].split(), env["DECOYS"].split()


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def prepare(task):
    with open(os.path.join(BENCH, task, "environment", "Dockerfile")) as fh:
        text = fh.read()
    url = re.search(r"git clone (\S+) /app", text).group(1)
    sha = re.search(r"checkout ([0-9a-f]{40})", text).group(1)
    base = os.path.join(ROOT, task)
    repo = os.path.join(base, "repo")
    if not os.path.isdir(os.path.join(repo, ".git")):
        shutil.rmtree(repo, ignore_errors=True)
        os.makedirs(repo)
        sh(["git", "init", "-q"], cwd=repo)
        sh(["git", "remote", "add", "origin", url], cwd=repo)
        sh(["git", "fetch", "-q", "--depth", "1", "origin", sha], cwd=repo)
        sh(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo)
        sh(["git", "config", "user.email", "ridges@example.com"], cwd=repo)
        sh(["git", "config", "user.name", "Ridges"], cwd=repo)
    os.makedirs(os.path.join(repo, "regression_tests"), exist_ok=True)
    name, mutants, decoys = sample_env(task)
    matrix = os.path.join(base, "matrix")
    shutil.rmtree(matrix, ignore_errors=True)
    for state in ["pristine"] + mutants + decoys:
        src = os.path.join(matrix, state, "src")
        os.makedirs(src)
        shutil.copytree(os.path.join(repo, name), os.path.join(src, name))
        if state != "pristine":
            sh(["git", "apply", "-p1", os.path.join(BENCH, task, "tests", state + ".patch")], cwd=src)
    subprocess.run(["chmod", "-R", "a+rX", base], check=True)
    return "%s: %s at %s, %d mutants, %d decoys" % (task, name, sha[:9], len(mutants), len(decoys))


def _cell(task, state, suite_dir, name):
    """Run the suite against one variant; returns (rc, junit path or None)."""
    work = tempfile.mkdtemp(prefix="cell-", dir=os.path.join(ROOT, "cells"))
    os.chmod(work, 0o755)
    os.makedirs(os.path.join(work, "tmp"))
    os.makedirs(os.path.join(work, "out"))
    shutil.copytree(os.path.join(ROOT, task, "matrix", state, "src"), os.path.join(work, "src"))
    shutil.copytree(suite_dir, os.path.join(work, "regression_tests"))
    subprocess.run(["chmod", "-R", "a+rX,go-w", os.path.join(work, "src"), os.path.join(work, "regression_tests")])
    subprocess.run(["chown", "-R", "nobody", os.path.join(work, "tmp"), os.path.join(work, "out")])
    py = python_for(task)
    probe = subprocess.run([py, "-c", "import %s, sys; sys.exit(0 if %s.__file__.startswith(%r) else 7)" % (
        name, name, os.path.join(work, "src") + "/")], cwd=work, env=dict(os.environ, PYTHONPATH=os.path.join(work, "src")))
    if probe.returncode:
        return 7, None, work
    cmd = ("cd {w} && HOME={w}/tmp TMPDIR={w}/tmp PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH={w}/src "
           "{py} -m pytest {w}/regression_tests -q -p no:cacheprovider -c /dev/null --rootdir={w} "
           "--junitxml={w}/out/junit.xml").format(w=work, py=py)
    proc = subprocess.Popen(["su", "-s", "/bin/bash", "nobody", "-c", cmd], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        rc = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        rc = 124
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # whatever the suite left running in its session
    except OSError:
        pass
    proc.wait()
    junit = os.path.join(work, "out", "junit.xml")
    return rc, (junit if os.path.isfile(junit) else None), work


def _counts(junit):
    try:
        root = ET.parse(junit).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        ids, failed = [], []
        for tc in suite.iter("testcase"):
            tid = "{}::{}".format(tc.get("classname") or "", tc.get("name") or "")
            ids.append(tid)
            if any(c.tag in ("failure", "error") for c in tc):
                failed.append(tid)
        return int(suite.get("tests")), int(suite.get("failures")), int(suite.get("errors")), sorted(ids), sorted(failed)
    except Exception:
        return None


def verify(task, patch_text):
    os.makedirs(os.path.join(ROOT, "cells"), exist_ok=True)
    name, mutants, decoys = sample_env(task)
    app = tempfile.mkdtemp(prefix="app-", dir=os.path.join(ROOT, "cells"))
    shutil.rmtree(app)
    shutil.copytree(os.path.join(ROOT, task, "repo"), app, symlinks=True)
    result = {"task": task, "reward": 0, "rows": {}, "problem": None}
    if not patch_text.strip():
        result["problem"] = "submitted patch is missing or empty"
        return result
    patch = app + ".patch"
    with open(patch, "w") as fh:
        fh.write(patch_text)
    if subprocess.run(["git", "apply", "--check", patch], cwd=app, capture_output=True).returncode:
        result["problem"] = "patch does not apply"
        return result
    numstat = subprocess.run(["git", "apply", "--numstat", "-z", patch], cwd=app, capture_output=True).stdout
    paths = [rec.split(b"\t")[-1].decode() for rec in numstat.split(b"\0") if rec]
    bad = [p for p in paths if not p.startswith("regression_tests/")]
    if bad:
        result["problem"] = "paths outside regression_tests: %s" % bad
        return result
    subprocess.run(["git", "apply", patch], cwd=app, check=True)
    suite = os.path.join(app, "regression_tests")
    cells = {}
    for state in ["pristine"] + decoys + mutants:
        rc, junit, work = _cell(task, state, suite, name)
        cells[state] = (rc, _counts(junit) if junit else None)
        shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(app, ignore_errors=True)
    os.remove(patch)
    base = cells["pristine"][1]
    if not base or base[0] < 1:
        result["problem"] = "no valid test report on the original library (rc=%s)" % cells["pristine"][0]
        return result

    def verdict(state, want):
        rc, counts = cells[state]
        if rc not in (0, 1) or not counts or counts[0] != base[0] or counts[3] != base[3]:
            return "invalid (rc=%s)" % rc
        bad = counts[1] + counts[2]
        ok = bad == 0 if want == "green" else bad >= 1
        return "ok" if ok else ("wrong: failed %s" % counts[4][:3] if want == "green" else "wrong")
    rows = {"original_passes": verdict("pristine", "green")}
    for s in decoys:
        rows[s + "_passes"] = verdict(s, "green")
    for s in mutants:
        rows[s + "_caught"] = verdict(s, "red")
    result["rows"] = rows
    result["tests"] = base[0]
    result["reward"] = int(all(v == "ok" for v in rows.values()))
    result["mutants_caught"] = "%d/%d" % (sum(rows[s + "_caught"] == "ok" for s in mutants), len(mutants))
    return result


def solution(task):
    """Verify the task's reference suite: builds it with solve.sh in a scratch copy of the repo."""
    app = tempfile.mkdtemp(prefix="sol-", dir=os.path.join(ROOT, "cells"))
    shutil.rmtree(app)
    shutil.copytree(os.path.join(ROOT, task, "repo"), app, symlinks=True)
    script = open(os.path.join(BENCH, task, "solution", "solve.sh")).read().replace("cd /app", "cd %s" % app)
    subprocess.run(["bash", "-c", script], check=True, capture_output=True)
    subprocess.run(["git", "add", "-A", "regression_tests"], cwd=app, check=True)
    patch = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=app, capture_output=True, text=True).stdout
    shutil.rmtree(app, ignore_errors=True)
    return verify(task, patch)


def agent(agent_path, task, out):
    """Run the agent the way the task image does (repo checked out, on PYTHONPATH), then verify its patch."""
    out = os.path.abspath(out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    os.chmod(out, 0o755)
    app = os.path.join(out, "app")
    shutil.copytree(os.path.join(ROOT, task, "repo"), app, symlinks=True)
    subprocess.run(["chmod", "-R", "a+rX", out])
    with open(os.path.join(BENCH, task, "instruction.md")) as fh:
        statement = fh.read().replace("/app", app)
    with open(os.path.join(out, "statement.md"), "w") as fh:
        fh.write(statement)
    wall = float(os.environ.get("AGENT_TIMEOUT") or 1800)
    env = dict(os.environ, PYTHONPATH=app, AGENT_TIMEOUT=str(int(wall)), TG_DEBUG_DIR=os.path.join(out, "debug"),
               TG_TRANSCRIPT=os.path.join(out, "transcript.txt"))
    t0 = time.time()
    with open(os.path.join(out, "log.txt"), "w") as log:
        proc = subprocess.Popen([python_for(task), os.path.join(HERE, "ridges_agent_driver.py"), agent_path,
                                 os.path.join(out, "statement.md"), os.path.join(out, "patch.diff")],
                                cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=wall + 60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    secs = time.time() - t0
    patch_path = os.path.join(out, "patch.diff")
    patch = open(patch_path).read() if os.path.isfile(patch_path) else ""
    result = verify(task, patch)
    result.update({"agent": os.path.basename(agent_path), "agent_secs": round(secs), "agent_rc": proc.returncode})
    with open(os.path.join(out, "result.json"), "w") as fh:
        json.dump(result, fh, indent=1)
    return result


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    os.makedirs(os.path.join(ROOT, "cells"), exist_ok=True)
    os.chmod(ROOT, 0o755)
    if cmd == "prepare":
        for t in tasks(args):
            print(prepare(t), flush=True)
    elif cmd == "solution":
        for t in tasks(args):
            r = solution(t)
            print(json.dumps({k: r.get(k) for k in ("task", "reward", "tests", "mutants_caught", "problem")}), flush=True)
            if not r["reward"]:
                print("   ", r["rows"], flush=True)
    elif cmd == "verify":
        print(json.dumps(verify(args[0], open(args[1]).read()), indent=1))
    elif cmd == "agent":
        print(json.dumps(agent(os.path.abspath(args[0]), args[1], args[2]), indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
