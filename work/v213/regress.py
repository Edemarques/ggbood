"""Regression checks for the bugs found in the S1-S7 / X1-X3 sessions.

usage: python regress.py AGENT.py [NAME ...]

Each check loads the agent from AGENT.py (or runs it as the --tg-runner / --tg-server subprocess)
and prints PASS or FAIL with a short detail. Run it against codexv2.12_orig.py to see the bugs and
against the fixed version to see them gone.
"""
import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import traceback
import urllib.error
import urllib.request

AGENT = os.path.abspath(sys.argv[1])
BASE = tempfile.mkdtemp(prefix="regress-")
os.chmod(BASE, 0o755)
os.environ.setdefault("OPENROUTER_API_KEY", "test")
REAL_URLOPEN = urllib.request.urlopen


def load():
    spec = importlib.util.spec_from_file_location("agent_%d" % time.monotonic_ns(), AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.log = lambda s: None
    return mod


def mkdir(*parts):
    d = os.path.join(BASE, *parts)
    os.makedirs(d, exist_ok=True)
    return d


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(text))


def run_runner(name, cfg, timeout=60, env=None, cases_src=None):
    d = mkdir(name)
    if cases_src is not None:
        write(os.path.join(d, "rec", "test_m.py"), cases_src)
    cfg = dict({"mode": "record", "modules": ["test_m"], "sys_path": [os.path.join(d, "rec")],
                "out": os.path.join(d, "out.jsonl"), "tmp_base": d, "packages": []}, **cfg)
    with open(os.path.join(d, "cfg.json"), "w") as fh:
        json.dump(cfg, fh)
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, AGENT, "--tg-runner", os.path.join(d, "cfg.json")], capture_output=True,
                           timeout=timeout, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
        rc, err = p.returncode, p.stderr.decode(errors="replace")[-400:]
    except subprocess.TimeoutExpired:
        rc, err = None, "timeout"
    wall = time.time() - t0
    recs = []
    if os.path.exists(cfg["out"]):
        recs = [json.loads(line) for line in open(cfg["out"]) if line.strip()]
    return rc, wall, recs, err


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# ---------------------------------------------------------------- runner robustness

@check
def str_of_exception_raises():
    """A raised exception whose __str__ fails must not crash the recording runner (S1-bugs/t3)."""
    rc, wall, recs, err = run_runner("strexc", {"cases": ["test_m::case_a", "test_m::case_b"]}, cases_src='''
        class E(Exception):
            def __init__(self, a):
                super().__init__()
            def __str__(self):
                return self.missing
        def case_a():
            raise E(1)
        def case_b():
            return 1
        ''')
    got = {r["key"]: r["status"] for r in recs if r.get("kind") == "case"}
    ok = got == {"test_m::case_a": "raises", "test_m::case_b": "value"} and recs[-1].get("kind") == "end"
    return ok, "rc=%s cases=%s %s" % (rc, got, err[-150:] if not ok else "")


@check
def alarm_handler_replaced_by_case():
    """A case that installs its own SIGALRM handler must not disable the per-case time limit (S1-perf/bugs)."""
    rc, wall, recs, err = run_runner("alarm", {"cases": ["test_m::case_a", "test_m::case_b", "test_m::case_c"],
                                               "timeout": 1.0}, timeout=30, cases_src='''
        import signal, time
        def case_a():
            signal.signal(signal.SIGALRM, lambda s, f: None)
            return 1
        def case_b():
            while True:
                time.sleep(0.05)
        def case_c():
            return 3
        ''')
    got = {r["key"]: r.get("error", r["status"]) for r in recs if r.get("kind") == "case"}
    ok = "test_m::case_c" in got and "longer" in str(got.get("test_m::case_b")) and wall < 10
    return ok, "wall %.1fs cases=%s" % (wall, got)


@check
def bare_except_swallows_timeout():
    """A case that swallows the time-limit exception must still end (S1-bugs/t4)."""
    rc, wall, recs, err = run_runner("swallow", {"cases": ["test_m::case_s", "test_m::case_after"], "timeout": 1.0},
                                     timeout=30, cases_src='''
        def case_s():
            while True:
                try:
                    sum(range(100000))
                except:
                    pass
        def case_after():
            return 2
        ''')
    got = {r["key"]: r.get("error", r["status"]) for r in recs if r.get("kind") == "case"}
    # either the case is recorded as too slow, or the runner stops soon after it started (the caller then
    # marks the case that started last as hanging and records the rest in a new run)
    last = recs[-1] if recs else {}
    stopped = last.get("kind") == "start" and last.get("key") == "test_m::case_s"
    ok = wall < 8 and ("test_m::case_s" in got or stopped)
    return ok, "wall %.1fs rc=%s cases=%s last=%s" % (wall, rc, got, last)


