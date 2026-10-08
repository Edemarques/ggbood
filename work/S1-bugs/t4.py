import os, sys, subprocess, tempfile, json, importlib.util, time
P = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", P)
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
base = os.path.expanduser("~/tgopt/S1-bugs")
d = tempfile.mkdtemp(dir=base)
rec = os.path.join(d, "rec"); os.makedirs(rec)
open(os.path.join(rec, "test_m.py"), "w").write('''
import threading
def case_timer():
    t = threading.Timer(8.0, lambda: None)
    t.start()
    return 1
def case_swallow():
    while True:
        try:
            sum(range(100000))
        except:
            pass
def case_after():
    return 2
''')
cfg = {"mode": "record", "modules": ["test_m"], "cases": ["test_m::case_timer"], "sys_path": [rec],
       "out": os.path.join(d, "out.jsonl"), "tmp_base": d, "packages": []}
open(os.path.join(d, "cfg.json"), "w").write(json.dumps(cfg))
t = time.time()
rc, out = agent._run_proc([sys.executable, P, "--tg-runner", os.path.join(d, "cfg.json")], cwd=d, env=dict(os.environ), timeout=30)
print("timer case: rc", rc, "wall %.1f" % (time.time() - t))
print(open(cfg["out"]).read())
cfg2 = dict(cfg, cases=["test_m::case_swallow", "test_m::case_after"], out=os.path.join(d, "out2.jsonl"), timeout=1.0)
open(os.path.join(d, "cfg2.json"), "w").write(json.dumps(cfg2))
t = time.time()
rc, out = agent._run_proc([sys.executable, P, "--tg-runner", os.path.join(d, "cfg2.json")], cwd=d, env=dict(os.environ), timeout=12)
print("swallow case (timeout 1s): rc", rc, "wall %.1f" % (time.time() - t))
print(open(cfg2["out"]).read())
