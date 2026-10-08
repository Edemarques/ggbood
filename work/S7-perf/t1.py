import importlib.util, os
base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("agent", os.path.join(base, "codexv2.12_orig.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

src = '''from mylib import good, Missing

def helper():
    return Missing(3)

def case_a():
    return helper()

def case_b():
    return good(1)

def case_c():
    return Missing(1)
'''
new, using = agent._mend_imports(src, "ImportError: cannot import name 'Missing' from 'mylib'")
print("dropped:", using)
print(new)

# rewrite variant with match capture
lib = b'''def f(v):
    x = 0
    match v:
        case [x]:
            return x
    return x
'''
out = agent._rewrite_variant(lib)
print(out.decode() if out else out)
ns = {}
exec(lib, ns); print("orig", ns["f"]([5]), ns["f"](1))
ns2 = {}
exec(out, ns2); print("variant", ns2["f"]([5]), ns2["f"](1))

print(agent._difference({"status": "value", "src": "1"}, {"status": "error", "error": "took longer than 5 s"}))
print(agent._parse_reply("```python cases_x.py\ndef case_a():\n    return 1\n```\nDONE", None))