@check
def non_daemon_thread_keeps_runner_alive():
    """A case that starts a non-daemon timer must not keep the runner process alive (S1-bugs/t4, S7-bugs/x2)."""
    rc, wall, recs, err = run_runner("timer", {"cases": ["test_m::case_t"]}, timeout=40, cases_src='''
        import threading
        def case_t():
            t = threading.Timer(20.0, lambda: None)
            t.start()
            return 1
        ''')
    ok = wall < 8 and any(r.get("kind") == "end" for r in recs)
    return ok, "wall %.1fs rc=%s" % (wall, rc)


@check
def huge_result_is_bounded():
    """Turning a huge result into source must stop early instead of building gigabytes (S1-bugs/t3)."""
    rc, wall, recs, err = run_runner("huge", {"cases": ["test_m::case_big", "test_m::case_ok"]}, timeout=60,
                                     cases_src='''
        def case_big():
            row = list(range(1000))
            return [row] * 12000
        def case_ok():
            return 1
        ''')
    got = {r["key"]: r.get("error", r["status"]) for r in recs if r.get("kind") == "case"}
    ok = "too large" in str(got.get("test_m::case_big")) and got.get("test_m::case_ok") == "value" and wall < 3
    return ok, "wall %.2fs %s" % (wall, {k: str(v)[:60] for k, v in got.items()})


@check
def to_src_matches_repr_semantics():
    """_to_src must still produce the same text as before for ordinary values."""
    agent = load()
    vals = [None, True, 0, -5, 2 ** 70, 1.5, float("nan"), float("inf"), -float("inf"), complex(1, -2), "a'b\n",
            b"\x00x", bytearray(b"ab"), (), (1,), (1, 2), [], [1, [2, (3,)]], {"a": 1, 2: [None]}, set(), {3, 1, 2},
            frozenset(), frozenset({"b", "a"}), {"k": {"n": (1.0, -0.0)}}]
    expect = ["None", "True", "0", "-5", repr(2 ** 70), "1.5", "float('nan')", "float('inf')", "-float('inf')",
              "complex(1.0, -2.0)", repr("a'b\n"), repr(b"\x00x"), "b'ab'", "()", "(1,)", "(1, 2)", "[]",
              "[1, [2, (3,)]]", "{'a': 1, 2: [None]}", "set()", "{1, 2, 3}", "frozenset()", "frozenset({'a', 'b'})",
              "{'k': {'n': (1.0, -0.0)}}"]
    bad = [(v, agent._to_src(v), e) for v, e in zip(vals, expect) if agent._to_src(v) != e]
    errs = []
    for v in (iter([1]), object(), [[[]]] * 2):
        try:
            agent._to_src(v)
        except agent._Unrecordable as u:
            errs.append(str(u)[:30])
    deep = []
    for _ in range(60):
        deep = [deep]
    try:
        agent._to_src(deep)
        deep_err = False
    except agent._Unrecordable:
        deep_err = True
    ok = not bad and len(errs) == 2 and deep_err
    return ok, "bad=%s errs=%s deep_err=%s" % (bad[:3], errs, deep_err)


