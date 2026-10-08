import importlib.util, time, json, random, os
path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)

class R: pass
r = R()
r.case_sources = {}
r.records = {}
r.test_names = {}
keys = []
random.seed(1)
for mi in range(10):
    mod = "cases_topic%d" % mi
    src = "from lib import x\n\n" + "".join("def case_c%d():\n    return x(%d, 'abc', [1,2,3])\n\n" % (i, i) for i in range(50))
    src = src * 3  # ~ make it bigger
    r.case_sources[mod] = src
    r.test_names[mod] = {}
    for ci in range(50):
        c = "case_c%d" % ci
        r.test_names[mod][c] = "test_c%d" % ci
        k = "%s::%s" % (mod, c)
        n = random.choice([20, 50, 200, 1000, 3000])
        r.records[k] = {"status": "value", "src": repr(["x" * 10] * (n // 14)), "secs": 0.002, "cls": None}
        keys.append(k)
print("case src len", len(src), "keys", len(keys))
Run = agent.Run
for name in ("check_payload", "pins_classes", "test_key", "test_module"):
    setattr(R, name, getattr(Run, name))
N = 50
t = time.perf_counter()
for _ in range(N):
    expected, back, secs, longest = r.check_payload(keys[:400])
t1 = time.perf_counter()
for _ in range(N):
    s = json.dumps({"op": "check", "keys": list(expected), "expected": expected})
t2 = time.perf_counter()
for _ in range(N):
    json.loads(s)
t3 = time.perf_counter()
print("payload bytes", len(s))
print("check_payload %.2f ms, dumps %.2f ms, loads %.2f ms" % ((t1 - t) / N * 1e3, (t2 - t1) / N * 1e3, (t3 - t2) / N * 1e3))
