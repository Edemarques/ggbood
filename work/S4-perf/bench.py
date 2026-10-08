import os, sys, time, shutil, subprocess, importlib.util, threading
HOME = os.path.expanduser("~/tgopt/S4-perf")
SRC = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
os.makedirs(HOME, exist_ok=True)
agent_path = os.path.join(HOME, "agent.py")
shutil.copyfile(SRC, agent_path)
repo = os.path.join(HOME, "repo")
shutil.rmtree(repo, ignore_errors=True)
os.makedirs(os.path.join(repo, "mylib"))
with open(os.path.join(repo, "mylib", "__init__.py"), "w") as fh:
    fh.write("from .core import *\n")
with open(os.path.join(repo, "mylib", "core.py"), "w") as fh:
    fh.write("\n".join("def f%d(x):\n    if x > %d:\n        return x * %d\n    return x - 1\n" % (i, i, i) for i in range(200)))
subprocess.run(["git", "init", "-q", repo])
spec = importlib.util.spec_from_file_location("agent", agent_path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.__file__ = agent_path
st = "Write tests for the repository at `%s`. Add files only under `tests/`. Import from `mylib`." % repo
os.environ["AGENT_TIMEOUT"] = "1800"
os.environ["PYTHONPATH"] = repo
run = agent.Run(st)
_be = run.base_env
UB = os.path.expanduser("~/.local")
run.base_env = lambda *a, **k: dict(_be(*a, **k), PYTHONUSERBASE=UB)
t = time.time(); run.setup(); print("setup %.2fs" % (time.time() - t), run.packages and run.packages[0]["name"])
NC = int(sys.argv[1]) if len(sys.argv) > 1 else 150
src = "from mylib import " + ", ".join("f%d" % i for i in range(200)) + "\n\n" + "\n".join(
    "def case_%d():\n    return [f%d(%d), f%d(%d)]\n" % (i, i % 200, i, (i + 7) % 200, i * 3) for i in range(NC))
for k in range(3):
    run.add_case_file("cases_m%d.py" % k, src)
names = ["cases_m%d" % k for k in range(3)]
t = time.time(); rep = run.record_modules(names); seq = time.time() - t
print("sequential record_modules %.2fs, records=%d" % (seq, len(run.records)))
print(str(rep)[:1500], list(run.case_problems.items())[:3])
# time each of the three runs individually
todo = sorted(run.records)
for kw in (dict(trace=True, reverse=False, seed="0"),
           dict(trace=False, reverse=True, seed="4217", clock_shift=agent.CLOCK_SHIFT),
           dict(trace=False, reverse=False, seed="91", clock_shift=agent.CLOCK_SHIFT / 37.0, absent=True)):
    t = time.time(); r = run._record(todo, **kw); print("  run %s %.2fs (%d)" % (kw.get("seed"), time.time() - t, len(r) - 1))
res = {}
def go(i, kw):
    res[i] = run._record(todo, **kw)
kws = [dict(trace=True, reverse=False, seed="0"),
       dict(trace=False, reverse=True, seed="4217", clock_shift=agent.CLOCK_SHIFT),
       dict(trace=False, reverse=False, seed="91", clock_shift=agent.CLOCK_SHIFT / 37.0, absent=True)]
t = time.time()
ths = [threading.Thread(target=go, args=(i, kw)) for i, kw in enumerate(kws)]
[x.start() for x in ths]; [x.join() for x in ths]
print("parallel three runs %.2fs" % (time.time() - t))
