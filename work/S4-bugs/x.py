import importlib.util, os, tempfile, sys
path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

R = agent.Run
r = object.__new__(R)
r.case_sources = {}
r.cases_dir = tempfile.mkdtemp()
r.records = {}
r.excluded = {}
r.case_problems = {}
r.module_problems = {}
r.case_order = {}

old = '''import json

def case_a():
    return 1

def case_b():
    return 2
'''
print(r.add_case_file("cases_x.py", old))
r.case_order["cases_x"] = ["case_a", "case_b"]
r.records["cases_x::case_a"] = {"status": "value"}
r.records["cases_x::case_b"] = {"status": "value"}

# Scenario 1: new version keeps both cases but has one broken block
new1 = '''import json

def case_a():
    return 10

def case_b():
    return 20

def case_c():
    return "unterminated
'''
kept = r.keep_previous_cases("cases_x", new1)
print("scenario1 kept:", kept)
print("add:", repr(r.add_case_file("cases_x.py", new1)))
print("cases_x:", r.case_sources["cases_x"])
for k, v in r.case_sources.items():
    print("==", k); print(v)

# Scenario 2: writer sends pytest-style tests (no case_) -> new not saved, prev duplicates everything
r2 = object.__new__(R)
for a in ("case_sources", "records", "excluded", "case_problems", "module_problems", "case_order"):
    setattr(r2, a, {})
r2.cases_dir = tempfile.mkdtemp()
r2.add_case_file("cases_x.py", old)
r2.case_order["cases_x"] = ["case_a", "case_b"]
r2.records["cases_x::case_a"] = {"status": "value"}
r2.records["cases_x::case_b"] = {"status": "value"}
new2 = '''def test_a():
    assert 1 == 1
'''
print("scenario2 kept:", r2.keep_previous_cases("cases_x", new2))
print("add:", repr(r2.add_case_file("cases_x.py", new2)))
print(sorted(r2.case_sources))
