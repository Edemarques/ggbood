import importlib.util, os, sys, time, types, random, math
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", SRC)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

random.seed(1)
# realistic recorded values: dicts / lists of mixed data of various sizes
def val(n):
    return {"items": [random.random() for _ in range(n)], "names": ["n%d" % i for i in range(n)],
            "pairs": [(i, str(i)) for i in range(n // 2)], "ok": True}
srcs = [agent._to_src(val(random.choice([1, 5, 20, 60]))) for _ in range(400)]
srcs = [s for s in srcs if len(s) <= agent.MAX_RESULT_CHARS]
print("avg src len", sum(map(len, srcs)) / len(srcs), "n", len(srcs))
t0 = time.perf_counter()
for s in srcs:
    eval(s, dict(agent._EVAL_NS))
t1 = time.perf_counter()
codes = [compile(s, "<x>", "eval") for s in srcs]
t2 = time.perf_counter()
for c in codes:
    eval(c, dict(agent._EVAL_NS))
t3 = time.perf_counter()
print("eval(str) x%d: %.4fs ; eval(precompiled): %.4fs" % (len(srcs), t1 - t0, t3 - t2))

# full _run_cases check-mode cost on trivial cases
mod = types.ModuleType("cases_x")
expected = {}
for i, s in enumerate(srcs):
    v = eval(s, dict(agent._EVAL_NS))
    exec("def case_%d():\n    return V\n" % i, {"V": v}, mod.__dict__)
    expected["cases_x::case_%d" % i] = {"status": "value", "src": s}
ns = {"math": math}
exec(agent.SAME_SRC, ns)
same = ns["_same_value"]
out = []
cfg = {"cases": list(expected), "expected": expected, "stop_on_first": True}
t0 = time.perf_counter()
agent._run_cases(cfg, "check", {"cases_x": mod}, out.append, same)
t1 = time.perf_counter()
print("_run_cases check of %d trivial cases: %.4fs, mismatches %d" % (len(srcs), t1 - t0, len(out) - len(srcs)))

import inspect
fns = [getattr(mod, "case_%d" % i) for i in range(len(srcs))]
t0 = time.perf_counter()
for f in fns:
    inspect.signature(f)
print("inspect.signature x%d: %.4fs" % (len(fns), time.perf_counter() - t0))

# _to_src on large result
for n in (10**5, 10**6):
    v = list(range(n))
    t0 = time.perf_counter()
    s = agent._to_src(v)
    print("_to_src list of %d ints: %.3fs, %d chars" % (n, time.perf_counter() - t0, len(s)))
    v = [{"a": i, "b": [float(i)]} for i in range(n // 10)]
    t0 = time.perf_counter()
    s = agent._to_src(v)
    print("_to_src list of %d dicts: %.3fs, %d chars" % (n // 10, time.perf_counter() - t0, len(s)))

# _public_class_path
import json, email.mime.text, xml.dom.minidom, decimal, http.client
class E(ValueError):
    pass
t0 = time.perf_counter()
for _ in range(100):
    agent._public_class_path(json.JSONDecodeError, {"json"})
print("_public_class_path x100 (sys.modules=%d): %.4fs" % (len(sys.modules), time.perf_counter() - t0))
