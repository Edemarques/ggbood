import importlib.util, os, time, ast, copy, sys
base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("agent", os.path.join(base, "codexv2.12_orig.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

parts = ['"""cases"""', "import mylib", "from mylib import thing", "", "def make(n):", "    return thing.Builder(n)", ""]
for i in range(40):
    if i % 7 == 3:
        parts += ["def case_c%d(tmp_path):" % i, '    """doc"""', "    if %d > 2:" % i, "        return 1",
                  "    actual = make(%d)" % i, "    return actual", ""]
    else:
        parts += ["def case_c%d():" % i, "    b = make(%d)" % i, "    b.add('x', [1, 2, 3], key=%d)" % i,
                  "    out = b.build()", "    return {'a': out.a, 'b': list(out.items()), 'n': len(out)}", ""]
src = "\n".join(parts)
names = agent._test_names(src)
recs = {}
for i in range(40):
    recs["case_c%d" % i] = {"status": "value", "src": "{'a': 1, 'b': [('x', [1, 2, 3])], 'n': %d}" % i} if i % 5 else \
        {"status": "raises", "cls": "mylib.errors.BadError"}

def run(n=50):
    t = time.perf_counter()
    for _ in range(n):
        out = agent._test_module_source(src, "x", names, set(recs), recs)
        out2 = agent._test_module_source(src, "x", names, set(recs))
    return out + out2, (time.perf_counter() - t) / n * 1000

ref, t_ref = run()
print("orig %.2f ms" % t_ref)

# variant: no deepcopy of body, no fix_missing_locations
class NoCopy:
    def __getattr__(self, k):
        return getattr(copy, k)
    @staticmethod
    def deepcopy(x):
        if isinstance(x, ast.stmt):
            return x
        return copy.deepcopy(x)
agent.copy = NoCopy()
r1, t1 = run()
print("no-stmt-deepcopy %.2f ms same=%s" % (t1, r1 == ref))
agent.ast = type(sys)("astproxy")
agent.ast.__dict__.update(ast.__dict__)
agent.ast.fix_missing_locations = lambda m: m
r2, t2 = run()
print("+no-fix_missing_locations %.2f ms same=%s" % (t2, r2 == ref))
