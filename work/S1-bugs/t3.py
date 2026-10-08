import os, sys, subprocess, tempfile, json, importlib.util, time
P = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", P)
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
base = os.path.expanduser("~/tgopt/S1-bugs")
d = tempfile.mkdtemp(dir=base)
# 1. str(exc) raising crashes runner
rec = os.path.join(d, "rec"); os.makedirs(rec)
open(os.path.join(rec, "test_m.py"), "w").write('''
class E(Exception):
    def __init__(self, a):
        super().__init__()
    def __str__(self):
        return self.missing
def case_a():
    raise E(1)
def case_b():
    return 1
def case_big():
    return list(range(3_000_000))
''')
cfg = {"mode": "record", "modules": ["test_m"], "cases": ["test_m::case_a", "test_m::case_b"], "sys_path": [rec],
       "out": os.path.join(d, "out.jsonl"), "tmp_base": d, "packages": []}
open(os.path.join(d, "cfg.json"), "w").write(json.dumps(cfg))
r = subprocess.run([sys.executable, P, "--tg-runner", os.path.join(d, "cfg.json")], capture_output=True, text=True)
print("rc", r.returncode, r.stderr[-300:])
print(open(cfg["out"]).read())
# 2. _to_src cost on a big list (no alarm armed)
cfg2 = dict(cfg, cases=["test_m::case_big"], out=os.path.join(d, "out2.jsonl"))
open(os.path.join(d, "cfg2.json"), "w").write(json.dumps(cfg2))
t = time.time()
r = subprocess.run([sys.executable, P, "--tg-runner", os.path.join(d, "cfg2.json")], capture_output=True, text=True)
print("big: wall %.2f" % (time.time() - t), open(cfg2["out"]).read()[:400])
# 3. find_spec fix
fix = agent._ABSENT_SRC.replace("sys.meta_path.insert(0, _Absent())", '''sys.meta_path.insert(0, _Absent())

import importlib.util as _util
_real_find_spec = _util.find_spec


def _find_spec(name, package=None):
    full = _util.resolve_name(name, package) if name.startswith(".") else name
    if "." not in full and full in _ABSENT:
        return None
    return _real_find_spec(name, package)


_util.find_spec = _find_spec''')
ad = os.path.join(d, "absent"); os.makedirs(ad)
open(os.path.join(ad, "sitecustomize.py"), "w").write(fix % json.dumps(["json5x", "csv"]))
env = dict(os.environ, PYTHONPATH=ad)
r = subprocess.run([sys.executable, "-c", "import importlib.util as u; print(u.find_spec('csv'), u.find_spec('json'))\ntry:\n import csv\nexcept ImportError as e: print('import', repr(e))\ntry:\n u.find_spec('csv.x')\nexcept ImportError as e: print('sub', repr(e))"], env=env, capture_output=True, text=True)
print("fix:", r.returncode, r.stdout, r.stderr[-300:])
