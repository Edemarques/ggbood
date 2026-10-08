"""End-to-end run of the agent against harness/sample_repo with the deterministic FakeLLM.

usage: python run_e2e.py AGENT.py OUT_DIR [AGENT_TIMEOUT_SECONDS]

Copies the sample repository to OUT_DIR/repo (as a git repository), runs Run.execute() and
Run.finalize() with harness/fake_llm.py answering every model call, and writes:
  OUT_DIR/patch.diff     the patch the agent returned
  OUT_DIR/log.txt        the agent log
  OUT_DIR/debug/         TG_DEBUG_DIR dump (cases, problems, mutants)
  OUT_DIR/summary.json   timings, test counts and the result of running the patch with pytest
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    agent_path = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2])
    wall = sys.argv[3] if len(sys.argv) > 3 else "600"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    repo = os.path.join(out, "repo")
    shutil.copytree(os.path.join(HERE, "sample_repo"), repo)
    os.chmod(out, 0o755)
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True)
    for dirpath, dirs, files in os.walk(repo):
        os.chmod(dirpath, 0o755)
        for f in files:
            os.chmod(os.path.join(dirpath, f), 0o644)
    with open(os.path.join(HERE, "statement.md")) as fh:
        statement = fh.read().replace("{REPO}", repo)
    os.environ.update({"AGENT_TIMEOUT": wall, "OPENROUTER_API_KEY": "fake", "PYTHONPATH": repo,
                       "TG_DEBUG_DIR": os.path.join(out, "debug")})
    sys.path.insert(0, HERE)
    import fake_llm

    spec = importlib.util.spec_from_file_location("agent_under_test", agent_path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    log_buf = io.StringIO()

    class Tee(io.TextIOBase):
        def write(self, s):
            log_buf.write(s)
            sys.__stdout__.write(s)
            return len(s)

        def flush(self):
            sys.__stdout__.flush()

    t0 = time.time()
    with contextlib.redirect_stdout(Tee()):
        run = agent.Run(statement)
        fake = fake_llm.FakeLLM(agent, run).install()
        try:
            run.execute()
        except Exception:
            import traceback
            print("[E2E] execute raised: %s" % traceback.format_exc())
        patch = run.finalize()
    secs = time.time() - t0
    with open(os.path.join(out, "patch.diff"), "w") as fh:
        fh.write(patch)
    with open(os.path.join(out, "log.txt"), "w") as fh:
        fh.write(log_buf.getvalue())
    # apply the patch to a fresh copy and run it the way a grader would
    check = os.path.join(out, "check")
    shutil.copytree(repo, check)
    r = subprocess.run(["git", "apply", "-"], cwd=check, input=patch.encode(), capture_output=True)
    applied = r.returncode == 0
    res = None
    if applied:
        t1 = time.time()
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=check,
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=check))
        tail = p.stdout.strip().split("\n")[-1] if p.stdout.strip() else ""
        res = {"rc": p.returncode, "tail": tail, "secs": round(time.time() - t1, 2)}
    tests = len(re.findall(r"(?m)^\+def test_", patch))
    summary = {"agent": os.path.basename(agent_path), "secs": round(secs, 1), "tests": tests,
               "files": len(re.findall(r"(?m)^diff --git", patch)), "applied": applied, "pytest": res,
               "llm_calls": run.llm.calls, "asks": fake.asks, "mutants": len(run.mutants),
               "mutation": run.mutation_stats(), "fast_stats": run.fast_stats,
               "excluded": len(run.excluded), "records": len(run.records)}
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "asks"}, indent=1))


if __name__ == "__main__":
    main()
