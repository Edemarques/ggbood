import importlib.util, os, sys
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", P)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

Run = agent.Run
r = Run.__new__(Run)
r.case_sources = {"cases_x": "from lib import a, Foo\n\ndef case_one():\n    return a(1)\n\ndef case_two():\n    return a(2)\n\ndef case_foo():\n    return Foo()\n"}
r.case_order = {}
r.module_problems = {}
r.case_problems = {}
r.records = {}
r.test_names = {}
r.absent_dir = None
r.pool_threads = []
r.excluded = {}

def lint(src):
    import ast
    t = ast.parse(src)
    return [], {}, [n.name for n in t.body if isinstance(n, ast.FunctionDef) and n.name.startswith("case_")]
r.lint = lint
r.write_record_module = lambda name, keep: None
r.rebuild_coverage = lambda: None

def _record(keys, **kw):
    out = {"__module_errors__": {}}
    for k in keys:
        mod = k.split("::")[0]
        if "Foo" in r.case_sources[mod]:
            out["__module_errors__"].setdefault(mod, ["importing the file failed: ImportError: cannot import name 'Foo' from 'lib'"])
        else:
            out[k] = {"key": k, "status": "value", "src": "1"}
    if not out["__module_errors__"]:
        out["__module_errors__"] = {}
    return out
r._record = _record

rep = r.record_modules(["cases_x"])
print("report:", rep)
print("records:", sorted(r.records))
print("case_problems:", r.case_problems)
