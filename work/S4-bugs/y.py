import importlib.util, os, tempfile, sys, ast
path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

R = agent.Run
r = object.__new__(R)
for a in ("case_sources", "records", "excluded", "case_problems", "module_problems", "case_order", "test_names"):
    setattr(r, a, {})
r.pool_threads = []
r.absent_dir = None
src = '''from pkg import good, missing_name

def case_a():
    return good(1)

def case_b():
    return good(2)

def case_c():
    return missing_name()
'''
r.case_sources["cases_x"] = src

def lint(source):
    tree = ast.parse(source)
    cases = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("case_")]
    return [], {}, cases
r.lint = lint
r.write_record_module = lambda name, keep: None
r.rebuild_coverage = lambda: None
calls = {"n": 0}

def _record(keys, **kw):
    calls["n"] += 1
    if calls["n"] <= 3:  # first record_modules: import fails
        out = {k: {"key": k, "status": "error", "error": "case not found"} for k in keys}
        out["__module_errors__"] = {"cases_x": ["importing the file failed: ImportError: cannot import name 'missing_name' from 'pkg'"]} if kw.get("trace") else {}
        return out
    out = {k: {"key": k, "status": "value", "src": "1"} for k in keys}
    out["__module_errors__"] = {}
    return out
r._record = _record
rep = r.record_modules(["cases_x"])
print("report:", rep)
print("records:", sorted(r.records))
print("case_problems:", r.case_problems)
