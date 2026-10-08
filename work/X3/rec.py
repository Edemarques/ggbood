import os, sys, json, shutil, time, subprocess, sysconfig, py_compile, threading
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "..", "..", "codexv2.12_orig.py")
W = os.path.expanduser("~/tgopt/X3/sim")
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
src = os.path.join(W, "src"); rec = os.path.join(W, "rec")
os.makedirs(src); os.makedirs(rec)
lib = sysconfig.get_paths()["stdlib"]
for m in ("argparse", "fractions", "textwrap"):
    shutil.copy(os.path.join(lib, m + ".py"), os.path.join(src, "my" + m + ".py"))
shutil.copy(AGENT, os.path.join(W, "agent.py"))
exe = py_compile.compile(os.path.join(W, "agent.py"), cfile=os.path.join(W, "agent.pyc"), doraise=True)
NC = int(sys.argv[1]) if len(sys.argv) > 1 else 150
body = ["import myargparse, myfractions, mytextwrap", ""]
keys = []
for i in range(NC):
    k = i % 3
    if k == 0:
        body.append("def test_c%d():\n    p = myargparse.ArgumentParser(prog='x')\n    p.add_argument('--a', type=int, default=%d)\n    p.add_argument('b', nargs='*')\n    return vars(p.parse_args(['--a', '%d', 'x']))\n" % (i, i, i))
    elif k == 1:
        body.append("def test_c%d():\n    s = myfractions.Fraction(0)\n    for j in range(1, %d):\n        s += myfractions.Fraction(1, j)\n    return str(s)\n" % (i, 20 + i % 30))
    else:
        body.append("def test_c%d():\n    return mytextwrap.fill('hello world %d ' * 40, width=%d)\n" % (i, i, 20 + i % 20))
    keys.append("test_sim::test_c%d" % i)
open(os.path.join(rec, "test_sim.py"), "w").write("\n".join(body))
py_compile.compile(os.path.join(rec, "test_sim.py"), doraise=True)

n = [0]
lock = threading.Lock()
def run(cfg, seed="0"):
    with lock:
        n[0] += 1
        d = os.path.join(W, "run%03d" % n[0])
    os.makedirs(os.path.join(d, "tmp")); os.makedirs(os.path.join(d, "out"))
    cfg = dict(cfg, out=os.path.join(d, "out", "out.jsonl"), tmp_base=os.path.join(d, "tmp"),
               packages=["myargparse"], sys_path=[src, rec])
    cp = os.path.join(d, "cfg.json")
    json.dump(cfg, open(cp, "w"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHON"))}
    env.update({"HOME": d, "TMPDIR": d, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": src})
    t = time.perf_counter()
    r = subprocess.run([sys.executable, exe, "--tg-runner", cp], cwd=d, env=env, capture_output=True)
    dt = time.perf_counter() - t
    recs = [json.loads(l) for l in open(cfg["out"])]
    return dt, recs

base = {"mode": "record", "modules": ["test_sim"], "cases": [], "timeout": 5}
dt, _ = run(dict(base))
print("empty runner startup: %.3fs" % dt)
dt, _ = run(dict(base, cases=["test_sim::test_c0"]))
print("1-case runner: %.3fs" % dt)
c1 = dict(base, cases=keys, trace_root=src + os.sep, timeout=15)
c2 = dict(base, cases=list(reversed(keys)), clock_shift=1e9)
c3 = dict(base, cases=keys, clock_shift=1e9 / 37)
t = time.perf_counter()
a = run(c1)[0]; b = run(c2, "4217")[0]; c = run(c3, "91")[0]
seq = time.perf_counter() - t
print("sequential: traced=%.3f run2=%.3f run3=%.3f total=%.3f" % (a, b, c, seq))
res = {}
def go(name, cfg, seed):
    res[name] = run(cfg, seed)[0]
t = time.perf_counter()
ths = [threading.Thread(target=go, args=x) for x in (("a", c1, "0"), ("b", c2, "4217"), ("c", c3, "91"))]
[x.start() for x in ths]; [x.join() for x in ths]
par = time.perf_counter() - t
print("concurrent: %s total=%.3f" % ({k: round(v, 3) for k, v in res.items()}, par))
t = time.perf_counter()
ths = [threading.Thread(target=go, args=x) for x in (("b", c2, "4217"), ("c", c3, "91"))]
a = run(c1)[0]
[x.start() for x in ths]; [x.join() for x in ths]
print("run1 then 2||3: total=%.3f" % (time.perf_counter() - t))
