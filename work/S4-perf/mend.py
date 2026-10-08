import os, sys, time, shutil, subprocess, importlib.util
HOME = os.path.expanduser("~/tgopt/S4-perf")
SRC = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
os.makedirs(HOME, exist_ok=True)
agent_path = os.path.join(HOME, "agent.py")
shutil.copyfile(SRC, agent_path)
if os.environ.get("PATCHED"): agent_path = os.path.join(HOME, "agent_patched.py")
repo = os.path.join(HOME, "repo2")
shutil.rmtree(repo, ignore_errors=True)
os.makedirs(os.path.join(repo, "mylib"))
with open(os.path.join(repo, "mylib", "__init__.py"), "w") as fh:
    fh.write("def f1(x):\n    return x + 1\n")
subprocess.run(["git", "init", "-q", repo])
spec = importlib.util.spec_from_file_location("agent", agent_path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.__file__ = agent_path
os.environ["AGENT_TIMEOUT"] = "1800"
os.environ["PYTHONPATH"] = repo
run = agent.Run("Write tests for the repository at `%s`. Add files only under `tests/`. Import from `mylib`." % repo)
_be = run.base_env
UB = os.path.expanduser("~/.local")
run.base_env = lambda *a, **k: dict(_be(*a, **k), PYTHONUSERBASE=UB)
run.setup()
src = """from mylib import f1, nothere


def case_a():
    return f1(1)


def case_b():
    return f1(2)


def case_c():
    return nothere(3)
"""
print(run.add_case_file("cases_x.py", src))
rep = run.record_modules(["cases_x"])
print("REPORT", rep)
print("RECORDS", sorted(run.records))
print("CASE_PROBLEMS", run.case_problems)
print("BOTH", sorted(set(run.records) & set(run.case_problems)))