@check
def server_stdin_is_not_protocol():
    """A case that reads sys.stdin in the change checker must not consume or block the protocol (S2-bugs/exp2)."""
    d = mkdir("stdin")
    write(os.path.join(d, "src", "mylib", "__init__.py"), '''
        import sys
        def run(data=None):
            if data is None:
                data = sys.stdin.read()
            return len(data) + 1
        ''')
    write(os.path.join(d, "rec", "test_c.py"), '''
        from mylib import run
        def case_a():
            return run()
        ''')
    mkdir("stdin", "tmp")
    cfg = {"sys_path": [d + "/src", d + "/rec"], "modules": ["test_c"], "packages": ["mylib"], "src_root": d + "/src",
           "tmp_base": d + "/tmp"}
    json.dump(cfg, open(d + "/cfg.json", "w"))
    p = subprocess.Popen([sys.executable, AGENT, "--tg-server", d + "/cfg.json"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE)
    p.stdout.readline()
    key = "test_c::case_a"
    req = {"op": "check", "keys": [key], "expected": {key: {"status": "value", "src": "1", "cls": None}},
           "timeout": 2, "deadline": 8}
    t0 = time.time()
    p.stdin.write((json.dumps(req) + "\n").encode())
    p.stdin.flush()
    resp = json.loads(p.stdout.readline())
    wall = time.time() - t0
    p.stdin.write((json.dumps(req) + "\n").encode())
    p.stdin.flush()
    resp2 = json.loads(p.stdout.readline())
    p.stdin.write(b'{"op": "quit"}\n')
    p.stdin.flush()
    p.wait(timeout=10)
    kinds = [r["kind"] for r in resp["recs"] if r["kind"] != "start"]
    kinds2 = [r["kind"] for r in resp2["recs"] if r["kind"] != "start"]
    ok = kinds == ["end"] and kinds2 == ["end"] and wall < 2
    return ok, "wall %.1fs first=%s second=%s" % (wall, kinds, kinds2)


@check
def server_function_run_at_import():
    """A function whose result was computed at import must not be swapped in place (S2-bugs/exp1)."""
    d = mkdir("imptime")
    lib = textwrap.dedent('''
        import functools

        def helper(x):
            return x * 2

        TABLE = [helper(i) for i in range(3)]

        @functools.lru_cache(maxsize=None)
        def cached(x):
            return x * 10

        WARM = cached(1)

        def get(i):
            return TABLE[i]

        def other(x):
            return x + 5
        ''')
    write(os.path.join(d, "src", "mylib", "__init__.py"), lib)
    write(os.path.join(d, "rec", "test_c.py"), '''
        from mylib import get, cached, helper, other
        def case_a():
            return get(2)
        def case_b():
            return cached(1)
        def case_h():
            return helper(2)
        def case_o():
            return other(1)
        ''')
    mkdir("imptime", "tmp")
    cfg = {"sys_path": [d + "/src", d + "/rec"], "modules": ["test_c"], "packages": ["mylib"], "src_root": d + "/src",
           "tmp_base": d + "/tmp"}
    json.dump(cfg, open(d + "/cfg.json", "w"))
    p = subprocess.Popen([sys.executable, AGENT, "--tg-server", d + "/cfg.json"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE)
    p.stdout.readline()
    src = lib.encode()
    out = {}
    for old, new, key, exp in (("x * 2", "(x + 1)", "test_c::case_a", "4"), ("x * 2", "(x + 1)", "test_c::case_h", "4"),
                               ("x * 10", "(x + 10)", "test_c::case_b", "10"),
                               ("x + 5", "(x - 5)", "test_c::case_o", "6")):
        start = src.index(old.encode())
        req = {"op": "check", "keys": [key], "expected": {key: {"status": "value", "src": exp, "cls": None}},
               "mut": {"file": "mylib/__init__.py", "start": start, "end": start + len(old),
                       "line": src[:start].count(b"\n") + 1, "repl": new, "default": None}, "timeout": 5,
               "deadline": 10}
        p.stdin.write((json.dumps(req) + "\n").encode())
        p.stdin.flush()
        recs = json.loads(p.stdout.readline())["recs"]
        kinds = {r["kind"] for r in recs}
        out[key] = "unsupported" if "unsupported" in kinds else "killed" if "mismatch" in kinds else "survived"
    p.stdin.write(b'{"op": "quit"}\n')
    p.stdin.flush()
    p.wait(timeout=10)
    # survived would be wrong for a, b and h (helper fills TABLE at import); "unsupported" (slow path) or
    # "killed" are both right; `other` never runs at import, so the fast path must still take it
    ok = all(out[k] != "survived" for k in ("test_c::case_a", "test_c::case_b", "test_c::case_h")) and \
        out["test_c::case_o"] == "killed"
    return ok, str(out)


@check
def server_annotated_class_method():
    """A change inside a method of a class with annotations must use the fast path (3.14 __annotate__)."""
    d = mkdir("annot")
    lib = textwrap.dedent('''
        LIMIT: int = 3

        class Box:
            size: int = 1

            def grow(self, n):
                return n + self.size

            label: str = "b"


        def f(x: int) -> int:
            return x * 2


        def g(x: int = 3) -> int: return x + 1


        def h(x: int = 3,
              y: int = 4) -> int:
            return x * 10 + y
        ''')
    write(os.path.join(d, "src", "mylib", "__init__.py"), lib)
    write(os.path.join(d, "rec", "test_c.py"), '''
        from mylib import Box, f
        def case_a():
            return Box().grow(2)
        def case_f():
            return f(3)
        def case_g():
            return g()
        def case_h():
            return h()
        ''')
    mkdir("annot", "tmp")
    cfg = {"sys_path": [d + "/src", d + "/rec"], "modules": ["test_c"], "packages": ["mylib"], "src_root": d + "/src",
           "tmp_base": d + "/tmp"}
    json.dump(cfg, open(d + "/cfg.json", "w"))
    p = subprocess.Popen([sys.executable, AGENT, "--tg-server", d + "/cfg.json"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE)
    p.stdout.readline()
    src = lib.encode()
    out = {}
    h_line = src[:src.index(b"def h(")].count(b"\n") + 1
    for old, new, key, exp, default in (
            ("n + self.size", "(n - self.size)", "test_c::case_a", "3", None),
            ("x * 2", "(x + 2)", "test_c::case_f", "6", None),
            ("x + 1", "(x - 1)", "test_c::case_g", "4", None),
            ("y: int = 4", "(5)", "test_c::case_h", "34",
             {"name": "h", "line": h_line, "param": "y", "positional": True, "index": 1, "expr": "(5)"})):
        start = src.index(old.encode())
        if default:
            start, old = start + len(old) - 1, "4"
        req = {"op": "check", "keys": [key], "expected": {key: {"status": "value", "src": exp, "cls": None}},
               "mut": {"file": "mylib/__init__.py", "start": start, "end": start + len(old),
                       "line": src[:start].count(b"\n") + 1, "repl": new, "default": default}, "timeout": 5,
               "deadline": 10}
        p.stdin.write((json.dumps(req) + "\n").encode())
        p.stdin.flush()
        recs = json.loads(p.stdout.readline())["recs"]
        kinds = {r["kind"] for r in recs}
        out[key] = ("unsupported: " + recs[0].get("why", "")) if "unsupported" in kinds else \
            "killed" if "mismatch" in kinds else "survived"
    p.stdin.write(b'{"op": "quit"}\n')
    p.stdin.flush()
    p.wait(timeout=10)
    ok = all(v == "killed" for v in out.values())
    return ok, str(out)


@check
def absent_find_spec():
    """With optional packages hidden, importlib.util.find_spec must return None, as when not installed (S1-bugs)."""
    agent = load()
    d = mkdir("absent")
    with open(os.path.join(d, "sitecustomize.py"), "w") as fh:
        fh.write(agent._ABSENT_SRC % json.dumps(["json5x", "csv"]))
    code = ("import importlib.util as u\nprint(u.find_spec('csv') is None, u.find_spec('json') is not None)\n"
            "try:\n import csv\n print('imported')\nexcept ImportError:\n print('ImportError')\n")
    r = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=d), capture_output=True,
                       text=True)
    ok = r.stdout.split() == ["True", "True", "ImportError"]
    return ok, "%s %s" % (r.stdout.split(), r.stderr.strip()[-160:])


@check
def absent_keeps_pytest_plugins():
    """Pytest plugins installed next to pytest must not be hidden: pytest loads them at start (S1-bugs/t1)."""
    d = mkdir("plug")
    site = os.path.join(d, "site")
    write(os.path.join(site, "fakeplug_tg.py"), "def pytest_configure(config):\n    pass\n")
    di = os.path.join(site, "fakeplug_tg-1.0.dist-info")
    write(os.path.join(di, "METADATA"), "Metadata-Version: 2.1\nName: fakeplug-tg\nVersion: 1.0\n")
    write(os.path.join(di, "top_level.txt"), "fakeplug_tg\n")
    write(os.path.join(di, "entry_points.txt"), "[pytest11]\nfakeplug_tg = fakeplug_tg\n")
    write(os.path.join(di, "RECORD"), "fakeplug_tg.py,,\n")
    code = ("import sys, importlib.util as u; s = u.spec_from_file_location('a', %r); a = u.module_from_spec(s); "
            "s.loader.exec_module(a); import json; print(json.dumps(a._optional_modules([], ['mylib'])))" % AGENT)
    r = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=site), capture_output=True,
                       text=True)
    optional = json.loads(r.stdout.strip().split("\n")[-1]) if r.stdout.strip() else None
    ok = optional is not None and "fakeplug_tg" not in optional
    return ok, "fakeplug_tg optional: %s %s" % (optional is not None and "fakeplug_tg" in optional, r.stderr[-200:])


