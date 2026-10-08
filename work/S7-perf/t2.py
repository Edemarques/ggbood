import importlib.util, os, time, ast, cProfile, pstats
base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("agent", os.path.join(base, "codexv2.12_orig.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

parts = ['"""cases"""', "import mylib", "from mylib import thing", "", "def make(n):", "    return thing.Builder(n)", ""]
for i in range(40):
    parts += ["def case_c%d():" % i, "    b = make(%d)" % i, "    b.add('x', [1, 2, 3], key=%d)" % i,
              "    out = b.build()", "    return {'a': out.a, 'b': list(out.items()), 'n': len(out)}", ""]
src = "\n".join(parts)
names = agent._test_names(src)
recs = {}
for i in range(40):
    recs["case_c%d" % i] = {"status": "value", "src": "{'a': 1, 'b': [('x', [1, 2, 3])], 'n': %d}" % i} if i % 5 else \
        {"status": "raises", "cls": "mylib.errors.BadError"}
t = time.perf_counter()
for _ in range(50):
    agent._test_module_source(src, "x", names, set(recs), recs)
print("per module %.2f ms" % ((time.perf_counter() - t) / 50 * 1000))
t = time.perf_counter()
for _ in range(50):
    ast.parse(agent.SAME_SRC)
print("SAME_SRC parse %.2f ms, %d chars" % ((time.perf_counter() - t) / 50 * 1000, len(agent.SAME_SRC)))
pr = cProfile.Profile(); pr.enable()
for _ in range(20):
    agent._test_module_source(src, "x", names, set(recs), recs)
pr.disable(); pstats.Stats(pr).sort_stats("tottime").print_stats(8)
