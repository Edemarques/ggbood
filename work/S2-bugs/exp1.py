import json, os, shutil, subprocess, sys

BASE = os.path.expanduser("~/tgopt/S2-bugs")
CWD = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58"
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE + "/src/mylib")
os.makedirs(BASE + "/rec")
os.makedirs(BASE + "/tmp")
shutil.copy(CWD + "/codexv2.12_orig.py", BASE + "/agent.py")
LIB = '''import functools

def helper(x):
    return x * 2

TABLE = [helper(i) for i in range(3)]

@functools.lru_cache(maxsize=None)
def cached(x):
    return x * 10

WARM = cached(1)

def get(i):
    return TABLE[i]
'''
open(BASE + "/src/mylib/__init__.py", "w").write(LIB)
open(BASE + "/rec/test_c.py", "w").write('''from mylib import get, cached, helper
def case_a():
    return get(2)
def case_b():
    return cached(1)
def case_h():
    return helper(2)
''')
cfg = {"sys_path": [BASE + "/src", BASE + "/rec"], "modules": ["test_c"], "packages": ["mylib"],
       "src_root": BASE + "/src", "tmp_base": BASE + "/tmp"}
json.dump(cfg, open(BASE + "/cfg.json", "w"))
p = subprocess.Popen([sys.executable, BASE + "/agent.py", "--tg-server", BASE + "/cfg.json"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
print("ready:", p.stdout.readline())
src = LIB.encode()

def mut(old, new, key, exp):
    start = src.index(old.encode())
    end = start + len(old)
    line = src[:start].count(b"\n") + 1
    req = {"op": "check", "keys": [key], "expected": {key: {"status": "value", "src": exp, "cls": None}},
           "mut": {"file": "mylib/__init__.py", "start": start, "end": end, "line": line, "repl": new,
                   "default": None}, "timeout": 5, "deadline": 10}
    p.stdin.write((json.dumps(req) + "\n").encode()); p.stdin.flush()
    print(old, "->", new, key, p.stdout.readline())

mut("x * 2", "(x + 1)", "test_c::case_a", "4")   # slow path: TABLE=[1,2,3] -> 3 != 4 -> killed
mut("x * 2", "(x + 1)", "test_c::case_h", "4")   # control: runtime call -> killed
mut("x * 10", "(x + 10)", "test_c::case_b", "10")  # slow: 11 != 10 -> killed
p.stdin.write(b'{"op": "quit"}\n'); p.stdin.flush(); p.wait()