@check
def conftest_no_cascade():
    """One test over the time limit must not fail every later test (S5-bugs/cascade)."""
    agent = load()
    conf = agent.CONFTEST_SRC.replace("TIME_LIMIT_SECONDS = 10", "TIME_LIMIT_SECONDS = 1")
    d = mkdir("cascade")
    t = os.path.join(d, "tests")
    write(os.path.join(t, "conftest.py"), conf)
    write(os.path.join(t, "test_a.py"), "import time\ndef test_1():\n    assert True\ndef test_2():\n    time.sleep(3)\n"
                                        "def test_3():\n    assert True\n")
    write(os.path.join(t, "test_b.py"), "def test_5():\n    assert True\n")
    r = subprocess.run([sys.executable, "-m", "pytest", t, "-q", "-p", "no:cacheprovider", "-c", os.devnull,
                        "--rootdir=" + d], capture_output=True, text=True)
    last = r.stdout.strip().split("\n")[-1]
    ok = "1 failed, 3 passed" in last
    return ok, last


# ---------------------------------------------------------------- case files and generated tests

@check
def future_import_in_case_file():
    """`from __future__ import annotations` in a case file must give a valid test module (S7-bugs/x1)."""
    agent = load()
    src = "from __future__ import annotations\nfrom json import loads\n\ndef case_a():\n    return loads('[1]')\n"
    try:
        text = agent._test_module_source(src, "a", agent._test_names(src), {"case_a"},
                                         {"case_a": {"status": "value", "src": "[1]"}})
        compile(text, "t", "exec")
        return True, "ok"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


