import json, os, shutil, subprocess, sys, time

BASE = os.path.expanduser("~/tgopt/S2-bugs2")
CWD = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58"
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE + "/src/mylib")
os.makedirs(BASE + "/rec")
os.makedirs(BASE + "/tmp")
os.makedirs(BASE + "/out")
shutil.copy(CWD + "/codexv2.12_orig.py", BASE + "/agent.py")
LIB = '''import sys

def run(data=None):
    if data is None:
        data = sys.stdin.read()
    return len(data) + 1
'''
open(BASE + "/src/mylib/__init__.py", "w").write(LIB)
open(BASE + "/rec/test_c.py", "w").write('''from mylib import run
def case_a():
    return run()
''')
# record mode the way Run.runner does it (stdin=DEVNULL)
rcfg = {"sys_path": [BASE + "/src", BASE + "/rec"], "modules": ["test_c"], "packages": ["mylib"],
        "mode": "record", "cases": ["test_c::case_a"], "out": BASE + "/out/out.jsonl", "tmp_base": BASE + "/tmp"}
json.dump(rcfg, open(BASE + "/rcfg.json", "w"))
subprocess.run([sys.executable, BASE + "/agent.py", "--tg-runner", BASE + "/rcfg.json"], stdin=subprocess.DEVNULL)
print("record:", open(BASE + "/out/out.jsonl").read())

cfg = {"sys_path": [BASE + "/src", BASE + "/rec"], "modules": ["test_c"], "packages": ["mylib"],
       "src_root": BASE + "/src", "tmp_base": BASE + "/tmp"}
json.dump(cfg, open(BASE + "/cfg.json", "w"))
p = subprocess.Popen([sys.executable, BASE + "/agent.py", "--tg-server", BASE + "/cfg.json"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
print("ready:", p.stdout.readline())
key = "test_c::case_a"
req = {"op": "check", "keys": [key], "expected": {key: {"status": "value", "src": "1", "cls": None}},
       "timeout": 2, "deadline": 10}
t0 = time.time()
p.stdin.write((json.dumps(req) + "\n").encode()); p.stdin.flush()
print("unmutated check:", p.stdout.readline(), "%.1fs" % (time.time() - t0))
p.stdin.write(b'{"op": "quit"}\n'); p.stdin.flush(); p.wait()
