import os, sys, json, subprocess, time, shutil, select

HERE = os.path.dirname(os.path.abspath(__file__))
agent = sys.argv[1]
env_extra = dict(a.split("=", 1) for a in sys.argv[2:])
base = os.path.join(HERE, "e2e")
shutil.rmtree(base, ignore_errors=True)
lib = os.path.join(base, "lib", "toylib")
tests = os.path.join(base, "tests")
os.makedirs(lib)
os.makedirs(tests)
os.makedirs(os.path.join(base, "tmp"))
LIB = '''
import functools

LIMIT = 7

def build(n, k=3):
    out = []
    for i in range(n):
        d = {"i": i, "sq": i * i + 1, "tags": [str(i % k), "x"]}
        out.append(d)
    return out

def summarize(rows):
    total = 0
    for r in rows:
        if r["i"] % 2 == 0:
            total += r["sq"]
    return {"total": total, "count": len(rows), "limit": LIMIT}

@functools.lru_cache(maxsize=None)
def cached(x):
    return x * 3 + 1

class Thing:
    SCALE = 2
    def __init__(self, v):
        self.v = v
    def grow(self):
        return [self.v * self.SCALE + j for j in range(5)]
'''
open(os.path.join(lib, "__init__.py"), "w").write(LIB)
cases = ["import toylib\n"]
NC = 120
for c in range(NC):
    cases.append('''
def case_%d():
    rows = toylib.build(%d)
    junk = [[{"a": [j, {"b": (j,)}]} for j in range(300)] for _ in range(10)]
    return [toylib.summarize(rows), rows[-1], toylib.cached(%d), toylib.Thing(%d).grow(), len(junk)]
''' % (c, 40 + c, c, c))
open(os.path.join(tests, "test_toy.py"), "w").write("".join(cases))

# compute expected
sys.path.insert(0, os.path.join(base, "lib"))
sys.path.insert(0, tests)
import test_toy
expected = {}
keys = []
for c in range(NC):
    key = "test_toy::case_%d" % c
    keys.append(key)
    expected[key] = {"status": "value", "src": repr(getattr(test_toy, "case_%d" % c)()), "cls": None}

cfg = {"sys_path": [os.path.join(base, "lib"), tests], "modules": ["test_toy"], "packages": ["toylib"],
       "src_root": os.path.join(base, "lib"), "tmp_base": os.path.join(base, "tmp")}
cfgp = os.path.join(base, "cfg.json")
json.dump(cfg, open(cfgp, "w"))
env = dict(os.environ, **env_extra)
env["PYTHONPATH"] = os.path.join(base, "lib")
p = subprocess.Popen([sys.executable, agent, "--tg-server", cfgp], cwd=base, stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, env=env)
print("ready:", p.stdout.readline().decode().strip())
src = LIB.encode()

def mut(old, new, nth=0):
    i = -1
    for _ in range(nth + 1):
        i = src.index(old, i + 1)
    line = src[:i].count(b"\n") + 1
    return {"file": "toylib/__init__.py", "start": i, "end": i + len(old), "line": line, "repl": new, "default": None}

muts = [mut(b"i * i + 1", "i * i + 2"), mut(b"% 2 == 0", "% 2 != 0"), mut(b"LIMIT = 7", "LIMIT = 8"),
        mut(b"x * 3 + 1", "x * 3 - 1"), mut(b"SCALE = 2", "SCALE = 3"), mut(b"range(5)", "range(4)"),
        mut(b'"x"]', '"y"]'), None]
out = []
t0 = time.time()
for rep in range(3):
    for m in muts:
        for subset in (keys, keys[-1:]):
            req = {"op": "check", "keys": subset, "expected": {k: expected[k] for k in subset},
                   "stop_on_first": True, "timeout": 5, "deadline": 60}
            if m:
                req["mut"] = m
            p.stdin.write((json.dumps(req) + "\n").encode())
            p.stdin.flush()
            resp = json.loads(p.stdout.readline())
            out.append([r for r in resp["recs"] if r.get("kind") != "start"])
# survivor-like: no mutation, all keys (runs all cases)
t1 = time.time()
for rep in range(10):
    req = {"op": "check", "keys": keys, "expected": expected, "stop_on_first": True, "timeout": 5, "deadline": 60}
    p.stdin.write((json.dumps(req) + "\n").encode())
    p.stdin.flush()
    resp = json.loads(p.stdout.readline())
    out.append([r for r in resp["recs"] if r.get("kind") != "start"])
t2 = time.time()
p.stdin.write(b'{"op": "quit"}\n')
p.stdin.flush()
p.wait()
print("mutant phase %.3fs  full-suite phase %.3fs (10 runs)" % (t1 - t0, t2 - t1))
json.dump(out, open(os.path.join(HERE, "out_%s.json" % os.path.basename(agent).replace(".py", "") + "_".join(sys.argv[2:])), "w"))
print("recs sample:", out[:8])