@check
def rewrite_variant_match_capture():
    """The renamed-locals copy must behave like the original with match/case captures (S7-perf/t1)."""
    agent = load()
    lib = b"def f(v):\n    x = 0\n    z = 1\n    match v:\n        case [x]:\n            return x * z\n" \
          b"        case {'k': y, **rest}:\n            return (y, rest, z)\n    return x + z\n"
    out = agent._rewrite_variant(lib)
    if out is None or b"z_rw" not in out:
        return False, "the function was not rewritten at all"
    a, b = {}, {}
    exec(lib, a)
    exec(out, b)
    probes = ([5], 1, {"k": 2, "z": 3})
    ok = all(a["f"](p) == b["f"](p) for p in probes)
    return ok, "orig=%s variant=%s" % ([a["f"](p) for p in probes], [b["f"](p) for p in probes])


@check
def difference_message_for_error():
    """A run that did not finish must be described as such, not as 'raised None' (S7-perf/t1)."""
    agent = load()
    msg = agent._difference({"status": "value", "src": "1"}, {"status": "error", "error": "took longer than 5 s"})
    ok = "None" not in msg and "longer" in msg
    return ok, msg


@check
def mend_drops_cases_using_broken_helper():
    """Dropping an unimportable name must also drop cases that use it through a helper (S7-perf/t1)."""
    agent = load()
    src = ("from mylib import good, Missing\n\ndef helper():\n    return Missing(3)\n\ndef case_a():\n"
           "    return helper()\n\ndef case_b():\n    return good(1)\n\ndef case_c():\n    return Missing(1)\n")
    new, using = agent._mend_imports(src, "ImportError: cannot import name 'Missing' from 'mylib'")
    ok = sorted(using) == ["case_a", "case_c"] and "def case_b" in new and "case_a" not in new
    return ok, "dropped=%s" % using


def bare_run(agent):
    r = agent.Run.__new__(agent.Run)
    for a in ("case_sources", "records", "excluded", "case_problems", "module_problems", "case_order", "test_names",
              "case_owner"):
        setattr(r, a, {})
    r.cases_dir = mkdir("cases_%d" % time.monotonic_ns())
    r.pool_threads = []
    r.absent_dir = None
    return r


@check
def keep_previous_no_duplicates():
    """Replacing a file must not keep old cases twice: broken block / unsaved reply (S4-bugs/x)."""
    agent = load()
    old = "import json\n\ndef case_a():\n    return 1\n\ndef case_b():\n    return 2\n"
    r = bare_run(agent)
    r.add_case_file("cases_x.py", old)
    r.case_order["cases_x"] = ["case_a", "case_b"]
    r.records.update({"cases_x::case_a": {"status": "value"}, "cases_x::case_b": {"status": "value"}})
    new1 = "import json\n\ndef case_a():\n    return 10\n\ndef case_b():\n    return 20\n\ndef case_c():\n" \
           "    return \"unterminated\n"
    kept1 = r.keep_previous_cases("cases_x", new1)
    r.add_case_file("cases_x.py", new1)
    r2 = bare_run(agent)
    r2.add_case_file("cases_x.py", old)
    r2.case_order["cases_x"] = ["case_a", "case_b"]
    r2.records.update({"cases_x::case_a": {"status": "value"}, "cases_x::case_b": {"status": "value"}})
    new2 = "def test_a():\n    assert 1 == 1\n"
    kept2 = r2.keep_previous_cases("cases_x", new2)
    err2 = r2.add_case_file("cases_x.py", new2)
    ok = kept1 is None and kept2 is None and bool(err2) and sorted(r2.case_sources) == ["cases_x"]
    return ok, "kept1=%s kept2=%s files2=%s" % (kept1 and kept1[0], kept2 and kept2[0], sorted(r2.case_sources))


