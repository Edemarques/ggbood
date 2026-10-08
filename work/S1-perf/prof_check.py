import importlib.util, os, sys, time, types, random, math, json, cProfile, pstats
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", SRC)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
random.seed(1)
def val(n):
    return {"items": [random.random() for _ in range(n)], "names": ["n%d" % i for i in range(n)],
            "pairs": [(i, str(i)) for i in range(n // 2)], "ok": True}
srcs = [agent._to_src(val(random.choice([1, 5, 20, 60]))) for _ in range(400)]
mod = types.ModuleType("cases_x")
expected = {}
for i, s in enumerate(srcs):
    v = eval(s, dict(agent._EVAL_NS))
    exec("def case_%d():\n    return V\n" % i, {"V": v}, mod.__dict__)
    expected["cases_x::case_%d" % i] = {"status": "value", "src": s}
ns = {"math": math}
exec(agent.SAME_SRC, ns)
same = ns["_same_value"]
r, w = os.pipe()
out = os.fdopen(w, "w", buffering=1)
import threading
def drain():
    while os.read(r, 65536):
        pass
threading.Thread(target=drain, daemon=True).start()
def emit(obj):
    out.write(json.dumps(obj) + "\n")
cfg = {"cases": list(expected), "expected": expected, "stop_on_first": True}
for rep in range(2):
    t0 = time.perf_counter()
    agent._run_cases(cfg, "check", {"cases_x": mod}, emit, same)
    print("run %.4fs" % (time.perf_counter() - t0))
cProfile.run('agent._run_cases(cfg, "check", {"cases_x": mod}, emit, same)', "/tmp/s1prof")
pstats.Stats("/tmp/s1prof").sort_stats("tottime").print_stats(12)
