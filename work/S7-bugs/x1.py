import importlib.util, sys, ast, os
path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
print(sys.version)

# 1. __future__ import in case file
src = '''from __future__ import annotations
from json import loads

def case_a():
    return loads("[1]")
'''
names = agent._test_names(src)
try:
    agent._test_module_source(src, "a", names, {"case_a"})
    print("future: OK")
except Exception as e:
    print("future: FAIL", type(e).__name__, e)

# 2. coverage of stmt lines
code = '''
G = 0
def f(x):
    global G
    try:
        y = x + 1
    except ValueError:
        pass
    for i in range(2):
        pass
    with open("/dev/null") as fh:
        pass
    if x:
        pass
    return y
'''
tree = ast.parse(code)
fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]
stmts = sorted(set(agent._stmt_lines(fn.body)))
ns = {}
co = compile(code, "/tmp/zz_cov.py", "exec")
exec(co, ns)
tr = agent._LineTracer("/tmp/")
tr.start()
try:
    ns["f"](1)
finally:
    lines = tr.stop()
cov = {n for _, n in lines}
print("stmts", stmts, "covered", sorted(cov), "missed", [n for n in stmts if n not in cov])

# 3. _repair_source scenarios
s3 = '''import json

def case_a():
    return json.loads("[1]")

Here is the next part:

def case_b():
    return 1 +

def case_c():
    return 3
'''
print(repr(agent._repair_source(s3)))