@check
def mend_report_consistent():
    """After an import mend, recorded cases must not also be reported as dropped (S4-bugs/y, S6-bugs/mend)."""
    agent = load()
    r = bare_run(agent)
    r.case_sources["cases_x"] = ("from lib import a, Foo\n\ndef case_one():\n    return a(1)\n\ndef case_two():\n"
                                 "    return a(2)\n\ndef case_foo():\n    return Foo()\n")

    def lint(src):
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
                out["__module_errors__"].setdefault(mod, [
                    "importing the file failed: ImportError: cannot import name 'Foo' from 'lib'"])
                out[k] = {"key": k, "status": "error", "error": "case not found"}
            else:
                out[k] = {"key": k, "status": "value", "src": "1"}
        return out
    r._record = _record
    rep = r.record_modules(["cases_x"])
    both = sorted(set(r.records) & set(r.case_problems))
    probs = rep["cases_x"]["problems"]
    ok = not both and sorted(r.records) == ["cases_x::case_one", "cases_x::case_two"] and \
        not any("case_one" in p or "case_two" in p for p in probs)
    return ok, "both=%s problems=%s" % (both, probs)


@check
def record_flow_with_real_runner():
    """Recording through Run.record_modules with the real runner: an import-broken name and a case that
    swallows its time limit (S4-perf/mend, S1-bugs/t4)."""
    agent = load()
    repo = mkdir("flowrepo")
    write(os.path.join(repo, "mylib", "__init__.py"), "def f1(x):\n    return x + 1\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    for dirpath, dirs, files in os.walk(BASE):
        os.chmod(dirpath, 0o755)
        for f in files:
            os.chmod(os.path.join(dirpath, f), 0o644)
    old_env = dict(os.environ)
    os.environ.update({"AGENT_TIMEOUT": "1800", "PYTHONPATH": repo})
    try:
        run = agent.Run("Write tests for the repository at `%s`. Add files only under `tests/`. Import from "
                        "`mylib`." % repo)
        run.setup()
        run.add_case_file("cases_x.py", "from mylib import f1, nothere\n\n\ndef case_a():\n    return f1(1)\n\n\n"
                                        "def case_b():\n    return f1(2)\n\n\ndef case_c():\n    return nothere(3)\n")
        run.add_case_file("cases_y.py", "from mylib import f1\n\n\ndef case_ok():\n    return f1(5)\n\n\n"
                                        "def case_hang():\n    while True:\n        try:\n"
                                        "            sum(range(10000))\n        except:\n            pass\n"
                                        "    return 1\n\n\n"
                                        "def case_after():\n    return f1(6)\n")
        t0 = time.time()
        rep = run.record_modules(["cases_x", "cases_y"])
        secs = time.time() - t0
    finally:
        os.environ.clear()
        os.environ.update(old_env)
    recorded = sorted(run.records)
    both = sorted(set(run.records) & set(run.case_problems))
    want = ["cases_x::case_a", "cases_x::case_b", "cases_y::case_after", "cases_y::case_ok"]
    hang = run.case_problems.get("cases_y::case_hang", "")
    ok = recorded == want and not both and "cases_x::case_c" in run.case_problems and hang and secs < 90 \
        and rep["cases_x"]["cases"] == 3
    return ok, "%.0fs recorded=%s both=%s hang=%r x=%s" % (secs, recorded, both, hang[:60], rep.get("cases_x"))


# ---------------------------------------------------------------- orchestration

class _Resp:
    def __init__(self, b):
        self.b = b

    def read(self, *a):
        return self.b

    def read1(self, *a):
        b, self.b = self.b, b""
        return b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _llm_scenario(agent, decide, n=4):
    barrier = threading.Barrier(n)

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data)
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.02)
        return decide(req.full_url, body["model"])
    urllib.request.urlopen = fake_urlopen
    agent.LLM._retry_pause = lambda self, failures: failures < 4
    llm = agent.LLM(agent.MODEL, 1.0, time.time() + 1000)
    res = [None] * n

    def w(i):
        res[i] = llm.ask("hi", conv=[{"role": "system", "content": "s"}])[0]
    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    try:
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        urllib.request.urlopen = REAL_URLOPEN
    return llm, res


