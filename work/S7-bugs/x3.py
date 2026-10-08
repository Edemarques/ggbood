import importlib.util, sys, ast, os
path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

code = '''
G = 0
def f(x, y):
    nonlocal_dummy = 1
    if (
        x
        and y
    ):
        z = 1
    w = (
        x
        + y
    )
    ...
    5
    q = [
        i for i in range(2)
    ]
    return (
        x
    )

def g():
    ...

def h():
    x = 0
    def inner():
        nonlocal x
        x += 1
    inner()
    return x
'''
tree = ast.parse(code)
ns = {}
co = compile(code, "/tmp/zz_cov3.py", "exec")
exec(co, ns)
tr = agent._LineTracer("/tmp/")
tr.start()
try:
    ns["f"](1, 2)
    ns["g"]()
    ns["h"]()
finally:
    lines = tr.stop()
cov = {n for _, n in lines}
for qual, fn in agent._functions(tree):
    stmts = sorted(set(agent._stmt_lines(fn.body)))
    print(qual, "stmts", stmts, "missed", [n for n in stmts if n not in cov])