_OK = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                  "usage": {"cost": 0.001}}).encode()


@check
def llm_model_refused_race():
    """Four writers hitting a refused model together must end on the first fallback, not past it (S3/X1)."""
    os.environ.pop("SANDBOX_PROXY_URL", None)
    agent = load()
    agent.FALLBACK_MODELS[:] = ["fb/one", "fb/two", "fb/three"]

    def decide(url, model):
        if model == agent.MODEL:
            raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(
                b'{"error":{"message":"%s is not a valid model ID"}}' % model.encode()))
        return _Resp(_OK)
    llm, res = _llm_scenario(agent, decide)
    ok = res == ["ok"] * 4 and llm.model == "fb/one" and not llm.exhausted
    return ok, "replies=%s model=%s exhausted=%s" % (res, llm.model, llm.exhausted)


@check
def llm_proxy_down_race():
    """Four writers finding the proxy unreachable together must all move to the working endpoint (S3-bugs/race2)."""
    os.environ["SANDBOX_PROXY_URL"] = "http://proxy.invalid"
    try:
        agent = load()

        def decide(url, model):
            if "proxy.invalid" in url:
                raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
            return _Resp(_OK)
        results = []
        for _ in range(3):
            llm, res = _llm_scenario(agent, decide)
            results.append((res == ["ok"] * 4, "proxy" not in llm.urls[llm.url_i]))
        llm, res = _llm_scenario(agent, lambda url, model: (_ for _ in ()).throw(
            urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"Not Found"))) if "proxy" in url else _Resp(_OK))
        results.append((res == ["ok"] * 4, "proxy" not in llm.urls[llm.url_i]))
    finally:
        os.environ.pop("SANDBOX_PROXY_URL", None)
    ok = all(a and b for a, b in results)
    return ok, str(results)


@check
def llm_trickling_response_bounded():
    """A response that trickles in must not hold a writer thread past the call's time limit (S3-perf/trickle)."""
    import http.server
    import socketserver

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b" " * 80 + _OK
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for _ in range(80):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.5)
                self.wfile.write(_OK)
            except OSError:
                pass

        def log_message(self, *a):
            pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    urllib.request.urlopen = REAL_URLOPEN
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["SANDBOX_PROXY_URL"] = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        agent = load()
        agent.LLM._retry_pause = lambda self, failures: False
        llm = agent.LLM(agent.MODEL, 1.0, time.time() + 1000)
        llm.urls = llm.urls[:1]
        box = {}

        def w():
            t0 = time.time()
            # the call's own limit is max(20, remaining - 15); a deadline 33 s away makes it 20 s
            llm.deadline = time.time() + 33
            if "conv" in agent.LLM.ask.__code__.co_varnames:
                box["reply"] = llm.ask("hi", conv=[{"role": "system", "content": "s"}])[0]
            else:  # codexv2.12_ lineage: one conversation per LLM object
                llm.messages = [{"role": "system", "content": "s"}]
                box["reply"] = llm.ask("hi")[0]
            box["secs"] = time.time() - t0
        t = threading.Thread(target=w, daemon=True)
        t.start()
        t.join(60)
    finally:
        os.environ.pop("SANDBOX_PROXY_URL", None)
        srv.shutdown()
    secs = box.get("secs")
    ok = secs is not None and secs < 30
    return ok, "secs=%s reply=%s" % (secs and round(secs, 1), box.get("reply"))


@check
def verify_suite_overhead_loop():
    """A suite slow only because of pytest start-up must be accepted, not re-run 8 times (S5-perf/verify)."""
    agent = load()
    r = agent.Run.__new__(agent.Run)
    r.suite_limit = 10.0
    r.excluded = {}
    r.best_files = {"OLD": "checkpoint suite"}
    runs = []
    r.end_left = lambda: 1000
    r.rebuild_coverage = lambda: None
    r.build_suite = lambda: ({"test_a.py": "x", "conftest.py": "c"},
                             {"test_a::test_%d" % i: "cases_a::c%d" % i for i in range(20)})

    def ci_run(files, hash_seed="0", src_root=None, absent=False):
        runs.append(hash_seed)
        return {"rc": 0, "tests": {"test_a::test_%d" % i: {"outcome": "passed", "time": 0.1} for i in range(20)},
                "secs": 5.0, "cpu": 4.8, "output": "", "collect_errors": []}
    r.ci_run = ci_run
    out = agent.Run._verify_suite(r, 2)
    ok = len(runs) <= 3 and out != {"OLD": "checkpoint suite"} and not r.excluded
    return ok, "pytest runs=%d returned=%s excluded=%d" % (len(runs), list(out)[:2], len(r.excluded))


@check
def pool_no_duplicate_checks():
    """Re-submitting unchecked changes must not check a queued change twice (S6-perf/pooldup)."""
    import collections
    agent = load()
    r = object.__new__(agent.Run)
    r.pool_cv = threading.Condition()
    r.pool_queue = []
    r.pool_seq = 0
    r.pool_threads = []
    r.pool_stop = False
    r.pool_paused = False
    r.pool_busy = 0
    r.pool_running = {}
    r.mutant_status, r.mutant_full, r.servers, r.func_feedback = {}, {}, {}, {}
    r.scope_files = []
    r._scope_words = set()
    r.deadline = time.time() + 1000
    n = 200
    r.mutants = {"m%d" % i: {"id": "m%d" % i, "func": "f", "file": "a.py", "module_level": False}
                 for i in range(1, n + 1)}
    calls = collections.Counter()
    lock = threading.Lock()

    def check_mutant(m, root, full):
        with lock:
            calls[m["id"]] += 1
        time.sleep(0.01)
        return "survived"
    r.check_mutant = check_mutant
    for i in range(4):
        t = threading.Thread(target=r._pool_worker, args=("w%d" % i,), daemon=True)
        t.start()
        r.pool_threads.append(t)
    r.pool_submit(list(r.mutants.values()), foreground=False)
    time.sleep(0.1)
    for _ in range(2):
        unchecked = [m for m in r.mutants.values() if m["id"] not in r.mutant_status]
        r.pool_submit(unchecked, foreground=False)
        time.sleep(0.1)
    end = time.time() + 30
    while time.time() < end:
        with r.pool_cv:
            if not r.pool_queue and not r.pool_busy:
                break
        time.sleep(0.01)
    with r.pool_cv:
        r.pool_stop = True
        r.pool_cv.notify_all()
    dup = sum(1 for v in calls.values() if v > 1)
    ok = dup == 0 and len(r.mutant_status) == n
    return ok, "checks=%d duplicated=%d statuses=%d" % (sum(calls.values()), dup, len(r.mutant_status))


@check
def coverage_ignores_lines_without_code():
    """Lines that never produce a line event (global, try:, multi-line headers) must not count as missed (S7-bugs)."""
    agent = load()
    code = textwrap.dedent('''
        G = 0
        def f(x, y):
            global G
            try:
                z = x + 1
            except ValueError:
                pass
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
            return (
                z
            )

        def h():
            x = 0
            def inner():
                nonlocal x
                x += 1
            inner()
            return x
        ''')
    d = mkdir("cov")
    path = os.path.join(d, "zz_cov.py")
    with open(path, "w") as fh:
        fh.write(code)
    ns = {}
    exec(compile(code, path, "exec"), ns)
    tr = agent._LineTracer(d + os.sep)
    tr.start()
    try:
        ns["f"](1, 2)
        ns["h"]()
    finally:
        lines = tr.stop()
    r = agent.Run.__new__(agent.Run)
    r.statement = "Test `f` and `h`."
    r.scope_files = ["zz_cov.py"]
    r.src_root = d
    r.cov_lines = {"zz_cov.py": {n for _, n in lines}}
    rep = r.coverage_report()
    # line 8 (`pass` in the handler) really never runs
    ok = rep.strip() == "zz_cov.py: f: lines 8 never run"
    return ok, rep.replace("\n", " | ")[:300]


def main():
    names = sys.argv[2:]
    failed = 0
    for fn in CHECKS:
        if names and fn.__name__ not in names:
            continue
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception:
            ok, detail = False, "check raised %s" % traceback.format_exc()[-600:]
        failed += not ok
        print("%s %-40s %5.1fs  %s" % ("PASS" if ok else "FAIL", fn.__name__, time.time() - t0, detail), flush=True)
    shutil.rmtree(BASE, ignore_errors=True)
    print("%d of %d checks failed" % (failed, len([f for f in CHECKS if not names or f.__name__ in names])))


if __name__ == "__main__":
    main()
