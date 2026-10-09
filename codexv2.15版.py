"""up2 — Never-empty resilience.

Critical constraint: if any case was recorded, the agent must ship that suite.
An empty or smoke patch is a harness failure, not a library failure.

Own idea: auto-mend broken case files (drop only the bad block), put optional
packages back when the whole draft dies without them, drop tests that pin
message text or local names (contract + rewrite gates), and always hand in
the last passing suite after a crash. Temperature 0. Reasoning stays high.
"""
from __future__ import annotations

import ast
import copy
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import tokenize
import traceback
import types

_T0 = time.time()

def log(msg: str) -> None:
    el = time.time() - _T0
    sys.stdout.write(f"[{int(el // 60)}:{el % 60:04.1f}] {msg}\n")
    sys.stdout.flush()

_TRANSCRIPT_LOCK = threading.Lock()

def _transcript(role: str, text: str) -> None:
    path = os.getenv("TG_TRANSCRIPT")
    if not path:
        return
    try:
        with _TRANSCRIPT_LOCK, open(path, "a") as fh:
            fh.write("\n\n======== %s (%.0fs) ========\n\n%s\n" % (role, time.time() - _T0, text))
    except OSError:
        pass

MODEL = os.getenv("TG_MODEL") or "openai/gpt-6-luna"
FALLBACK_MODELS = [m.strip() for m in (os.getenv("TG_FALLBACK_MODELS") or "~openai/gpt-luna-latest").split(",")
                   if m.strip()]
REASONING_EFFORT = os.getenv("TG_REASONING") or "high"
ROUND_REASONING = os.getenv("TG_ROUND_REASONING") or ""
SECOND_LOOK_REASONING = os.getenv("TG_SECOND_REASONING") or ""
MAX_OUTPUT_TOKENS = int(os.getenv("TG_MAX_OUTPUT_TOKENS") or 32000)
TEMPERATURE = float(os.getenv("TG_TEMPERATURE") or 0.0)
SEED = int(os.getenv("TG_SEED") or "20261005")

PRICES = {
    "openai/gpt-6-luna": (0.10, 0.50, 0.01),
    "openai/gpt-5.6-luna": (0.20, 1.20, 0.02),
    "deepseek/deepseek-v4.1-flash": (0.03, 0.60, 0.027),
    "z-ai/glm-5.3-flash": (0.15, 0.50, 0.03),
    "minimax/minimax-m3": (0.30, 1.20, 0.06),
    "google/gemini-3.8-flash": (0.75, 3.75, 0.075),
    "deepseek/deepseek-v4-pro": (0.955, 1.911, 0.08),
    "anthropic/claude-sonnet-5": (2.0, 10.0, 0.2),
}

WRITERS = int(os.getenv("TG_WRITERS") or 4)
ROUNDS_MAX = int(os.getenv("TG_ROUNDS") or 6)
ROUNDS_UNTIL = 0.85
SECOND_KINDS_FRAC = 0.7
MUTANTS_PER_WRITER = int(os.getenv("TG_MUTANTS_PER_WRITER") or 40)
SWEEP_SECONDS = 60.0
FAST_VERIFY = os.getenv("TG_FAST_VERIFY") == "1"
APPROX_CONFIRM = os.getenv("TG_APPROX_CONFIRM") == "1"
CASE_TIMEOUT = 5.0
MAX_RESULT_CHARS = 8000
MAX_CASE_FILES = 400
CHECK_CASES_CAP = 30
CHECK_CASES_CAP_FAST = 400
CLOCK_SHIFT = 1e9
FAIL_STREAK_SECONDS = 300
DRAFT_WAIT_FRAC = 0.33
ROUND_WAIT_FRAC = 0.12
STRAGGLER_GRACE = 90.0
CPU_WORKERS_CAP = 4

SAME_SRC = '''
def _same_value(actual, expected):
    """True when `actual` equals the recorded value `expected` (floats compared closely)."""
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, int):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    if isinstance(expected, float):
        if not isinstance(actual, float):
            return False
        if math.isnan(expected):
            return math.isnan(actual)
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(expected, complex):
        return (isinstance(actual, complex) and _same_value(actual.real, expected.real)
                and _same_value(actual.imag, expected.imag))
    if isinstance(expected, str):
        return isinstance(actual, str) and actual == expected
    if isinstance(expected, bytes):
        return isinstance(actual, (bytes, bytearray)) and bytes(actual) == expected
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_same_value(a, e) for a, e in zip(actual, expected)))
    if isinstance(expected, tuple):
        return (isinstance(actual, tuple) and len(actual) == len(expected)
                and all(_same_value(a, e) for a, e in zip(actual, expected)))
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and len(actual) == len(expected)
                and all(k in actual and _same_value(actual[k], v) for k, v in expected.items()))
    if isinstance(expected, (set, frozenset)):
        return isinstance(actual, (set, frozenset)) and actual == expected
    return actual == expected
'''

CONFTEST_SRC = '''"""Suite-wide settings: a test that runs far longer than expected fails instead of hanging."""
import signal

import pytest

TIME_LIMIT_SECONDS = 10


class TimeLimitExceeded(BaseException):
    """Raised inside a test that ran longer than TIME_LIMIT_SECONDS."""


_state = {"expired": False}


def _expire(signum, frame):
    _state["expired"] = True
    raise TimeLimitExceeded("test ran longer than %s seconds" % TIME_LIMIT_SECONDS)


@pytest.fixture(autouse=True)
def _time_limit():
    if _state["expired"]:
        pytest.fail("not run: an earlier test exceeded the time limit")
    if not hasattr(signal, "setitimer"):
        yield
        return
    previous = signal.signal(signal.SIGALRM, _expire)
    signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
'''

_EVAL_NS = {"__builtins__": {}, "float": float, "set": set, "frozenset": frozenset, "complex": complex}

class _Unrecordable(Exception):
    pass

class _CaseTimeout(BaseException):
    pass

def _to_src(v, depth=0, limit=None):
    """Source text that evaluates to the plain value `v`.

    With `limit`, gives up as soon as the text is known to be longer than `limit` characters, so that a
    huge result costs little time and memory before it is rejected.
    """
    left = [math.inf if limit is None else limit]

    def spend(n):
        left[0] -= n
        if left[0] < 0:
            raise _Unrecordable("the result is too large (more than %d characters); return a smaller summary of "
                                "it" % limit)

    def leaf(text):
        spend(len(text))
        return text

    def items_of(values, depth):
        out = []
        for x in values:
            spend(2)
            out.append(conv(x, depth + 1))
        return out

    def conv(v, depth):
        if depth > 40:
            raise _Unrecordable("the result is nested too deeply")
        if v is None:
            return leaf("None")
        if isinstance(v, bool):
            return leaf("True" if v else "False")
        if isinstance(v, int):
            return leaf(repr(int(v)))
        if isinstance(v, float):
            f = float(v)
            if math.isnan(f):
                return leaf("float('nan')")
            if math.isinf(f):
                return leaf("float('inf')" if f > 0 else "-float('inf')")
            return leaf(repr(f))
        if isinstance(v, complex):
            return "complex(%s, %s)" % (conv(float(v.real), 0), conv(float(v.imag), 0))
        if isinstance(v, (str, bytes, bytearray)):
            if len(v) > left[0]:
                spend(len(v))
            return leaf(repr(v[:]) if isinstance(v, str) else repr(bytes(v)))
        if isinstance(v, tuple):
            items = items_of(v, depth)
            return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"
        if isinstance(v, list):
            return "[" + ", ".join(items_of(v, depth)) + "]"
        if isinstance(v, dict):
            pairs = []
            for k, x in v.items():
                spend(4)
                pairs.append("%s: %s" % (conv(k, depth + 1), conv(x, depth + 1)))
            return "{" + ", ".join(pairs) + "}"
        if isinstance(v, (set, frozenset)):
            items = sorted(items_of(v, depth))
            if isinstance(v, frozenset):
                return "frozenset({%s})" % ", ".join(items) if items else "frozenset()"
            return "{%s}" % ", ".join(items) if items else "set()"
        if hasattr(v, "__next__"):
            raise _Unrecordable("returned an iterator; wrap it in list()")
        raise _Unrecordable("returned a %s.%s, which is not plain data; return plain data derived from it "
                            "through the public API" % (type(v).__module__, type(v).__qualname__))

    return conv(v, depth)

class _LineTracer:

    def __init__(self, root):
        self.root = root
        self.lines = set()

    def _global(self, frame, event, arg):
        if frame.f_code.co_filename.startswith(self.root):
            self.lines.add((frame.f_code.co_filename, frame.f_lineno))
            return self._local
        return None

    def _local(self, frame, event, arg):
        if event == "line":
            self.lines.add((frame.f_code.co_filename, frame.f_lineno))
        return self._local

    def start(self):
        self.lines = set()
        threading.settrace(self._global)
        sys.settrace(self._global)

    def stop(self):
        sys.settrace(None)
        threading.settrace(None)
        return sorted([os.path.relpath(f, self.root), n] for f, n in self.lines)

def _safe_str(e) -> str:
    """str(e), even for exceptions whose __str__ itself fails."""
    try:
        return str(e)
    except Exception:
        return "<str() of the %s failed>" % type(e).__name__

def _fmt_exc(e):
    text = "%s: %s" % (type(e).__name__, _safe_str(e))
    return text if len(text) < 300 else text[:300] + "..."

def _describe_api(mod, limit=16000):
    import inspect
    out = []
    declared = getattr(mod, "__all__", None)
    names = list(declared) if isinstance(declared, (list, tuple)) else [n for n in dir(mod) if not n.startswith("_")]
    pkg_root = mod.__name__.split(".")[0]
    for name in names:
        try:
            obj = getattr(mod, name)
        except Exception:
            continue
        omod = getattr(obj, "__module__", "") or ""
        if inspect.ismodule(obj):
            if obj.__name__.split(".")[0] == pkg_root:
                out.append("module %s" % obj.__name__)
            continue
        if declared is None and omod and omod.split(".")[0] != pkg_root:
            continue
        doc = (inspect.getdoc(obj) or "").strip().split("\n")[0][:160]
        if inspect.isclass(obj):
            try:
                sig = str(inspect.signature(obj))
            except Exception:
                sig = "(...)"
            out.append("class %s%s  # %s" % (name, sig, doc))
            for mname, member in sorted(vars(obj).items()):
                if mname.startswith("_") and mname not in ("__call__", "__iter__", "__len__", "__getitem__",
                                                           "__contains__", "__eq__", "__enter__", "__exit__"):
                    continue
                fn = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
                if isinstance(member, property):
                    out.append("    .%s (property)" % mname)
                elif callable(fn):
                    try:
                        msig = str(inspect.signature(fn))
                    except Exception:
                        msig = "(...)"
                    mdoc = (inspect.getdoc(fn) or "").strip().split("\n")[0][:120]
                    out.append("    .%s%s  # %s" % (mname, msig, mdoc))
        elif callable(obj):
            try:
                sig = str(inspect.signature(obj))
            except Exception:
                sig = "(...)"
            out.append("%s%s  # %s" % (name, sig, doc))
        else:
            try:
                r = repr(obj)
            except Exception:
                r = "<%s>" % type(obj).__name__
            out.append("%s = %s" % (name, r if len(r) < 80 else r[:80] + "..."))
        if sum(len(x) for x in out) > limit:
            out.append("... (truncated)")
            break
    return "\n".join(out)

def _public_class_path(cls, roots):
    for klass in cls.__mro__:
        if klass in (BaseException, Exception, object):
            break
        if klass.__module__ == "builtins":
            return klass.__name__
        if klass.__name__.startswith("_"):
            continue
        mods = sorted((name for name in list(sys.modules) if name.split(".")[0] in roots
                       and not any(part.startswith("_") for part in name.split("."))),
                      key=lambda name: (name.count("."), name))
        for name in mods:
            if getattr(sys.modules.get(name), klass.__name__, None) is klass:
                return name + "." + klass.__name__
    return "Exception"

def _resolve_class(path):
    import importlib
    if "." not in path:
        return getattr(__import__("builtins"), path, Exception)
    mod, _, attr = path.rpartition(".")
    return getattr(importlib.import_module(mod), attr)

def _runner_main(cfg_path):
    import importlib

    with open(cfg_path) as fh:
        cfg = json.load(fh)
    for p in reversed(cfg.get("sys_path", [])):
        sys.path.insert(0, p)
    out = open(cfg["out"], "a", buffering=1)
    out_lock = threading.Lock()

    def emit(obj):
        line = json.dumps(obj) + "\n"
        with out_lock:
            out.write(line)

    mode = cfg["mode"]
    if mode == "deps":
        emit({"kind": "deps", "optional": _optional_modules(cfg.get("declared") or [], cfg.get("packages") or [])})
        emit({"kind": "end"})
        return
    if mode == "api":
        for name in cfg["candidates"]:
            info = {"kind": "api", "name": name}
            try:
                m = importlib.import_module(name)
                info["file"] = getattr(m, "__file__", None)
                info["path"] = list(getattr(m, "__path__", []) or [])
                try:
                    ver = getattr(m, "__version__", None)
                except Exception:
                    ver = None
                info["version"] = ver if isinstance(ver, str) else None
                try:
                    info["api"] = _describe_api(m)
                except Exception as e:
                    info["api"] = "(could not list the public names: %s)" % _fmt_exc(e)
            except BaseException as e:
                info["error"] = _fmt_exc(e)
            emit(info)
        emit({"kind": "end"})
        return

    ns = {"math": math}
    exec(SAME_SRC, ns)
    same = ns["_same_value"]
    if cfg.get("clock_shift"):
        _shift_clocks(float(cfg["clock_shift"]))
    if mode == "check":
        for pkg in cfg.get("packages") or ():
            try:
                importlib.import_module(pkg)
            except BaseException as e:
                emit({"kind": "library_error", "error": _fmt_exc(e)})
                emit({"kind": "end"})
                return
    modules = {}
    for name in cfg["modules"]:
        try:
            modules[name] = importlib.import_module(name)
        except BaseException as e:
            emit({"kind": "module_error", "module": name, "error": _fmt_exc(e),
                  "trace": traceback.format_exc()[-1500:], "cause": _import_failure_cause(e, name)})
    tracer = _LineTracer(cfg["trace_root"]) if cfg.get("trace_root") else None
    _run_cases(cfg, mode, modules, emit, same, tracer)
    emit({"kind": "end"})

_ALWAYS_PRESENT = {"pip", "setuptools", "pkg_resources", "_distutils_hack", "distutils", "wheel", "sitecustomize",
                   "usercustomize", "pytest", "_pytest", "py"}

def _optional_modules(declared: list, packages: list) -> list:
    import importlib.metadata as md

    def norm(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    try:
        mod_dists = md.packages_distributions()
    except Exception:
        return []
    roots = list(declared) + ["pytest"]
    for pkg in packages:
        roots += mod_dists.get(pkg, [])
    # pytest plugins load whenever pytest starts; hiding one would break every suite run, not test absence
    try:
        dists = list(md.distributions())
    except Exception:
        dists = []
    for dist in dists:
        try:
            if any(ep.group == "pytest11" for ep in dist.entry_points) and dist.metadata["Name"]:
                roots.append(dist.metadata["Name"])
        except Exception:
            pass
    keep, todo = set(), [norm(r) for r in roots]
    while todo:
        name = todo.pop()
        if name in keep:
            continue
        keep.add(name)
        try:
            reqs = md.requires(name) or []
        except Exception:
            reqs = []
        for req in reqs:
            if re.search(r"\bextra\s*==", req):
                continue
            mm = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
            if mm:
                todo.append(norm(mm.group(1)))
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    out = []
    for mod, dists in mod_dists.items():
        if not mod.isidentifier() or mod in stdlib or mod in packages or mod in _ALWAYS_PRESENT:
            continue
        if any(norm(d) in keep or norm(d) in _ALWAYS_PRESENT for d in dists):
            continue
        out.append(mod)
    return sorted(out)

_ABSENT_SRC = '''"""Optional third-party modules are unavailable in this interpreter, as where they are not installed."""
import sys

_ABSENT = frozenset(%s)


class _Absent:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name.partition(".")[0] in _ABSENT:
            raise ModuleNotFoundError("No module named %%r" %% name, name=name)
        return None


sys.meta_path.insert(0, _Absent())


def _hide_from_find_spec():
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name, package=None):
        # a probe for an optional package answers None when it is not installed; it does not raise
        full = importlib.util.resolve_name(name, package) if name.startswith(".") else name
        if full in _ABSENT:
            return None
        return real(name, package)

    importlib.util.find_spec = find_spec


_hide_from_find_spec()
'''

def _declared_dependencies(repo: str) -> list:
    names = set()

    def add(req):
        mm = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req or "")
        if mm and mm.group(1).lower() != "python":
            names.add(mm.group(1))

    path = os.path.join(repo, "pyproject.toml")
    if os.path.isfile(path):
        try:
            import tomllib
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            for req in (data.get("project") or {}).get("dependencies") or []:
                add(req)
            for name in ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}:
                add(name)
        except Exception:
            try:
                with open(path, errors="replace") as fh:
                    text = fh.read()
                block = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)\]", text)
                for req in re.findall(r"[\"']([^\"']+)[\"']", block.group(1) if block else ""):
                    add(req)
            except Exception:
                pass
    path = os.path.join(repo, "setup.cfg")
    if os.path.isfile(path):
        try:
            import configparser
            parser = configparser.ConfigParser()
            parser.read(path)
            for line in parser.get("options", "install_requires", fallback="").splitlines():
                add(line)
        except Exception:
            pass
    path = os.path.join(repo, "setup.py")
    if os.path.isfile(path):
        try:
            with open(path, errors="replace") as fh:
                tree = ast.parse(fh.read())
            assigned = {n.targets[0].id: n.value for n in ast.walk(tree)
                        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "install_requires":
                    value = assigned.get(node.value.id, node.value) if isinstance(node.value, ast.Name) else node.value
                    for elt in getattr(value, "elts", []):
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            add(elt.value)
        except Exception:
            pass
    return sorted(names)

def _takes_tmp_path(fn) -> bool:
    code = getattr(fn, "__code__", None)
    if code is not None:
        return "tmp_path" in code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
    import inspect
    try:
        return "tmp_path" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False

def _run_cases(cfg, mode, modules, emit, same, tracer=None):
    import faulthandler

    timeout = float(cfg.get("timeout", CASE_TIMEOUT))
    tmp_base = cfg.get("tmp_base") or tempfile.gettempdir()
    expected = cfg.get("expected") or {}
    armed = [False]
    # In record mode a case that cannot be stopped (it swallows the timeout, or hangs inside C code)
    # ends the process; the caller then marks the case that started last as hanging and goes on.
    hard_limit = timeout * 1.5 + 2 if mode == "record" else None

    def on_alarm(signum, frame):
        if armed[0]:
            raise _CaseTimeout()

    for key in cfg["cases"]:
        mod_name, case_name = key.split("::", 1)
        m = modules.get(mod_name)
        fn = getattr(m, case_name, None) if m is not None else None
        if fn is None:
            if mode == "record":
                emit({"kind": "case", "key": key, "status": "error", "error": "case not found"})
            else:
                emit({"kind": "mismatch", "key": key, "status": "missing"})
                if cfg.get("stop_on_first"):
                    break
            continue
        if mode == "record":
            emit({"kind": "start", "key": key})
        kwargs = {}
        if _takes_tmp_path(fn):
            import pathlib
            kwargs["tmp_path"] = pathlib.Path(tempfile.mkdtemp(prefix="case-", dir=tmp_base))
        status, value, exc, lines = "value", None, None, None
        t0 = time.perf_counter()
        # installed again for every case: an earlier case may have replaced the handler
        signal.signal(signal.SIGALRM, on_alarm)
        armed[0] = True
        # fires again every half second, in case the first one is swallowed by a bare `except:`
        signal.setitimer(signal.ITIMER_REAL, timeout, 0.5)
        if hard_limit:
            try:
                faulthandler.dump_traceback_later(hard_limit, exit=True)
            except Exception:
                hard_limit = None
        try:
            if tracer:
                tracer.start()
            try:
                value = fn(**kwargs)
            finally:
                armed[0] = False
                if tracer:
                    lines = tracer.stop()
        except _CaseTimeout:
            status = "timeout"
        except Exception as e:
            status, exc = "raises", e
        except BaseException as e:
            status, exc = "fatal", e
        finally:
            armed[0] = False
            signal.setitimer(signal.ITIMER_REAL, 0)
            if hard_limit:
                faulthandler.cancel_dump_traceback_later()
        secs = time.perf_counter() - t0
        if mode == "record":
            rec = {"kind": "case", "key": key, "status": status, "secs": round(secs, 4)}
            if status == "value":
                try:
                    src = _to_src(value, limit=MAX_RESULT_CHARS)
                    if len(src) > MAX_RESULT_CHARS:
                        raise _Unrecordable("the result is too large (%d characters); return a smaller "
                                            "summary of it" % len(src))
                    rec["src"] = src
                except _Unrecordable as u:
                    rec["status"], rec["error"] = "error", str(u)
                except Exception as e:
                    rec["status"], rec["error"] = "error", "could not record the result: " + _fmt_exc(e)
            elif status == "raises":
                rec["exc"] = type(exc).__name__
                rec["msg"] = _safe_str(exc)[:160]
                try:
                    rec["cls"] = _public_class_path(type(exc), set(cfg.get("packages") or ()))
                except Exception:
                    rec["cls"] = "Exception"

            elif status == "timeout":
                rec["status"], rec["error"] = "error", "took longer than %.0f s" % timeout
            else:
                rec["status"], rec["error"] = "error", "raised %s, which a test cannot catch" % type(exc).__name__
            if lines is not None:
                rec["lines"] = lines
            emit(rec)
        else:
            exp = expected.get(key)
            if exp is None:
                continue
            if exp["status"] == "value":
                ok = status == "value"
                if ok:
                    try:
                        ok = bool(same(value, eval(exp["src"], dict(_EVAL_NS))))
                    except Exception:
                        ok = False
            else:
                ok = status == "raises"
                if ok and exp.get("cls"):
                    try:
                        ok = isinstance(exc, _resolve_class(exp["cls"]))
                    except Exception:
                        ok = False
            if not ok:
                emit({"kind": "mismatch", "key": key, "status": status})
                if cfg.get("stop_on_first"):
                    break

class _Unsupported(Exception):
    pass

def _code_children(code):
    # Python 3.14 compiles lazily evaluated annotations into extra `__annotate__` functions whose line
    # spans overlap the real definitions; they never hold a changed line.
    return [c for c in code.co_consts if isinstance(c, types.CodeType) and not c.co_name.startswith("__annotate")]

def _code_lines(code, cache):
    key = id(code)
    if key in cache:
        return cache[key][1]
    nums = [code.co_firstlineno]
    try:
        nums += [ln for _, _, ln in code.co_lines() if ln is not None]
    except AttributeError:
        import dis
        nums += [ln for _, ln in dis.findlinestarts(code)]
    for c in _code_children(code):
        a, b = _code_lines(c, cache)
        nums += [a, b]
    span = (min(nums), max(nums))
    cache[key] = (code, span)
    return span

def _code_chain(orig, mut, line, cache):
    oc, mc = _code_children(orig), _code_children(mut)
    if len(oc) != len(mc) or any(a.co_name != b.co_name for a, b in zip(oc, mc)):
        raise _Unsupported("the change alters which functions exist")
    for a, b in zip(oc, mc):
        lo, hi = _code_lines(a, cache)
        if lo <= line <= hi:
            return [(a, b)] + _code_chain(a, b, line, cache)
    return []

def _server_main(cfg_path):
    import gc
    import importlib
    import select

    with open(cfg_path) as fh:
        cfg = json.load(fh)
    for p in reversed(cfg.get("sys_path", [])):
        sys.path.insert(0, p)
    # the protocol gets its own descriptors; a case that reads stdin or prints sees /dev/null
    proto_in = os.fdopen(os.dup(0), "rb")
    proto_out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)

    def reply(obj):
        proto_out.write(json.dumps(obj) + "\n")
        proto_out.flush()

    ns = {"math": math}
    exec(SAME_SRC, ns)
    same = ns["_same_value"]
    errors = []
    for pkg in cfg.get("packages") or ():
        try:
            importlib.import_module(pkg)
        except BaseException as e:
            errors.append("library: " + _fmt_exc(e))
    modules = {}
    for name in cfg["modules"]:
        try:
            modules[name] = importlib.import_module(name)
        except BaseException as e:
            errors.append("%s: %s" % (name, _fmt_exc(e)))
    src_root = os.path.realpath(cfg["src_root"])
    real = {}

    def realname(f):
        if f not in real:
            real[f] = os.path.realpath(f) if not f.startswith("<") else f
        return real[f]

    index = {}
    for obj in gc.get_objects():
        if type(obj) is types.FunctionType:
            c = obj.__code__
            fname = realname(c.co_filename)
            if fname.startswith(src_root + os.sep):
                index.setdefault((fname, c.co_name, c.co_firstlineno), []).append(obj)
    originals = {}
    line_cache = {}
    redecorated = {}
    module_of = {}
    def_lines = {}
    reply({"kind": "ready", "errors": errors, "functions": len(index)})

    def original(rel):
        if rel not in originals:
            path = os.path.join(src_root, rel)
            with open(path, "rb") as fh:
                src = fh.read()
            starts = [0]
            for ln in src.split(b"\n"):
                starts.append(starts[-1] + len(ln) + 1)
            tree = ast.parse(src)
            originals[rel] = (src, compile(src, path, "exec", dont_inherit=True), tree, starts)
            _code_lines(originals[rel][1], line_cache)
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.decorator_list:
                    first = min([n.lineno] + [d.lineno for d in n.decorator_list])
                    def_lines[(path, n.name, first)] = n.lineno
        return originals[rel]

    def live_functions(path, a):
        found = index.get((path, a.co_name, a.co_firstlineno))
        if found:
            return found
        line = def_lines.get((path, a.co_name, a.co_firstlineno))
        return index.get((path, a.co_name, line), []) if line else []

    def in_fork(fn, timeout=30.0):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            try:
                out = str(fn())
            except BaseException:
                out = ""
            try:
                os.write(w, out.encode("utf-8", errors="replace"))
            finally:
                os._exit(0)
        os.close(w)
        chunks, end = [], time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([r], [], [], 1.0)
            if ready:
                data = os.read(r, 65536)
                if not data:
                    break
                chunks.append(data)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        os.close(r)
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        return b"".join(chunks).decode("utf-8", errors="replace") or None

    def redecoration(rel, a, f):
        path = os.path.join(src_root, rel)
        key = (path, a.co_name, a.co_firstlineno)
        if key in redecorated:
            return redecorated[key]
        tree = original(rel)[2]
        node = None
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == a.co_name and n.decorator_list \
                    and min([n.lineno] + [d.lineno for d in n.decorator_list]) == a.co_firstlineno:
                node = n
        if node is not None and getattr(f, "__wrapped__", None) is not None:
            node = None
        result = None
        if node is not None:
            decs = [ast.unparse(d) for d in reversed(node.decorator_list)]

            def probe():
                g = types.FunctionType(a, f.__globals__, f.__name__, f.__defaults__,
                                       f.__closure__ if len(a.co_freevars) == len(f.__closure__ or ()) else None)
                g.__kwdefaults__ = f.__kwdefaults__
                res = g
                for depth, d in enumerate(decs, 1):
                    res = eval(d, f.__globals__)(res)
                    if not isinstance(res, types.FunctionType):
                        return 0
                    if res.__code__ == f.__code__:
                        return depth
                return 0
            out = in_fork(probe)
            depth = int(out) if out and out.strip().isdigit() else 0
            if depth:
                result = (decs[:depth], depth)
        redecorated[key] = result
        return result

    def find_module(path):
        if path not in module_of:
            module_of[path] = None
            for m in list(sys.modules.values()):
                fname = getattr(m, "__file__", None)
                if fname and realname(fname) == path:
                    module_of[path] = m
                    break
        return module_of[path]

    def literal(expr):
        for n in ast.walk(expr):
            if isinstance(n, ast.Call):
                if _dotted(n.func) not in ("re.compile", "frozenset", "set", "tuple", "list", "dict", "re.escape"):
                    return False
            elif not isinstance(n, (ast.Constant, ast.Dict, ast.List, ast.Tuple, ast.Set, ast.UnaryOp, ast.BinOp,
                                    ast.Name, ast.Attribute, ast.keyword, ast.expr_context, ast.operator,
                                    ast.unaryop)):
                return False
        return True

    def plan(mut):
        rel = mut["file"]
        path = os.path.join(src_root, rel)
        src, orig_code, tree, starts = original(rel)
        if mut.get("default"):
            d = mut["default"]
            target = None
            for a, _ in _code_chain(orig_code, orig_code, d["line"], line_cache):
                if a.co_name == d["name"]:
                    target = a
            live = live_functions(path, target) if target else []
            if not live:
                raise _Unsupported("no live function for the changed default")
            return {"kind": "default", "live": live, "d": d}
        new_src = src[:mut["start"]] + mut["repl"].encode("utf-8") + src[mut["end"]:]
        try:
            new_code = compile(new_src, path, "exec", dont_inherit=True)
        except SyntaxError as e:
            raise _Unsupported("does not compile: %s" % _fmt_exc(e))
        chain = _code_chain(orig_code, new_code, mut["line"], line_cache)
        swaps = []
        for a, b in chain:
            for f in live_functions(path, a):
                if f.__code__ == a:
                    swaps.append((f, b, None))
                    continue
                redo = redecoration(rel, a, f)
                if redo is None:
                    raise _Unsupported("the live function differs from its source")
                swaps.append((f, b, redo[0]))
        if swaps:
            return {"kind": "swap", "swaps": swaps, "path": path, "new_src": new_src}
        if any(a.co_flags & 0x2 for a, _ in chain):
            raise _Unsupported("no live function contains the change")
        stmt, owner = None, None
        for st in tree.body:
            if st.lineno <= mut["line"] <= (st.end_lineno or st.lineno):
                if isinstance(st, ast.ClassDef):
                    owner = st.name
                    st = next((x for x in st.body if x.lineno <= mut["line"] <= (x.end_lineno or x.lineno)), None)
                stmt = st
                break
        if stmt is None or not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
            raise _Unsupported("not an assignment of data")
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if not all(isinstance(t, ast.Name) for t in targets) or not literal(stmt.value):
            raise _Unsupported("not an assignment of literal data")
        a0 = starts[stmt.lineno - 1] + stmt.col_offset
        b0 = starts[stmt.end_lineno - 1] + stmt.end_col_offset
        if not (a0 <= mut["start"] <= mut["end"] <= b0):
            raise _Unsupported("change outside the statement")
        text = b"\n" * (stmt.lineno - 1) + src[a0:mut["start"]] + mut["repl"].encode("utf-8") + src[mut["end"]:b0]
        try:
            code = compile(text, path, "exec", dont_inherit=True)
        except SyntaxError as e:
            raise _Unsupported("statement does not compile: %s" % _fmt_exc(e))
        module = find_module(path)
        holder = module if owner is None else getattr(module, owner, None)
        if module is None or holder is None:
            raise _Unsupported("module or class not loaded")
        return {"kind": "rebind", "code": code, "module": module, "holder": holder, "class": owner is not None,
                "names": [t.id for t in targets]}

    def apply(pl):
        if pl["kind"] == "default":
            d = pl["d"]
            for f in pl["live"]:
                try:
                    value = eval(d["expr"], f.__globals__)
                except Exception as e:
                    raise _Unsupported("default not evaluable: %s" % _fmt_exc(e))
                if d["positional"]:
                    defaults = list(f.__defaults__ or ())
                    if not (0 <= d["index"] < len(defaults)):
                        raise _Unsupported("default position")
                    defaults[d["index"]] = value
                    f.__defaults__ = tuple(defaults)
                else:
                    kw = dict(f.__kwdefaults__ or {})
                    if d["param"] not in kw:
                        raise _Unsupported("keyword default")
                    kw[d["param"]] = value
                    f.__kwdefaults__ = kw
            return False
        if pl["kind"] == "swap":
            if any(decs for _, _, decs in pl["swaps"]):
                import linecache
                text = pl["new_src"].decode("utf-8", errors="replace")
                linecache.cache[pl["path"]] = (len(text), None, text.splitlines(True), pl["path"])
            for f, b, decs in pl["swaps"]:
                code = b
                if decs:
                    g = types.FunctionType(b, f.__globals__, f.__name__, f.__defaults__,
                                           f.__closure__ if len(b.co_freevars) == len(f.__closure__ or ()) else None)
                    g.__kwdefaults__ = f.__kwdefaults__
                    res = g
                    for dsrc in decs:
                        res = eval(dsrc, f.__globals__)(res)
                    if not isinstance(res, types.FunctionType):
                        raise _Unsupported("decorator result")
                    code = res.__code__
                if len(code.co_freevars) != len(f.__closure__ or ()):
                    raise _Unsupported("the change alters a closure")
                f.__code__ = code
            return False
        holder, names = pl["holder"], pl["names"]
        old = {n: getattr(holder, n, None) for n in names}
        ns = {}
        if pl["class"]:
            exec(pl["code"], pl["module"].__dict__, ns)
            for n in names:
                setattr(holder, n, ns[n])
        else:
            exec(pl["code"], pl["module"].__dict__)
        for n in names:
            new = getattr(holder, n, None)
            if isinstance(old[n], (str, bytes, int, float, bool, type(None))):
                continue
            for m in list(sys.modules.values()):
                fname = getattr(m, "__file__", None)
                if not fname or not realname(fname).startswith(src_root + os.sep) or m is pl["module"]:
                    continue
                for attr, val in list(vars(m).items()):
                    if val is old[n]:
                        setattr(m, attr, new)
        return True

    while True:
        line = proto_in.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("op") == "quit":
            break
        pl = None
        if req.get("mut"):
            try:
                pl = plan(req["mut"])
            except _Unsupported as u:
                reply({"recs": [{"kind": "unsupported", "why": str(u)}]})
                continue
            except Exception as e:
                reply({"recs": [{"kind": "unsupported", "why": "plan: " + _fmt_exc(e)}]})
                continue
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(r)
                out = os.fdopen(w, "w", buffering=1)

                def emit(obj):
                    out.write(json.dumps(obj) + "\n")

                try:
                    if pl is not None and apply(pl):
                        emit({"kind": "approx"})
                except _Unsupported as u:
                    emit({"kind": "unsupported", "why": str(u)})
                    emit({"kind": "end"})
                    out.flush()
                    os._exit(0)
                ccfg = {"cases": req["keys"], "expected": req.get("expected") or {}, "stop_on_first":
                        req.get("stop_on_first", True), "timeout": req.get("timeout", CASE_TIMEOUT),
                        "tmp_base": cfg.get("tmp_base"), "packages": cfg.get("packages")}
                _run_cases(ccfg, "check", modules, emit, same)
                emit({"kind": "end"})
                out.flush()
            except BaseException as e:
                try:
                    out.write(json.dumps({"kind": "runner_crash", "output": _fmt_exc(e)}) + "\n")
                    out.flush()
                except BaseException:
                    pass
            os._exit(0)
        os.close(w)
        chunks, deadline, timed_out = [], time.time() + float(req.get("deadline", 60)), False
        while True:
            left = deadline - time.time()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([r], [], [], min(left, 1.0))
            if ready:
                data = os.read(r, 65536)
                if not data:
                    break
                chunks.append(data)
        if timed_out:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        os.close(r)
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        recs = []
        for ln in b"".join(chunks).decode("utf-8", errors="replace").split("\n"):
            if ln.strip():
                try:
                    recs.append(json.loads(ln))
                except ValueError:
                    pass
        if timed_out:
            recs.append({"kind": "runner_timeout"})
        elif not recs or recs[-1].get("kind") != "end":
            recs.append({"kind": "runner_crash", "output": "the checking process ended early"})
        reply({"recs": recs})

class _Shifted:
    __slots__ = ("fn", "d")

    def __init__(self, fn, d):
        self.fn, self.d = fn, d

    def __call__(self):
        return self.fn() + self.d

class _AtNow:
    __slots__ = ("fn", "struct")

    def __init__(self, fn, struct=False):
        self.fn, self.struct = fn, struct

    def __call__(self, t=None):
        if t is None:
            t = time.localtime() if self.struct else time.time()
        return self.fn(t)

class _StrftimeAtNow:
    __slots__ = ("fn",)

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, fmt, t=None):
        return self.fn(fmt, time.localtime() if t is None else t)

def _shift_clocks(shift: float) -> None:
    for name, amount in (("monotonic", shift), ("perf_counter", shift), ("time", shift / 10)):
        f = getattr(time, name, None)
        if f is not None:
            setattr(time, name, _Shifted(f, amount))
        f_ns = getattr(time, name + "_ns", None)
        if f_ns is not None:
            setattr(time, name + "_ns", _Shifted(f_ns, int(amount * 1e9)))
    for name in ("localtime", "gmtime", "ctime"):
        f = getattr(time, name, None)
        if f is not None:
            setattr(time, name, _AtNow(f))
    if hasattr(time, "asctime"):
        time.asctime = _AtNow(time.asctime, struct=True)
    if hasattr(time, "strftime"):
        time.strftime = _StrftimeAtNow(time.strftime)

def _import_failure_cause(exc, module_name):
    if isinstance(exc, SyntaxError) and exc.filename and \
            os.path.splitext(os.path.basename(exc.filename))[0] != module_name:
        return "library"
    tb = exc.__traceback__
    own = own_line = None
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_name == "<module>" and not code.co_filename.startswith("<"):
            if own is None and os.path.splitext(os.path.basename(code.co_filename))[0] == module_name:
                own, own_line = code.co_filename, tb.tb_lineno
            elif code.co_filename != own:
                return "library"
        tb = tb.tb_next
    if own is None:
        return "library"
    try:
        with open(own, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and \
                    node.lineno <= own_line <= (node.end_lineno or node.lineno):
                return "library"
    except Exception:
        pass
    return "file"

def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return text[:head] + "\n... [%d characters omitted] ...\n" % (len(text) - limit) + text[-(limit - head):]

def _available_cpus() -> int:
    n = os.cpu_count() or 2
    try:
        n = min(n, len(os.sched_getaffinity(0)))
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max":
            n = min(n, max(1, int(math.ceil(int(quota) / int(period)))))
    except Exception:
        pass
    return max(1, n)

def _stdlib_names() -> set:
    names = set(getattr(sys, "stdlib_module_names", ()))
    if not names:
        names = {"abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect", "builtins",
                 "calendar", "cmath", "codecs", "collections", "contextlib", "copy", "csv", "dataclasses",
                 "datetime", "decimal", "difflib", "enum", "errno", "fnmatch", "fractions", "functools",
                 "gc", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http", "inspect", "io",
                 "ipaddress", "itertools", "json", "keyword", "locale", "logging", "math", "numbers",
                 "operator", "os", "pathlib", "pickle", "pprint", "queue", "random", "re", "shutil",
                 "signal", "statistics", "string", "struct", "sys", "tempfile", "textwrap", "threading",
                 "time", "timeit", "traceback", "types", "typing", "unicodedata", "unittest", "urllib",
                 "uuid", "warnings", "weakref", "xml", "zipfile", "zlib"}
    return names

def _nobody_ids():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    try:
        import pwd
        pw = pwd.getpwnam("nobody")
        return pw.pw_uid, pw.pw_gid
    except Exception:
        return 65534, 65534

def _run_proc(cmd, *, cwd, env, timeout, as_nobody=False):
    kw = {}
    ids = _nobody_ids() if as_nobody else None
    if ids:
        if sys.version_info >= (3, 9):
            kw.update(user=ids[0], group=ids[1], extra_groups=[])
        else:
            def _drop(uid=ids[0], gid=ids[1]):
                os.setgroups([])
                os.setgid(gid)
                os.setuid(uid)
            kw["preexec_fn"] = _drop
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, **kw)
    except Exception as e:
        return -1, "could not start %s: %s" % (cmd[0], e)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode(errors="replace")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:
            out = b""
        return None, (out or b"").decode(errors="replace")

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL_REFUSED = re.compile(r"not a valid model|invalid model|unknown model|no endpoints found|does not exist|"
                            r"not (?:in the )?allow|allowed (?:model )?list|allowlist|not permitted|"
                            r"not available|unsupported model|not supported|not found", re.I)

def _inference_urls() -> list:
    urls = []
    proxy = (os.getenv("SANDBOX_PROXY_URL") or "").strip().rstrip("/")
    if proxy:
        urls.append(proxy + "/api/v1/chat/completions")
    base = (os.getenv("OPENROUTER_BASE_URL") or "").strip().rstrip("/")
    if base:
        urls.append(base + "/chat/completions")
    urls.append(OPENROUTER_CHAT_URL)
    return list(dict.fromkeys(urls))

def _api_key() -> str:
    return (os.getenv("OPENROUTER_API_KEY") or "").strip()

class _HardTimeout(BaseException):
    pass

class _hard_deadline:

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.active = False
        self.previous = None

    def __enter__(self):
        if threading.current_thread() is threading.main_thread() and hasattr(signal, "setitimer"):
            def _fire(signum, frame):
                raise _HardTimeout()
            self.previous = signal.signal(signal.SIGALRM, _fire)
            signal.setitimer(signal.ITIMER_REAL, max(1.0, self.seconds))
            self.active = True
        return self

    def __exit__(self, *exc):
        if self.active:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous or signal.SIG_DFL)
        return False

class LLM:

    def __init__(self, model: str, budget: float, deadline: float, summarizer=None):
        self.summarizer = summarizer
        self.models = [model] + [m for m in FALLBACK_MODELS if m != model]
        self.model_i = 0
        self.urls = _inference_urls()
        self.url_i = 0
        self.bad_urls: set = set()
        self.budget = budget
        self.deadline = deadline
        self.spent = 0.0
        self.calls = 0
        self.exhausted = False
        self._call_state = threading.local()
        self.refused = False
        self.fail_since = None
        self.messages: list = []
        self.last_call_cost = 0.0
        self.lock = threading.Lock()
        self.route_lock = threading.Lock()

    @property
    def model(self) -> str:
        return self.models[self.model_i]

    @property
    def refused(self) -> bool:
        """Whether the current thread's call was refused by every endpoint and model (writers call in parallel)."""
        return getattr(self._call_state, "refused", False)

    @refused.setter
    def refused(self, value: bool) -> None:
        self._call_state.refused = value

    def _next_url(self, why: str, retire: bool = False, sent: int | None = None) -> bool:
        """Move on from the endpoint `sent` (the one a failed request used); several writers may report the
        same failure, and only the first of them switches."""
        with self.route_lock:
            sent = self.url_i if sent is None else sent
            if retire:
                self.bad_urls.add(self.urls[sent])
            if self.url_i != sent and self.urls[self.url_i] not in self.bad_urls:
                return True
            n = len(self.urls)
            for step in range(1, n + 1):
                i = (sent + step) % n
                if i != sent and self.urls[i] not in self.bad_urls:
                    self.url_i = i
                    log("[LLM] %s; switching to %s" % (why, self.urls[i]))
                    return True
            return False

    def _next_model(self, why: str, sent: int | None = None) -> bool:
        """Move on from the model `sent`; a refusal another writer already acted on needs no second switch."""
        with self.route_lock:
            sent = self.model_i if sent is None else sent
            if self.model_i != sent:
                return True
            if self.model_i + 1 >= len(self.models):
                return False
            self.model_i += 1
            log("[LLM] %s; switching to model %s" % (why, self.model))
            return True

    def _out_of_budget(self, detail: str) -> bool:
        low = detail.lower()
        if any(mark in low for mark in ("insufficient credit", "credits exhausted", "payment required",
                                       "budget exhausted", "budget exceeded", "budget limit exceeded")):
            return True
        if any(mark in low for mark in ("rate limit", "rate_limit", "rate-limit", "too many requests",
                                       "capacity", "overloaded")):
            return False
        return "budget" in low or "cost" in low or "credit" in low

    def affordable(self) -> bool:
        reserve = max(self.last_call_cost * 1.5, 0.01)
        return (not self.exhausted and self.spent + reserve < self.budget * 0.96
                and time.time() < self.deadline - 30)

    def ask(self, text: str, max_tokens: int = MAX_OUTPUT_TOKENS, conv: list | None = None, effort: str = ""):
        if not self.affordable():
            return None, None
        messages = self.messages if conv is None else conv
        messages.append({"role": "user", "content": text})
        self._trim(messages)
        reply, finish = self._call(max_tokens, messages, effort or REASONING_EFFORT)
        if reply is None:
            messages.pop()
            return None, None
        messages.append({"role": "assistant", "content": reply})
        return reply, finish

    def _trim(self, messages: list, limit_chars: int = 600000) -> None:
        total = sum(len(m["content"]) for m in messages)
        if total <= limit_chars or len(messages) < 9:
            return
        head = messages[:2]
        tail = messages[-5:]
        while tail and tail[0]["role"] != "user":
            tail = tail[1:]
        summary = "(Earlier work in this conversation is summarised here.)"
        if self.summarizer:
            try:
                summary = self.summarizer()
            except Exception:
                pass
        messages[:] = head + [{"role": "assistant", "content": summary}] + tail
        log("[LLM] conversation compacted from %d to %d characters" % (
            total, sum(len(m["content"]) for m in messages)))

    def _payload_messages(self, messages: list):
        out = []
        n = len(messages)
        for i, m in enumerate(messages):
            if m["role"] in ("system", "user") and (i <= 1 or i == n - 1):
                out.append({"role": m["role"], "content": [
                    {"type": "text", "text": m["content"], "cache_control": {"type": "ephemeral"}}]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def _call(self, max_tokens: int, messages: list, effort: str = ""):
        self.refused = False
        reply, finish = self._attempts(max_tokens, messages, effort)
        if reply is not None:
            self.fail_since = None
        elif not self.exhausted:
            now = time.time()
            if self.refused:
                log("[LLM] every endpoint and model refused the request; no more model calls")
                self.exhausted = True
            elif self.fail_since is None:
                self.fail_since = now
            elif now - self.fail_since > FAIL_STREAK_SECONDS:
                log("[LLM] calls have failed for %.0f s; no more model calls" % (now - self.fail_since))
                self.exhausted = True
        return reply, finish

    def _retry_pause(self, failures: int) -> bool:
        wait = min(20, 2 * failures ** 2)
        if failures >= 4 or self.deadline - time.time() < wait + 30:
            return False
        time.sleep(wait)
        return True


    @staticmethod
    def _read_body(resp, seconds: float) -> bytes:
        """The whole response body, read within `seconds` in total (the socket timeout alone only bounds each
        read, so a server that trickles bytes could otherwise hold a writer thread indefinitely)."""
        read1 = getattr(resp, "read1", None)
        if read1 is None:
            return resp.read()
        end = time.time() + seconds
        chunks = []
        while True:
            chunk = read1(65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            if time.time() > end:
                raise TimeoutError("the response took longer than %.0f s to arrive" % seconds)

    def _attempts(self, max_tokens: int, messages: list, effort: str = ""):
        import urllib.error
        import urllib.request

        key = _api_key()
        if not key:
            log("[LLM] no API key")
            self.exhausted = True
            return None, None
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        failures = empty = hops = 0
        refused = set()
        while failures < 4:
            remaining = self.deadline - time.time()
            if remaining < 30:
                return None, None
            model_i, url_i = self.model_i, self.url_i
            model, sent_url = self.models[model_i], self.urls[url_i]
            payload = {"model": model, "messages": self._payload_messages(messages), "max_tokens": max_tokens,
                       "temperature": TEMPERATURE, "seed": SEED, "usage": {"include": True}}
            if effort:
                payload["reasoning"] = {"effort": effort, "exclude": True}
            body = json.dumps(payload).encode()
            timeout = max(20.0, min(300.0, remaining - 15))
            t0 = time.time()
            data = None
            status = None
            detail = ""
            try:
                req = urllib.request.Request(sent_url, data=body, headers=headers, method="POST")
                with _hard_deadline(timeout + 5):
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        raw = self._read_body(resp, timeout)
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    detail = e.read().decode(errors="replace")[:400]
                except Exception:
                    pass
            except _HardTimeout:
                log("[LLM] call exceeded %.0f s" % timeout)
                failures += 1
                continue
            except Exception as e:
                log("[LLM] request to %s failed: %s" % (sent_url, _fmt_exc(e)))
                failures += 1
                slow = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
                if not slow and isinstance(e, OSError) and self._next_url("endpoint unreachable", sent=url_i):
                    continue
                if not self._retry_pause(failures):
                    return None, None
                continue
            choice = {}
            text = ""
            if data is not None:
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = msg.get("content") or ""
                if isinstance(text, list):
                    text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
                usage = data.get("usage") or {}
                cost = usage.get("cost")
                if not isinstance(cost, (int, float)):
                    resolved = data.get("model")
                    rate_model = (resolved if isinstance(resolved, str) and resolved in PRICES
                                  else model if resolved in (None, "") else None)
                    pin, pout, pcache = PRICES.get(rate_model, (5.0, 25.0, 0.5))
                    prompt = usage.get("prompt_tokens") or 0
                    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
                    comp = usage.get("completion_tokens") or 0
                    cost = ((prompt - cached) * pin + cached * pcache + comp * pout) / 1e6
                with self.lock:
                    self.spent += float(cost)
                    self.last_call_cost = float(cost)
                    self.calls += 1
                    log("[LLM] call %d: %.1fs, in=%s out=%s cost=$%.4f total=$%.4f finish=%s provider=%s" % (
                        self.calls, time.time() - t0, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                        cost, self.spent, choice.get("finish_reason"), data.get("provider")))
                if text.strip():
                    return text, choice.get("finish_reason")
                error = data.get("error")
                if error:
                    detail = str(error.get("message", "")) if isinstance(error, dict) else ""
                    try:
                        code = int(error.get("code")) if isinstance(error, dict) else None
                    except (TypeError, ValueError, OverflowError):
                        code = None
                    inflight = any(m in detail.lower() for m in ("in_flight", "in-flight", "in flight"))
                    exhausted = any(mark in detail.lower() for mark in (
                        "insufficient credit", "not enough credit", "requires more credit", "credits exhausted",
                        "credit exhausted", "payment required", "budget exhausted", "budget exceeded",
                        "budget limit exceeded"))
                    if code in (402, 429) and exhausted and not inflight:
                        log("[LLM] budget exhausted (response %d): %s" % (code, detail[:200]))
                        self.exhausted = True
                        return None, None
            if status is not None:
                low = detail.lower()
                about_model = ("model" in low or model.lower() in low) and bool(_MODEL_REFUSED.search(low))
                inflight = status == 402 and any(m in low for m in ("in_flight", "in-flight", "in flight"))
                if (status == 402 and not inflight) or (status == 429 and self._out_of_budget(detail)):
                    log("[LLM] budget exhausted (HTTP %d): %s" % (status, detail[:200]))
                    self.exhausted = True
                    return None, None
                log("[LLM] HTTP %d from %s: %s" % (status, sent_url, detail[:200]))
                if data is None and status in (400, 403, 404) and about_model:
                    if self._next_model("model %s refused" % model, sent=model_i):
                        refused.clear()
                        continue
                    self.refused = True
                    return None, None
                if data is None and status in (404, 405):
                    if self._next_url("endpoint answered HTTP %d" % status, retire=True, sent=url_i):
                        continue
                    self.refused = True
                    return None, None
                if data is None and status in (401, 403):
                    refused.add(url_i)
                    hops += 1
                    usable = [i for i, u in enumerate(self.urls) if u not in self.bad_urls]
                    if hops <= 2 * len(self.urls) * len(self.models):
                        if any(i not in refused for i in usable) and \
                                self._next_url("endpoint answered HTTP %d" % status, sent=url_i):
                            continue
                        if self._next_model("request refused by every endpoint", sent=model_i):
                            refused.clear()
                            continue
                    self.refused = True
                    return None, None
                if inflight or status in (408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529):
                    failures += 1
                    if not self._retry_pause(failures):
                        return None, None
                    continue
                return None, None
            if not text.strip():
                empty += 1
                if empty < 3:
                    if choice.get("finish_reason") == "length" and effort == "xhigh" and not data.get("error"):
                        effort = "high"
                        log("[LLM] empty length-limited reply; retrying with high reasoning")
                    continue
                return None, None
            return text, choice.get("finish_reason")
        return None, None

SYSTEM_PROMPT = """\
You are an expert Python test engineer. You write regression test suites that pin down the observable
behaviour of an existing library precisely: any change to the covered behaviour, however small, must make a
test fail, while a refactoring that keeps the behaviour unchanged must keep every test passing.

You do not write pytest files yourself. You write *case functions*. The tooling runs every case against the
library as it is today, records its exact result, and turns each case into a pytest test that asserts that
result. You never type an expected value; you choose what to run and what to observe.

## Case files

Write case files as fenced blocks whose info string is `python` followed by the file name:

```python cases_parsing.py
from mylib import parse, Parser        # the package under test, pytest and the standard library only

WORDS = ["alpha", "beta"]              # module level: imports, literal constants, helpers only

def case_parse_plain_number():
    return parse("12")

def case_parser_keeps_state_between_calls():
    p = Parser(strict=True)
    first = p.feed("a=1")
    second = p.feed("b=2")
    return [first, second, p.count]

def case_parse_rejects_empty_input():
    return parse("")                   # when a case raises, its test asserts that the call raises
```

Rules:
- A function named `case_<name>` is one test. It takes no arguments (or only `tmp_path`, a fresh
  pathlib.Path directory) and returns what it observes with `return <expression>`, normally as its last
  statement; a return inside `try:` (with cleanup in `finally:`) or inside a `with` block is fine too.
- Return plain data only: None, bool, int, float, str, bytes, and lists, tuples, dicts and sets of those.
  Turn library objects into plain data through their public API (attributes, methods, list(), sorted()).
  Return several observations at once as a tuple, list or dict when they belong together.
- A case whose body raises is recorded as "raises an exception", whatever the class. Where the task says
  exception classes are part of the contract, put those cases in their own case file whose first code line
  is `EXCEPTION_CLASSES = True`; every raising case in such a file asserts the exact exception class it
  raises today:

  ```python cases_errors.py
  EXCEPTION_CLASSES = True
  from mylib import parse

  def case_parse_rejects_bytes():
      return parse(b"12")
  ```

  Keep raising cases whose class is not part of the contract in files without that line.
- Import only the package under test (public names only: nothing starting with an underscore, and only from
  the modules the task allows), pytest and the standard library. Never run library code when the file is
  imported: no module-level calls, and no module-level class that subclasses or is decorated with library
  objects. Define such classes inside a helper function that the cases call.
- Keep every case deterministic, independent and fast (well under 0.2 s): no real clock, sleeping,
  randomness, network, environment variables, file paths of the library, object ids or process state;
  restore any global state a case changes before it returns. Never compute a value from a clock reading
  the library took (such as `obj.started_at + 1.5`): float rounding then depends on the machine; set
  such attributes to fixed numbers first. Never return anything that contains the current date or time
  (a log line with a time field, say); pass explicit times where the API accepts them.
- Return observations within the requested scope. Where ordering is not part of the requested
  behaviour, return sorted data.
- Leave out features that need a third-party package the package under test does not depend on: it may not
  be installed where the suite runs.
- If a case uses randomness, seed it inside the case and restore its state before returning;
  return the observations required by the requested behaviour.

Group cases by topic, about 20-60 cases per file. To add cases later, write a NEW file (e.g.
`cases_parsing_2.py`). To fix dropped cases, write corrected versions of just those cases into a new file
too; the good cases of the old file stay. Only when a whole file is reported NOT USABLE, send the complete
corrected file again under the same name.

## How the work runs

Several writers work on the suite at the same time, each in its own conversation and each on its own
share of the scope. There is no shell: the task, the in-scope source and the documentation are given
above, and every expected value is recorded by running your cases, so nothing needs to be looked up or
tried out first. Every reply is one exchange, and exchanges are the scarce resource: write everything
your share needs in each reply rather than a little at a time. After a reply the tooling records your
cases and later reports what was dropped and which small changes of the library your cases do not notice
yet.
"""

AUTHOR_TASK = """\
Write the regression cases for your share of the scope. {writers} writers work on this suite at the same
time; every one sees the same task and code. In this first draft, focus on your share so the drafts do not
overlap; where a feature outside your share changes the result of one in your share, test that interaction
too. (Later messages may give you items from anywhere in the scope; those are yours as well.)

YOUR SHARE:
{share}

Write all of your case files now, in this one reply: about {target} cases over 2-5 files whose names say
what they cover (```python cases_<topic>.py). Write nothing else but a sentence per file at most.

Cover, for your share:
- every public function, class, method, parameter and option it names; each parameter's default and
  non-default values, and every accepted form of each argument;
- boundaries and edge cases (empty, single element, zero, negative, very large, unicode, None where
  accepted); values just below, at and just above every threshold, limit and length in the code; for an
  option that defaults to None, the other false values too (False, 0, '', empty containers) where accepted;
  where numbers are accepted, integers and floats (and booleans, which are integers in Python) alike;
- documented invalid inputs (cases that raise);
- combinations of options that interact; sequences of calls where state matters; whether inputs are
  left unmodified;
- the examples given in the documentation and the docstrings.
Choose inputs that make different code paths give different results: asymmetric values, distinct values
for different arguments, inputs that reach every branch and flag in the source. Prefer many small,
focused cases; return several related observations from one case when they belong together.
"""

ROUND_HEAD = """\
The shares of the first draft no longer apply: every item in this message is yours to cover, whichever
part of the scope it belongs to (the task and the code are above)."""

MUTATION_INTRO = """\
Each item below is a small deliberate change to the library source that none of the suite's cases
notices yet: every case still returns its recorded result. For each item, find inputs that reach the
changed line through the in-scope public API and whose result differs because of the change, and write
new cases for them in new case files. The best case makes the changed line decide the result: a value
exactly at a changed boundary, an input for which the removed statement matters, an option combination
that takes the changed branch, an argument left out so that a changed default applies, a false value
other than None (False, 0, '') where a removed `x = None` or a changed truth test decides the result.
If a change cannot alter in-scope behaviour that the contract covers (it only affects excluded details,
or it is equivalent; "another writer's share" is not a reason), put its id and the reason on a line of its own, `SKIP: m12 (equivalent for the requested behaviour)`,
and write nothing for it. Set a change aside only after working out which inputs reach the changed line:
most changes can be observed with the right inputs."""

SECOND_LOOK = """\
Second look. Each change below was shown to another writer before and is still undetected: it was set
aside as impossible to observe, or the cases written for it did not notice it. These are the hard ones, so
do not set them aside: for every item, read the code around the changed line (marked with >), work out
which public call reaches that line and in which situation the changed statement decides the result, and
write at least one case for exactly that situation. Think of the less common situations: an argument at a
boundary, an option combination that is rarely used, a callback or hook that returns a new or different
object, the same object passed or reached twice, an empty or a one-element input, a second call that sees
state left by the first. A case that turns out not to notice the change costs nothing; a missed one does."""

ROUND_TAIL = """\
Write all new case files for the items above in this one reply (new file names, ```python
cases_<topic>.py). Do not repeat cases that already exist."""

SHARE_LENSES = [
    "Edge and boundary behaviour across the whole scope: empty, single and very large inputs, values just "
    "below, at and above every threshold and limit in the code, unusual but accepted argument forms, and "
    "documented invalid inputs (cases that raise).",
    "Interactions across the whole scope: options combined with each other, state and the order of calls, "
    "documented examples from the docs and docstrings reproduced as cases.",
    "Every option and default across the whole scope, one at a time: each parameter at its default, at "
    "each documented non-default value, and in each accepted form.",
    "The main documented behaviour of everything in the scope with typical inputs.",
]

class _MutServer:

    def __init__(self, run, root: str):
        self.run, self.root = run, root
        self.proc = None
        self.gen = -1
        self.dir = None
        self.failures = 0
        self.buf = b""

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure(self) -> bool:
        run = self.run
        if self.alive() and self.gen == run.module_gen:
            return True
        self.stop()
        if self.failures >= 3:
            return False
        gen = run.module_gen
        modules = sorted(run.test_module(n) for n in list(run.case_sources) if n not in run.module_problems
                         and os.path.isfile(os.path.join(run.record_dir, run.test_module(n) + ".py")))
        d = run.new_run_dir()
        cfg = {"sys_path": [run.src_root] + run.extra_path + [run.record_dir], "modules": modules,
               "packages": [p["name"] for p in run.packages], "src_root": run.src_root,
               "tmp_base": os.path.join(d, "tmp")}
        cfg_path = os.path.join(d, "cfg.json")
        with open(cfg_path, "w") as fh:
            json.dump(cfg, fh)
        os.chmod(cfg_path, 0o644)
        env = run.base_env(os.path.join(d, "tmp"), os.pathsep.join([run.src_root] + run.extra_path))
        _seal(d)
        kw = {}
        ids = _nobody_ids()
        if ids:
            kw.update(user=ids[0], group=ids[1], extra_groups=[])
        try:
            self.proc = subprocess.Popen([run.py, run.runner_exec, "--tg-server", cfg_path], cwd=d, env=env,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         start_new_session=True, **kw)
        except Exception as e:
            log("[FAST] could not start the change checker: %s" % _fmt_exc(e))
            self.failures += 1
            self.proc = None
            _remove_dir(d)
            return False
        self.dir, self.buf = d, b""
        ready = self._read(90)
        if not ready or ready.get("kind") != "ready":
            log("[FAST] the change checker did not start")
            self.failures += 1
            self.stop()
            return False
        if ready.get("errors"):
            log("[FAST] change checker import problems: %s" % "; ".join(ready["errors"])[:300])
        self.gen = gen
        return True

    def _read(self, timeout: float):
        import select
        end = time.time() + timeout
        fd = self.proc.stdout.fileno()
        while b"\n" not in self.buf:
            left = end - time.time()
            if left <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], min(left, 1.0))
            if ready:
                data = os.read(fd, 1 << 20)
                if not data:
                    return None
                self.buf += data
            elif self.proc.poll() is not None:
                return None
        line, _, self.buf = self.buf.partition(b"\n")
        try:
            return json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            return None

    def request(self, req: dict, timeout: float):
        if not self.alive():
            return None
        try:
            self.proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except Exception:
            self.failures += 1
            self.stop()
            return None
        resp = self._read(timeout)
        if resp is None:
            self.failures += 1
            self.stop()
            return None
        return resp.get("recs")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            for fh in (proc.stdin, proc.stdout):
                try:
                    fh.close()
                except Exception:
                    pass
        if self.dir:
            _remove_dir(self.dir)
            self.dir = None
        self.gen = -1

class Run:
    def __init__(self, statement: str):
        self.statement = statement
        self.t_start = _T0
        raw = (os.getenv("AGENT_TIMEOUT") or "").strip()
        try:
            wall = float(raw) if raw else 0.0
        except ValueError:
            wall = 0.0
        self.wall = wall if wall > 0 else 1800.0
        self.reserve = max(75.0, min(150.0, 0.07 * self.wall))
        self.deadline = self.t_start + self.wall - self.reserve
        budget = 0.29
        try:
            budget = float(os.getenv("RIDGES_MAX_COST_USD") or budget)
        except ValueError:
            pass
        self.budget = budget
        self.py = sys.executable or shutil.which("python3") or "python3"
        self.stdlib = _stdlib_names()
        self.work = tempfile.mkdtemp(prefix="tg-")
        os.chmod(self.work, 0o755)
        self.run_counter = 0
        self.counter_lock = threading.Lock()
        self.repo = ""
        self.test_rel = "tests"
        self.suite_limit = 30.0
        self.packages: list = []
        self.src_root = os.path.join(self.work, "src")
        self.cases_dir = os.path.join(self.work, "cases")
        self.runner_file = os.path.join(self.work, "tg_runner.py")
        self.runner_exec = self.runner_file
        self.absent_dir = None
        self.record_dir = os.path.join(self.work, "record")
        self.test_names: dict = {}
        self.extra_path: list = []
        self.scope_files: list = []
        self.initial_test_entries = None
        self.case_sources: dict = {}
        self.module_problems: dict = {}
        self.case_problems: dict = {}
        self.records: dict = {}
        self.case_order: dict = {}
        self.excluded: dict = {}
        self.best_files: dict = {}
        self.notes: list = []
        self.llm = LLM(MODEL, self.budget, self.deadline, summarizer=self.conversation_summary)
        self.cov_lines: dict = {}
        self.line_cases: dict = {}
        self.mutants: dict = {}
        self.mutant_keys: set = set()
        self.mutant_status: dict = {}
        self.shown: set = set()
        self.skipped: set = set()
        self.retried: set = set()
        self.context_files: set = set()
        self.func_feedback: dict = {}
        self.mutant_full: dict = {}
        self.pool_cv = threading.Condition()
        self.pool_queue: list = []
        self.pool_seq = 0
        self.pool_threads: list = []
        self.pool_stop = False
        self.pool_paused = False
        self.pool_busy = 0
        self.pool_running: dict = {}
        self.worker_roots: list = []
        self.import_fragile: dict = {}
        self.unreliable: set = set()
        self.alone_ok: dict = {}
        self.import_fragile_done: set = set()
        self.repo_baseline: dict = {}
        self.module_gen = 0
        self.servers: dict = {}
        self.fast_stats: dict = {}
        self.case_owner: dict = {}
        self.writers: list = []
        self.shares: list = []
        self.writer_notes: dict = {}
        self.validation_noted: dict = {}
        self.validation_pending: dict = {}
        self.writer_losses: dict = {}
        self.shown_count: dict = {}
        self.cov_shown: dict = {}
        self.shown_to: dict = {}
        self.second_looked: set = set()
        self.kinds = 1
        self.writer_empty: set = set()
        self.inflight: dict = {}

    def left(self) -> float:
        return self.deadline - time.time()

    def end_left(self) -> float:
        return self.t_start + self.wall - 25 - time.time()

    def frac(self) -> float:
        return (time.time() - self.t_start) / self.wall

    def setup(self) -> None:
        st = self.statement
        m_repo = re.search(r"repository at `(/[^`]+)`", st)
        m_tests = re.search(r"[Aa]dd files only under `([^`]+)`", st) or re.search(r"only under `([^`]+/)`", st)
        asked_repo = asked_tests = None
        if not m_repo or not m_tests:
            asked_repo, asked_tests = self.ask_layout()
        candidates = []
        if m_repo:
            candidates.append(m_repo.group(1).rstrip("/"))
        elif asked_repo and os.path.isabs(asked_repo):
            candidates.append(asked_repo)
        candidates += [os.getcwd(), "/repo"]
        for c in candidates:
            if c and os.path.isdir(c) and (os.path.isdir(os.path.join(c, ".git")) or c == candidates[0]):
                self.repo = os.path.realpath(c)
                break
        if not self.repo:
            self.repo = os.path.realpath(os.getcwd())
        p = (m_tests.group(1) if m_tests else asked_tests or "").strip().rstrip("/")
        if os.path.isabs(p):
            p = os.path.relpath(p, self.repo) if p.startswith(self.repo + "/") else os.path.basename(p)
        if p and p != "." and not p.startswith(".."):
            self.test_rel = p
        self.snapshot_repo()
        m = re.search(r"finish in under (\d+(?:\.\d+)?) seconds", st)
        if m:
            self.suite_limit = float(m.group(1))
        log("[SETUP] repo=%s tests=%s suite_limit=%.0fs wall=%.0fs budget=$%.2f model=%s" % (
            self.repo, self.test_rel, self.suite_limit, self.wall, self.budget, MODEL))
        for d in (self.src_root, self.cases_dir, self.record_dir):
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o755)
        shutil.copyfile(os.path.abspath(__file__), self.runner_file)
        os.chmod(self.runner_file, 0o644)
        try:
            import py_compile
            compiled = py_compile.compile(self.runner_file, cfile=self.runner_file + "c", doraise=True)
            os.chmod(compiled, 0o644)
            self.runner_exec = compiled
        except Exception:
            self.runner_exec = self.runner_file
        tdir = os.path.join(self.repo, self.test_rel)
        self.initial_test_entries = set(os.listdir(tdir)) if os.path.isdir(tdir) else None
        self.discover_packages()
        if self.packages:
            self.copy_library()
            try:
                self.prepare_absent()
            except Exception:
                log("[SETUP] optional packages: %s" % traceback.format_exc()[-300:])

    def discover_packages(self) -> None:
        st = self.statement
        names = []

        def add(name):
            root = name.split(".")[0]
            if root.isidentifier() and root not in names:
                names.append(root)
        for base in (self.repo, os.path.join(self.repo, "src"), os.path.join(self.repo, "lib")):
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.listdir(base)):
                full = os.path.join(base, entry)
                if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")) and \
                        entry.lower() not in _NOT_PACKAGES:
                    add(entry)
        for tok in re.findall(r"[Ii]mport(?:ed)? (?:only |from )?`([A-Za-z_][\w.]*)`", st):
            add(tok)
        for tok in re.findall(r"`([A-Za-z_][\w.]*)`", st):
            add(tok)
        names = [n for n in names if n not in self.stdlib and n not in ("pytest", "tests", "test", "docs")
                 and not n.startswith("_")][:60]
        recs = self.runner({"mode": "api", "candidates": names}, timeout=120, as_nobody=False,
                           pythonpath=os.environ.get("PYTHONPATH", ""), sys_path=[])
        found = []
        for r in recs:
            if r.get("kind") != "api" or r.get("error") or not r.get("file"):
                continue
            f = os.path.realpath(r["file"])
            r["is_pkg"] = bool(r.get("path"))
            r["dir"] = os.path.dirname(f) if r["is_pkg"] else f
            r["in_repo"] = f.startswith(self.repo + os.sep)
            found.append(r)
        chosen = [r for r in found if r["in_repo"]]
        if not chosen:
            backticked = re.findall(r"`([A-Za-z_]\w*)`", st)
            chosen = [r for r in found if r["name"] in backticked[:12]] or found[:1]
        explicit = [r for r in chosen if re.search(r"[Ii]mport(?:ed)? from `%s[`.]" % re.escape(r["name"]), st)]
        if explicit:
            chosen = explicit
        self.packages = chosen[:3]
        log("[SETUP] packages: %s" % ", ".join("%s (%s)" % (p["name"], p["dir"]) for p in self.packages))

    def copy_library(self) -> None:
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
        for p in self.packages:
            dst = os.path.join(self.src_root, os.path.basename(p["dir"]))
            if p["is_pkg"]:
                shutil.copytree(p["dir"], dst, ignore=ignore, dirs_exist_ok=True)
            else:
                shutil.copy2(p["dir"], dst)
            p["rel"] = os.path.basename(p["dir"])
        try:
            import compileall
            compileall.compile_dir(self.src_root, quiet=2, workers=1)
        except Exception:
            pass
        _make_readable(self.src_root)
        probe = self.runner({"mode": "api", "candidates": [p["name"] for p in self.packages]}, timeout=120,
                            pythonpath=self.src_root, sys_path=[self.src_root])
        bad = [r for r in probe if r.get("kind") == "api" and
               (r.get("error") or not str(r.get("file") or "").startswith(self.src_root))]
        if bad:
            log("[SETUP] the package does not import from the copy alone (%s); adding the repository" %
                (bad[0].get("error") or bad[0].get("file")))
            self.extra_path = [self.repo]
        for p in self.packages:
            for r in probe:
                if r.get("name") == p["name"]:
                    p["api"] = r.get("api") or p.get("api") or ""

    def prepare_absent(self) -> None:
        declared = _declared_dependencies(self.repo)
        recs = self.runner({"mode": "deps", "declared": declared}, timeout=120, as_nobody=False,
                           pythonpath=os.environ.get("PYTHONPATH", ""), sys_path=[])
        optional = next((r.get("optional") or [] for r in recs if r.get("kind") == "deps"), [])
        if not optional:
            return
        d = os.path.join(self.work, "absent")
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o755)
        for _ in range(8):
            with open(os.path.join(d, "sitecustomize.py"), "w") as fh:
                fh.write(_ABSENT_SRC % json.dumps(sorted(optional)))
            os.chmod(os.path.join(d, "sitecustomize.py"), 0o644)
            probe = self.runner({"mode": "api", "candidates": [p["name"] for p in self.packages]}, timeout=120,
                                pythonpath=os.pathsep.join([d, self.src_root] + self.extra_path),
                                sys_path=[self.src_root])
            bad = [r for r in probe if r.get("kind") == "api" and r.get("error")]
            if not bad:
                self.absent_dir = d
                log("[SETUP] declared dependencies %s; %d optional packages made unavailable in one recording "
                    "and one suite run: %s" % (declared, len(optional), ", ".join(optional[:12])))
                return
            mm = re.search(r"No module named '([\w.]+)'", bad[0].get("error") or "")
            needed = mm.group(1).split(".")[0] if mm else ""
            if needed not in optional:
                break
            optional.remove(needed)
        log("[SETUP] the package does not import without its optional packages; no such run")

    def new_run_dir(self) -> str:
        with self.counter_lock:
            self.run_counter += 1
            n = self.run_counter
        d = os.path.join(self.work, "run", "%05d" % n)
        for sub in ("tmp", "out"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
            os.chmod(os.path.join(d, sub), 0o777)
        os.chmod(os.path.dirname(d), 0o755)
        os.chmod(d, 0o755)
        return d

    def base_env(self, tmp: str, pythonpath: str, hash_seed: str = "0") -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHON"))}
        env.update({"HOME": tmp, "TMPDIR": tmp, "PYTHONHASHSEED": hash_seed, "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": pythonpath, "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"})
        env.pop("OPENROUTER_API_KEY", None)
        return env

    def runner(self, cfg: dict, *, timeout: float, as_nobody: bool = True, pythonpath: str | None = None,
               hash_seed: str = "0", sys_path: list | None = None, absent: bool = False) -> list:
        d = self.new_run_dir()
        cfg = dict(cfg)
        cfg["out"] = os.path.join(d, "out", "out.jsonl")
        cfg["tmp_base"] = os.path.join(d, "tmp")
        cfg.setdefault("packages", [p["name"] for p in self.packages])
        cfg["sys_path"] = sys_path if sys_path is not None else [self.src_root] + self.extra_path + [self.record_dir]
        cfg_path = os.path.join(d, "cfg.json")
        with open(cfg_path, "w") as fh:
            json.dump(cfg, fh)
        os.chmod(cfg_path, 0o644)
        pp = pythonpath if pythonpath is not None else os.pathsep.join([self.src_root] + self.extra_path)
        if absent and self.absent_dir:
            pp = self.absent_dir + os.pathsep + pp
        env = self.base_env(os.path.join(d, "tmp"), pp, hash_seed)
        _seal(d)
        rc, output = _run_proc([self.py, self.runner_exec, "--tg-runner", cfg_path], cwd=d,
                               env=env, timeout=timeout, as_nobody=as_nobody)
        recs = []
        try:
            with open(cfg["out"], errors="replace") as fh:
                for line in fh:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        if rc is None:
            recs.append({"kind": "runner_timeout"})
        elif not recs or recs[-1].get("kind") != "end":
            recs.append({"kind": "runner_crash", "output": output[-2000:]})
        _remove_dir(d)
        return recs

    def package_files(self) -> list:
        files = []
        for p in self.packages:
            base = os.path.join(self.src_root, p["rel"])
            if os.path.isfile(base):
                files.append(p["rel"])
                continue
            for root, dirs, fs in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for f in sorted(fs):
                    if f.endswith(".py"):
                        files.append(os.path.relpath(os.path.join(root, f), self.src_root))
        return files

    def scope_text(self) -> str:
        keep, skipping = [], False
        for line in self.statement.split("\n"):
            if re.match(r"\s*#+\s", line):
                low = line.lower()
                skipping = any(w in low for w in ("not part of", "out of scope", "not in scope", "excluded",
                                                  "unstable", "non-goals"))
            if not skipping:
                keep.append(line)
        return "\n".join(keep)

    def mentioned_files(self, files: list) -> list:
        st = self.scope_text()
        out = []
        by_stem = {}
        for rel in files:
            by_stem.setdefault(os.path.basename(rel)[:-3], []).append(rel)
        for rel in files:
            dotted = rel[:-3].replace(os.sep, ".")
            stem = os.path.basename(rel)[:-3]
            if re.search(re.escape(rel), st) or re.search(r"`%s`" % re.escape(dotted), st):
                out.append(rel)
            elif stem not in ("__init__", "utils", "util", "core", "compat", "base", "main", "_compat") \
                    and re.search(r"`%s`" % re.escape(stem), st) \
                    and rel == min(by_stem[stem], key=lambda r: (r.count(os.sep), r)):
                out.append(rel)
        names = set(re.findall(r"`([A-Za-z_]\w*)(?:\(\))?`", st)) | set(re.findall(r"`[\w.]*\.([A-Za-z_]\w*)`", st))
        for rel in files:
            if rel in out:
                continue
            try:
                with open(os.path.join(self.src_root, rel), errors="replace") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                continue
            defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))}
            if len(defined & names) >= 1 and not os.path.basename(rel).startswith("test"):
                out.append(rel)
        return out

    def build_context(self) -> str:
        files = self.package_files()
        mentioned = self.mentioned_files(files)
        inits = [f for f in files if f.endswith("__init__.py") and f.count(os.sep) <= 1]
        self.scope_files = list(dict.fromkeys(mentioned + inits)) or files[:6]
        parts = ["# Task\n\n" + self.statement.strip()]
        facts = ["# Facts gathered by the tooling", "",
                 "- Repository root: %s" % self.repo,
                 "- Python: %s" % sys.version.split()[0],
                 "- The generated suite goes to `%s/`; you only write case files." % self.test_rel]
        for p in self.packages:
            facts.append("- Package under test: `%s`%s, imported from %s" % (
                p["name"], " %s" % p["version"] if p.get("version") else "", p["dir"]))
        parts.append("\n".join(facts))
        listing = []
        for rel in files:
            try:
                with open(os.path.join(self.src_root, rel), errors="replace") as fh:
                    n = sum(1 for _ in fh)
            except OSError:
                n = 0
            listing.append("%s (%d lines)" % (rel, n))
        parts.append("# Package files\n\n" + "\n".join(listing[:200]))
        for p in self.packages:
            if p.get("api"):
                parts.append("# Public names of `%s`\n\n%s" % (p["name"], _clip(p["api"], 14000)))
        budget = 90000
        included = []
        for rel in self.scope_files:
            try:
                with open(os.path.join(self.src_root, rel), errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if len(text) <= budget:
                parts.append("# Source: %s\n\n```python\n%s\n```" % (rel, _number(text)))
                budget -= len(text)
                included.append(rel)
                self.context_files.add(rel)
            else:
                parts.append("# Outline: %s (too long to include; only this outline is shown)\n\n%s" % (
                    rel, _outline(text)))
        for rel in files:
            if rel in included or rel in self.scope_files or budget < 2000:
                continue
            try:
                with open(os.path.join(self.src_root, rel), errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            outline = _outline(text)
            parts.append("# Outline: %s\n\n%s" % (rel, outline))
            budget -= len(outline)
        docs = []
        for tok in re.findall(r"`([\w./-]+\.(?:rst|md|txt))`", self.statement):
            if os.path.isfile(os.path.join(self.repo, tok)) and tok not in docs:
                docs.append(tok)
        dbudget = 30000
        for rel in docs[:4]:
            try:
                with open(os.path.join(self.repo, rel), errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            chunk = _clip(text, max(3000, dbudget // max(1, len(docs))))
            parts.append("# Documentation: %s\n\n%s" % (rel, chunk))
        return "\n\n".join(parts)

    def allowed_roots(self) -> set:
        return set(self.stdlib) | {"pytest"} | {p["name"] for p in self.packages}

    def import_rule(self):
        if not hasattr(self, "_import_rule"):
            pkg_names = {p["name"] for p in self.packages}
            rule = set()
            for m in re.finditer(r"\b(?:[Oo]nly [Ii]mport (?:from )?|[Ii]mport (?:only (?:from )?|from only ))((?:[^.;\n]|\.(?=\S))+)|\b[Ii]mport (?:from )?((?:[^.;\n]|\.(?=\S))+?) (?:only|\(only (?:that|these) modules?\))(?=[.;\n]|$)", self.statement):
                if m.group(2) is not None or m.group(0).lower().startswith("only import"):
                    remainder = re.sub(r"`[A-Za-z_][\w.]*`", "", m.group(1) or m.group(2))
                    if re.sub(r"\b(?:and|or)\b|[\s,]", "", remainder):
                        continue
                if m.group(0).lower().startswith("only import"):
                    prefix = re.split(r"[.;\n]", self.statement[:m.start()])[-1].strip().lower()
                    if prefix and not re.fullmatch(r"(?:you|tests?|the tests?|we) (?:may|must|should|can)", prefix):
                        continue
                before = self.statement[max(0, m.start() - 16):m.start()].lower()
                if re.search(r"\b(?:not|never|no|avoid|don't|cannot|can't|must not)\s+$", before):
                    continue
                rule |= {t for t in re.findall(r"`([A-Za-z_][\w.]*)`", m.group(1) or m.group(2)) if t.split(".")[0] in pkg_names}
            self._import_rule = rule or None
        return self._import_rule

    def import_rule_problem(self, modname: str, names) -> str:
        rule = self.import_rule()
        if not rule or modname.split(".")[0] not in {p["name"] for p in self.packages}:
            return ""
        if modname in rule:
            return ""
        if names is None and any(r.startswith(modname + ".") for r in rule):
            return ""
        if names is not None and all("%s.%s" % (modname, n) in rule for n in names):
            return ""
        return "the task allows imports only from %s; import the public names from there" % (
            ", ".join("`%s`" % r for r in sorted(rule)))

    def lint(self, source: str):
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return ["syntax error: %s (line %s)" % (e.msg, e.lineno)], {}, []
        mod_problems, case_problems, cases = [], {}, []
        allowed = self.allowed_roots()
        pkg_names = {p["name"] for p in self.packages}
        lib_aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    root = a.name.split(".")[0]
                    if root not in allowed:
                        mod_problems.append("line %d: `import %s` is not allowed (package under test, pytest and "
                                            "the standard library only)" % (node.lineno, a.name))
                    if any(part.startswith("_") for part in a.name.split(".")):
                        mod_problems.append("line %d: `import %s` uses a private module" % (node.lineno, a.name))
                    if root in pkg_names:
                        lib_aliases.add((a.asname or a.name).split(".")[0])
                    problem = self.import_rule_problem(a.name, None)
                    if problem:
                        mod_problems.append("line %d: %s" % (node.lineno, problem))
            elif isinstance(node, ast.ImportFrom):
                modname = node.module or ""
                root = modname.split(".")[0]
                if node.level:
                    mod_problems.append("line %d: relative imports are not allowed" % node.lineno)
                elif modname != "__future__":
                    if root not in allowed:
                        mod_problems.append("line %d: `from %s import ...` is not allowed (package under test, "
                                            "pytest and the standard library only)" % (node.lineno, modname))
                    if any(part.startswith("_") for part in modname.split(".")):
                        mod_problems.append("line %d: `from %s import ...` uses a private module" % (node.lineno, modname))
                    for a in node.names:
                        if a.name.startswith("_"):
                            mod_problems.append("line %d: imports the private name `%s`" % (node.lineno, a.name))
                        if root in pkg_names:
                            lib_aliases.add(a.asname or a.name)
                    problem = self.import_rule_problem(modname, [a.name for a in node.names])
                    if problem:
                        mod_problems.append("line %d: %s" % (node.lineno, problem))
        lib_helpers = _library_helpers(tree, lib_aliases)
        for node in tree.body:
            what = _import_time_library_use(node, lib_aliases, lib_helpers)
            if what:
                mod_problems.append(
                    "line %d: %s runs library code when the file is imported; if a change to the library breaks that "
                    "code, the file cannot be imported and none of its tests can report the change. Create such "
                    "classes, decorated functions and values inside a helper function that the cases call (for "
                    "example `def make_plugin():` defining the class and returning an instance)" % (node.lineno, what))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("case_"):
                    problem = _lint_case(node)
                    if problem:
                        case_problems[node.name] = problem
                    cases.append(node.name)
                elif node.name.startswith("test"):
                    mod_problems.append("line %d: rename helper `%s` (names starting with `test` are reserved)" % (
                        node.lineno, node.name))
                continue
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                mod_problems.append("line %d: rename class `%s` (names starting with `Test` are reserved)" % (
                    node.lineno, node.name))
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "EXCEPTION_CLASSES" for t in targets) and \
                        not (isinstance(value, ast.Constant) and isinstance(value.value, bool)):
                    mod_problems.append("line %d: EXCEPTION_CLASSES must be True or False" % node.lineno)
                continue
            if isinstance(node, ast.ClassDef):
                continue
            mod_problems.append("line %d: only imports, constants, helper functions and classes are allowed at "
                                "module level" % node.lineno)
        seen = set()
        for c in cases:
            if c in seen:
                case_problems[c] = "defined twice in the file"
            seen.add(c)
        users = _helper_users(tree, cases)

        def report(node, text):
            owner = _owner_top(tree, node)
            if owner is None:
                mod_problems.append(text)
            elif owner.startswith("case_"):
                case_problems.setdefault(owner, text)
            else:
                for c in users.get(owner, ()):
                    case_problems.setdefault(c, "%s (in `%s`, which this case uses)" % (text, owner))
        for node in ast.walk(tree):
            msg = _forbidden(node)
            if msg:
                report(node, "line %d: %s" % (node.lineno, msg))
        return mod_problems[:10], case_problems, cases

    def add_case_file(self, fname: str, source: str) -> str:
        source = _adopt_test_names(source)
        source, removed = _repair_source(source)
        if removed:
            log("[MEND] %s: dropped %d broken top-level block(s)" % (fname, removed))
        source, localized = _localize_library_values(source, {p["name"] for p in getattr(self, "packages", [])})
        if localized:
            log("[MEND] %s: moved %d module-level statement(s) that run library code into the functions that use them"
                % (fname, localized))
        if not source.strip() or not re.search(r"(?m)^def case_\w+\s*\(", source):
            return "%s: not saved (no usable case_ after mend)" % fname
        name = fname[:-3]
        if name not in self.case_sources and len(self.case_sources) >= MAX_CASE_FILES:
            return "%s: not saved (at most %d case files)" % (fname, MAX_CASE_FILES)
        self.case_sources[name] = source
        path = os.path.join(self.cases_dir, fname)
        with open(path, "w") as fh:
            fh.write(source)
        os.chmod(path, 0o644)
        for store in (self.records, self.excluded, self.case_problems):
            for key in [k for k in store if k.startswith(name + "::")]:
                del store[key]
        self.module_problems.pop(name, None)
        return ""

    def keep_previous_cases(self, module: str, new_source: str):
        if module not in self.case_sources:
            return None
        good = [c for c in self.case_order.get(module, []) if "%s::%s" % (module, c) in self.records]
        # look at the new version as add_case_file will store it: broken blocks dropped, and not at all when
        # nothing usable is left (then the old file simply stays)
        fixed, _ = _repair_source(_adopt_test_names(new_source))
        if not fixed.strip() or not re.search(r"(?m)^def case_\w+\s*\(", fixed):
            return None
        new_cases = {n.name for n in ast.parse(fixed).body if isinstance(n, ast.FunctionDef)}
        keep = [c for c in good if c not in new_cases]
        if not keep:
            return None
        try:
            tree = ast.parse(self.case_sources[module])
            tree.body = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name.startswith("case_")
                                                     and n.name not in keep)]
            prev_source = ast.unparse(tree) + "\n"
        except Exception:
            return None
        k = 1
        while "%s_prev%d" % (module, k) in self.case_sources:
            k += 1
        prev = "%s_prev%d" % (module, k)
        if self.add_case_file(prev + ".py", prev_source):
            return None
        return prev, ("%s.py was replaced; %d recorded cases missing from the new version were kept in %s.py" % (
            module, len(keep), prev))

    def record_modules(self, names: list, mended: bool = False) -> dict:
        report = {}
        todo = []
        for name in names:
            mprobs, cprobs, cases = self.lint(self.case_sources[name])
            self.case_order[name] = cases
            rep = {"cases": len(cases), "values": 0, "raises": [], "problems": [], "module": list(mprobs)}
            report[name] = rep
            if mprobs:
                self.module_problems[name] = list(mprobs)
                continue
            for c, prob in cprobs.items():
                self.case_problems["%s::%s" % (name, c)] = prob
                rep["problems"].append("%s: %s" % (c, prob))
            keep = [c for c in cases if c not in cprobs]
            try:
                self.test_names[name] = _test_names(self.case_sources[name])
                self.write_record_module(name, set(keep))
            except Exception as e:
                err = "could not turn the file into tests: " + _fmt_exc(e)
                rep["module"].append(err)
                self.module_problems[name] = [err]
                continue
            todo += ["%s::%s" % (name, c) for c in keep]
        if not todo:
            return report
        first = self._record(todo, trace=True, reverse=False, seed="0")
        second = self._record(todo, trace=False, reverse=True, seed="4217", clock_shift=CLOCK_SHIFT)
        third = self._record(todo, trace=False, reverse=False, seed="91", clock_shift=CLOCK_SHIFT / 37.0,
                             absent=True)
        for name, errs in first.get("__module_errors__", {}).items():
            if name in report:
                report[name]["module"] += errs
                self.module_problems.setdefault(name, []).extend(errs)
        again = {}
        if not mended:
            for name, errs in (first.get("__module_errors__") or {}).items():
                new, gone = _mend_imports(self.case_sources.get(name, ""), "\n".join(errs))
                if new:
                    log("[MEND] %s: dropped import-broken names %s" % (name, gone))
                    self.case_sources[name] = new
                    self.module_problems.pop(name, None)
                    again[name] = (gone, errs)
            if again:
                report.update(self.record_modules(sorted(again), mended=True))
                for name, (gone, errs) in again.items():
                    cause = re.sub(r"\s*\([^()]*\.py\)\s*$", "", errs[0].replace("importing the file failed: ", ""))
                    why = "left out: it uses a name that cannot be imported (%s)" % _clip(cause, 240)
                    for c in gone:
                        self.case_problems["%s::%s" % (name, c)] = why
                        report[name]["problems"].append("%s: %s" % (c, why))
                        report[name]["cases"] += 1
        for key in todo:
            name, case = key.split("::")
            rep = report[name]
            if name in self.module_problems or name in again:
                continue
            a, b, c = first.get(key), second.get(key), third.get(key)
            if a is None:
                problem = "did not finish (it may hang)"
            elif a["status"] == "error":
                problem = a.get("error", "error")
            elif self.absent_dir and not self.differs(a, b) and self.differs(a, c):
                problem = ("result changes when the optional third-party packages installed here are missing; they "
                           "may not be installed where the suite runs, so leave out what needs them")
            elif any(x is None or x["status"] != a["status"] or (a["status"] == "value" and x.get("src") != a.get("src"))
                     for x in (b, c)):
                other = next(x for x in (b, c) if x is None or x["status"] != a["status"] or
                             (a["status"] == "value" and x.get("src") != a.get("src")))
                problem = ("result differs between two runs (depends on order, hashing, the current clock reading or "
                           "state)" + _difference(a, other))
            else:
                problem = ""
            if problem:
                self.case_problems[key] = problem
                rep["problems"].append("%s: %s" % (case, problem))
                continue
            self.records[key] = a
            if a["status"] == "value":
                rep["values"] += 1
            else:
                rep["raises"].append("%s -> %s: %s" % (case, a.get("exc"), (a.get("msg") or "")[:100]))
        self.rebuild_coverage()
        if self.pool_threads:
            self.refresh_mutants()
        return report

    @staticmethod
    def differs(a, x) -> bool:
        return x is None or x["status"] != a["status"] or (a["status"] == "value" and x.get("src") != a.get("src"))

    def _record(self, keys: list, *, trace: bool, reverse: bool, seed: str, clock_shift: float = 0.0,
                absent: bool = False) -> dict:
        back = {self.test_key(k): k for k in keys}
        order = [self.test_key(k) for k in (reversed(keys) if reverse else keys)]
        modules = sorted({k.split("::")[0] for k in order})
        mod_back = {self.test_module(k.split("::")[0]): k.split("::")[0] for k in keys}
        results, module_errors = {}, {}
        attempts = 0
        while order and attempts < 4 and self.left() > 20:
            attempts += 1
            timeout = min(self.left() - 10, 60 + len(order) * (1.5 if trace else 0.5))
            cfg = {"mode": "record", "modules": modules, "cases": order,
                   "timeout": CASE_TIMEOUT * (3 if trace else 1), "clock_shift": clock_shift}
            if trace:
                cfg["trace_root"] = self.src_root + os.sep
            recs = self.runner(cfg, timeout=max(10, timeout), hash_seed=seed, absent=absent)
            started = None
            for r in recs:
                if r.get("kind") == "module_error":
                    module_errors.setdefault(mod_back.get(r["module"], r["module"]), []).append(
                        "importing the file failed: " + r["error"])
                elif r.get("kind") == "start":
                    started = r["key"]
                elif r.get("kind") == "case":
                    results[r["key"]] = r
                    started = None
            progressed = any(k in results for k in order)
            order = [k for k in order if k not in results]
            if not order:
                break
            if started and started in order:
                results[started] = {"key": started, "status": "error",
                                    "error": "did not finish in time (hangs or is far too slow)"}
                order.remove(started)
            elif not progressed:
                break
        out = {back[k]: v for k, v in results.items() if k in back}
        out["__module_errors__"] = module_errors
        return out

    def pins_classes(self, module: str) -> bool:
        return bool(re.search(r"(?m)^EXCEPTION_CLASSES\s*(?::[^=]*)?=\s*True\b", self.case_sources.get(module, "")))

    def test_module(self, module: str) -> str:
        return "test_" + (module[len("cases_"):] if module.startswith("cases_") else module)

    def test_key(self, key: str) -> str:
        module, case = key.split("::")
        return "%s::%s" % (self.test_module(module), self.test_names[module][case])

    def write_record_module(self, module: str, keep: set) -> None:
        topic = module[len("cases_"):] if module.startswith("cases_") else module
        text = _test_module_source(self.case_sources[module], topic, self.test_names[module], keep)
        path = os.path.join(self.record_dir, self.test_module(module) + ".py")
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        try:
            import py_compile
            os.chmod(py_compile.compile(path, doraise=True), 0o644)
            os.chmod(os.path.join(self.record_dir, "__pycache__"), 0o755)
        except Exception:
            pass
        self.module_gen += 1

    def rebuild_coverage(self) -> None:
        cov, lc = {}, {}
        for key, rec in self.records.items():
            if key in self.excluded:
                continue
            for rel, line in rec.get("lines") or []:
                cov.setdefault(rel, set()).add(line)
                lc.setdefault((rel, line), set()).add(key)
        self.cov_lines, self.line_cases = cov, lc

    def usable_keys(self, module: str) -> list:
        out = []
        for c in self.case_order.get(module, []):
            key = "%s::%s" % (module, c)
            if key in self.records and key not in self.excluded:
                out.append(key)
        return out

    def format_report(self, report: dict) -> str:
        lines = []
        for name in sorted(report):
            rep = report[name]
            if rep["module"]:
                lines.append("%s.py: NOT USABLE until fixed:" % name)
                lines += ["  - " + p for p in rep["module"][:8]]
                continue
            lines.append("%s.py: %d cases, %d recorded values, %d raise" % (
                name, rep["cases"], rep["values"], len(rep["raises"])))
            if rep["raises"]:
                lines.append("  raising cases (make sure each is meant to raise; a typo also raises):")
                lines += ["    " + x for x in rep["raises"][:15]]
                if len(rep["raises"]) > 15:
                    lines.append("    ... %d more" % (len(rep["raises"]) - 15))
            if rep["problems"]:
                lines.append("  dropped until fixed:")
                lines += ["    " + x for x in rep["problems"][:20]]
        total = sum(1 for k in self.records if k not in self.excluded)
        lines.append("Recorded cases in the suite so far: %d." % total)
        return "\n".join(lines)

    def conversation_summary(self) -> str:
        lines = ["Summary of my earlier work (the conversation was shortened). These case files are saved and "
                 "recorded; I keep adding new files rather than rewriting them:"]
        for name in sorted(self.case_sources):
            keys = self.usable_keys(name)
            lines.append("- %s.py: %d recorded cases%s" % (name, len(keys),
                                                           " (not usable)" if name in self.module_problems else ""))
        stats = self.mutation_stats()
        if stats:
            lines.append("Mutation analysis so far: %d changes detected, %d undetected, %d set aside." % (
                stats.get("killed", 0), stats.get("survived", 0), len(self.skipped)))
        return "\n".join(lines)

    def build_suite(self) -> tuple:
        files, idmap = {}, {}
        for name in sorted(self.case_sources):
            if name in self.module_problems:
                continue
            keys = self.usable_keys(name)
            if not keys:
                continue
            topic = name[len("cases_"):] if name.startswith("cases_") else name
            names = self.test_names.get(name) or _test_names(self.case_sources[name])
            recs = {k.split("::")[1]: self.records[k] for k in keys}
            if not self.pins_classes(name):
                recs = {c: dict(r, cls=None) for c, r in recs.items()}
            try:
                src = _test_module_source(self.case_sources[name], topic, names, set(recs), recs)
            except Exception as e:
                log("[BUILD] %s: %s" % (name, _fmt_exc(e)))
                continue
            tname = "test_" + topic
            files[tname + ".py"] = src
            for case in recs:
                idmap["%s::%s" % (tname, names[case])] = "%s::%s" % (name, case)
        if files:
            files["conftest.py"] = CONFTEST_SRC
        return files, idmap

    def ci_run(self, files: dict, hash_seed: str = "0", src_root: str | None = None, absent: bool = False) -> dict:
        d = self.new_run_dir()
        tdir = os.path.join(d, os.path.basename(self.test_rel) or "tests")
        os.makedirs(tdir)
        for rel, text in files.items():
            with open(os.path.join(tdir, rel), "w") as fh:
                fh.write(text)
            os.chmod(os.path.join(tdir, rel), 0o444)
        os.chmod(tdir, 0o555)
        _seal(d)
        out_dir = os.path.join(d, "out")
        junit = os.path.join(out_dir, "junit.xml")
        paths = [src_root or self.src_root] + self.extra_path
        if absent and self.absent_dir:
            paths = [self.absent_dir] + paths
        env = self.base_env(os.path.join(d, "tmp"), os.pathsep.join(paths), hash_seed)
        cmd = [self.py, "-m", "pytest", tdir, "-q", "-p", "no:cacheprovider", "-c", os.devnull,
               "--rootdir=" + d, "--junitxml=" + junit]
        t0 = time.time()
        import resource
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        rc, output = _run_proc(cmd, cwd=d, env=env,
                               timeout=max(20, min(self.end_left() - 5, self.suite_limit * 4 + 30)), as_nobody=True)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        secs = time.time() - t0
        cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
        tests, collect_errors = {}, []
        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(junit).getroot()
            for tc in root.iter("testcase"):
                cls = tc.get("classname") or ""
                if not cls and any(child.tag == "error" for child in tc):
                    collect_errors.append((tc.get("name") or "").split(".")[-1] + ".py")
                    continue
                tid = "%s::%s" % (cls.split(".")[-1], tc.get("name"))
                outcome = "passed"
                for child in tc:
                    if child.tag in ("failure", "error"):
                        outcome = "failed"
                        break
                    if child.tag == "skipped":
                        outcome = "skipped"
                tests[tid] = {"outcome": outcome, "time": float(tc.get("time") or 0)}
        except Exception:
            pass
        for line in output.split("\n"):
            mm = re.match(r"ERROR (?:collecting )?\S*?(test_\w+\.py)(?:\s|$)", line.strip())
            if mm and mm.group(1) not in collect_errors:
                collect_errors.append(mm.group(1))
        os.chmod(tdir, 0o755)
        _remove_dir(d)
        return {"rc": rc, "tests": tests, "secs": secs, "cpu": cpu, "output": output[-3000:],
                "collect_errors": collect_errors}

    def note_validation_failure(self, module: str, identity: str, output, cause: str = "during pytest validation") -> None:
        if not isinstance(module, str) or not module or not isinstance(identity, str) or not identity:
            return
        owner = self.case_owner.get(module)
        if type(owner) is not int or not 0 <= owner < len(self.writers):
            return
        token = (owner, identity)
        source = self.case_sources.get(module, "")
        if token in self.validation_noted and self.validation_noted[token] == source:
            return
        pending = self.validation_pending.setdefault(owner, [])
        if len(pending) >= 4:
            return
        context = _clip(output, 500) if isinstance(output, str) and output.strip() else "No additional output was captured."
        text = ("Your recorded case or file %s was excluded because its generated tests did not pass "
                "%s. Check whether the current public contract supports its intended "
                "behavior. Only if a stable correction is warranted, write corrected cases in a new "
                "case file using the existing recording flow; otherwise leave it excluded. Keep the "
                "other valid cases. Actual validation output:\n%s" % (_clip(identity, 200), cause, context))
        self.writer_notes.setdefault(owner, []).append(text)
        pending.append(text)
        self.validation_noted[token] = source


    def verify_suite(self, confirm_runs: int = 2) -> dict:
        self.pause_pool(True)
        try:
            return self._verify_suite(confirm_runs)
        finally:
            self.pause_pool(False)

    def _verify_suite(self, confirm_runs: int) -> dict:
        clean = 0
        for attempt in range(8):
            files, idmap = self.build_suite()
            if not files or self.end_left() < 20:
                break
            seed = "0" if clean == 0 else str(1000 + attempt)
            res = self.ci_run(files, hash_seed=seed, absent=clean > 0)
            failed = [tid for tid, t in res["tests"].items() if t["outcome"] != "passed"]
            broken = []
            if res["rc"] not in (0, 1):
                broken = self.broken_files(res, files, seed)
                if not broken:
                    log("[CI] run %d seed=%s: pytest exit %s and no file to blame; keeping the last passing suite" % (
                        attempt + 1, seed, res["rc"]))
                    break
            log("[CI] run %d seed=%s rc=%s tests=%d failed=%d secs=%.1f cpu=%.1f broken=%s" % (
                attempt + 1, seed, res["rc"], len(res["tests"]), len(failed), res["secs"], res.get("cpu") or 0,
                broken[:3]))
            for f in broken:
                mod = "cases_" + f[len("test_"):-3]
                self.module_problems.setdefault(mod, []).append(
                    "the generated tests for this file do not run under pytest: " + _clip(res["output"], 500))
                self.notes.append("%s.py was dropped: its generated tests do not run under pytest (%s)" % (
                    mod, _clip(res["output"], 300)))
                self.note_validation_failure(mod, mod + ".py", res.get("output"))
            dropped = 0
            for tid in failed:
                key = idmap.get(tid)
                if key and key not in self.excluded:
                    self.excluded[key] = "failed when the suite ran under pytest"
                    dropped += 1
                    self.note_validation_failure(key.split("::", 1)[0], key, res.get("output"))
            if dropped:
                self.notes.append("%d cases were dropped because their tests failed under pytest: %s" % (
                    dropped, ", ".join(idmap.get(t, t).split("::")[-1] for t in failed[:10])))
            if broken or failed:
                clean = 0
                self.rebuild_coverage()
                continue
            if not res["tests"]:
                break
            if min(res["secs"], res.get("cpu") or res["secs"]) > self.suite_limit * 0.45:
                if self._trim_slow(res, idmap):
                    clean = 0
                    self.rebuild_coverage()
                    continue
                # the tests themselves fit their share; the rest is pytest start-up and collection, which
                # dropping tests would not shorten
                log("[CI] run %d: %.1fs, but only %.1fs in the tests themselves; nothing to trim" % (
                    attempt + 1, res["secs"], sum(t["time"] for t in res["tests"].values())))
            clean += 1
            self.best_files = files
            if clean >= confirm_runs:
                return files
        return self.best_files

    def broken_files(self, res: dict, files: dict, seed: str) -> list:
        tfiles = [f for f in files if f.startswith("test_")]
        broken = [f for f in res.get("collect_errors") or [] if f in tfiles]
        if broken:
            return list(dict.fromkeys(broken))
        for f in tfiles:
            if self.end_left() < 30:
                break
            r1 = self.ci_run({f: files[f], "conftest.py": files["conftest.py"]}, hash_seed=seed)
            if r1["rc"] not in (0, 1):
                broken.append(f)
        return broken



    def _trim_slow(self, res: dict, idmap: dict) -> int:
        """Drop the slowest tests until the tests' own time fits the budget; returns how many were dropped."""
        budget = self.suite_limit * 0.3
        times = sorted(((t["time"], tid) for tid, t in res["tests"].items()), reverse=True)
        total = sum(t for t, _ in times)
        dropped = 0
        for t, tid in times:
            if total <= budget:
                break
            key = idmap.get(tid)
            if key and key not in self.excluded:
                self.excluded[key] = "too slow"
                total -= t
                dropped += 1
        return dropped


    def changed_files(self):
        r = subprocess.run(["git", "-C", self.repo, "diff", "--name-only", "-z", "HEAD"], timeout=30,
                           capture_output=True)
        if r.returncode != 0:
            return None
        return [x for x in r.stdout.decode(errors="replace").split("\0") if x]

    def snapshot_repo(self) -> None:
        try:
            for rel in self.changed_files() or []:
                try:
                    with open(os.path.join(self.repo, rel), "rb") as fh:
                        self.repo_baseline[rel] = fh.read()
                except OSError:
                    self.repo_baseline[rel] = None
        except Exception:
            pass


    def ask_layout(self):
        prompt = ("Read the task statement below and answer with exactly two lines:\n"
                  "REPO: <absolute path of the repository the task is about, or unknown>\n"
                  "TESTS: <directory in which the new test files must be added, or unknown>\n\n"
                  "Task statement:\n\n" + _clip(self.statement, 30000))
        self.llm.messages = [{"role": "system", "content": "You answer questions about task statements briefly."}]
        reply, _ = self.llm.ask(prompt, max_tokens=300)
        self.llm.messages = []
        repo = tests = None
        for line in (reply or "").split("\n"):
            mm = re.match(r"\W*(REPO|TESTS)\W*:\s*`?([^`\s]+)`?", line.strip())
            if mm and mm.group(2).lower().strip(".") != "unknown":
                if mm.group(1) == "REPO":
                    repo = mm.group(2).rstrip("/")
                else:
                    tests = mm.group(2)
        log("[SETUP] layout from the model: repo=%s tests=%s" % (repo, tests))
        return repo, tests

    def coverage_report(self, detail: bool = False, as_list: bool = False):
        items = []
        st = self.scope_text()
        files = list(dict.fromkeys(self.scope_files + sorted(self.cov_lines)))
        for rel in files:
            text, tree = _read_source(os.path.join(self.src_root, rel))
            if tree is None:
                continue
            covered = self.cov_lines.get(rel, set())
            in_scope = rel in self.scope_files
            if not covered and not in_scope:
                continue
            src_lines = text.split("\n")
            for qual, fn in _functions(tree):
                if any((_dotted(d) or "").endswith("overload") for d in fn.decorator_list):
                    continue
                spans = {n: (a, b) for n, a, b in _stmt_spans(fn.body)}
                stmts = sorted(spans)
                if not stmts:
                    continue
                missed = [n for n in stmts if not any(x in covered for x in range(spans[n][0], spans[n][1] + 1))]
                if not missed:
                    continue
                short = qual.split(".")[-1]
                if len(missed) == len(stmts):
                    named = not short.startswith("_") and re.search(r"\b%s\b" % re.escape(short), st)
                    owner = qual.split(".")[-2] if "." in qual else ""
                    operator = (short.startswith("__") and short.endswith("__") and short not in _SKIP_FUNCS
                                and owner and re.search(r"\b%s\b" % re.escape(owner), st))
                    if not (named or operator):
                        continue
                    items.append((0, "%s: %s (line %d) is never called" % (rel, qual, fn.lineno)))
                else:
                    text = "%s: %s: lines %s never run" % (rel, qual, _ranges(missed))
                    if detail and src_lines:
                        text += "\n" + "\n".join("    %5d  %s" % (n, src_lines[n - 1].rstrip())
                                                   for n in missed[:4] if n <= len(src_lines))
                    items.append((1, text))
        items.sort(key=lambda x: x[0])
        if as_list:
            return [t for _, t in items[:60]]
        return "\n".join(t for _, t in items[:60 if not detail else 30])

    def refresh_mutants(self) -> int:
        added = []
        for rel in sorted(self.cov_lines):
            path = os.path.join(self.src_root, rel)
            if not rel.endswith(".py") or not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as fh:
                    source = fh.read()
                muts = _generate_mutants(rel, source, self.cov_lines[rel],
                                         more=self.kinds > 1 and rel in self.scope_files)
            except Exception as e:
                log("[MUT] %s: %s" % (rel, _fmt_exc(e)))
                continue
            for m in muts:
                k = (m["file"], m["start"], m["end"], m["repl"])
                if k not in self.mutant_keys:
                    self.mutant_keys.add(k)
                    added.append(m)
        groups = {}
        for m in added:
            groups.setdefault((m["file"], m["func"]), []).append(m)
        n = len(self.mutants)
        fresh = []
        while groups:
            for g in list(groups):
                m = groups[g].pop(0)
                n += 1
                m["id"] = "m%d" % n
                self.mutants[m["id"]] = m
                fresh.append(m)
                if not groups[g]:
                    del groups[g]
        if fresh and self.pool_threads:
            self.pool_submit(fresh, foreground=False)
        return len(added)

    def relevant_keys(self, m: dict) -> list:
        keys = set()
        line_cases = self.line_cases
        for line in range(m["stmt_start"], m["stmt_end"] + 1):
            keys |= line_cases.get((m["file"], line), set())
        if not keys and m.get("scope_lines"):
            for line in m["scope_lines"]:
                keys |= line_cases.get((m["file"], line), set())
        if not keys and m.get("module_level"):
            keys = {k for (f, _), ks in list(line_cases.items()) if f == m["file"] for k in ks}
        return sorted(k for k in keys if k in self.records and k not in self.excluded)

    def ensure_workers(self) -> None:
        n_workers = max(1, min(CPU_WORKERS_CAP, _available_cpus() + 1))
        while len(self.worker_roots) < n_workers:
            root = os.path.join(self.work, "mut", "w%d" % len(self.worker_roots))
            shutil.copytree(self.src_root, root)
            _make_readable(root)
            self.worker_roots.append(root)

    def check_mutant(self, m: dict, root: str, full: bool) -> str:
        skip = set()
        for attempt in range(4):
            try:
                keys = [k for k in self.relevant_keys(m) if k not in self.unreliable and k not in skip]
                if not keys:
                    return "no-case" if attempt == 0 else "survived"
                cap = CHECK_CASES_CAP_FAST if (root in self.servers and m.get("fast", True)) else CHECK_CASES_CAP
                if not full and len(keys) > cap:
                    keys = sorted(keys, key=lambda k: (len(self.records[k].get("lines") or ()),
                                                       self.records[k].get("secs", 0.01)))[:cap]
                keys.sort(key=self.suite_position)
            except Exception:
                return "error"
            verdict, first = self.run_check(m, root, keys)
            if verdict != "killed" or first is None:
                return verdict
            if not self.alone_reproduces(first, root):
                self.unreliable.add(first)
                continue
            if first == keys[0]:
                m["killer"] = first
                return "killed"
            why = m.get("why", "")
            if self.run_check(m, root, [first])[0] == "killed":
                m["why"] = why
                m["killer"] = first
                return "killed"
            skip.add(first)
            full = True
        m["why"] = "detected only together with other cases"
        return "survived"

    def suite_position(self, key: str) -> tuple:
        module, case = key.split("::")
        order = self.case_order.get(module) or []
        return (self.test_module(module), order.index(case) if case in order else len(order))

    def alone_reproduces(self, key: str, root: str) -> bool:
        if key not in self.alone_ok:
            verdict, _ = self.run_check(None, root, [key])
            self.alone_ok[key] = verdict != "killed"
        return self.alone_ok[key]

    def run_check(self, m, root: str, keys: list) -> tuple:
        server = self.servers.get(root)
        if server is not None and (m is None or m.get("fast", True)):
            t0 = time.time()
            fast = self.run_check_fast(m, server, keys)
            self.count("fast_secs", time.time() - t0)
            if fast is not None:
                if FAST_VERIFY and m is not None:
                    slow = self.run_check_slow(m, root, keys)
                    if (slow[0] == "killed") != (fast[0] == "killed"):
                        self.count("mismatch")
                        log("[FAST] verdicts differ for %s %s:%d (%s): fast=%s slow=%s %s" % (
                            m["id"], m["file"], m["line"], m["kind"], fast[0], slow[0], m["show"][:160]))
                return fast
        t0 = time.time()
        try:
            return self.run_check_slow(m, root, keys)
        finally:
            self.count("slow_secs", time.time() - t0)

    def count(self, what: str, amount: float = 1) -> None:
        with self.counter_lock:
            self.fast_stats[what] = round(self.fast_stats.get(what, 0) + amount, 1)

    def check_payload(self, keys: list):
        expected, back = {}, {}
        for k in keys:
            r = self.records[k]
            cls = r.get("cls") if self.pins_classes(k.split("::")[0]) else None
            expected[self.test_key(k)] = {"status": r["status"], "src": r.get("src"), "cls": cls}
            back[self.test_key(k)] = k
        secs = sum(self.records[k].get("secs", 0.01) for k in keys)
        longest = max(self.records[k].get("secs", 0.01) for k in keys)
        return expected, back, secs, longest

    def run_check_fast(self, m, server, keys: list):
        try:
            expected, back, secs, longest = self.check_payload(keys)
        except Exception:
            return None
        req = {"op": "check", "keys": list(expected), "expected": expected, "stop_on_first": True}
        if m is not None:
            req["mut"] = {"file": m["file"], "start": m["start"], "end": m["end"], "line": m["line"],
                          "repl": m["repl"].decode("utf-8", errors="replace"), "default": m.get("default")}
        recs = None
        for factor in (1, 3):
            req["timeout"] = factor * max(1.0, 20 * longest)
            req["deadline"] = factor * (10 + 3 * secs)
            recs = server.request(req, timeout=req["deadline"] + 20)
            if recs is None:
                self.count("server_failed")
                return None
            if any(r.get("kind") == "unsupported" for r in recs):
                if m is not None:
                    m["fast"] = False
                    m["fast_why"] = next(r.get("why") for r in recs if r.get("kind") == "unsupported")
                self.count("unsupported")
                return None
            timed_out = any(r.get("kind") == "runner_timeout" or
                            (r.get("kind") == "mismatch" and r.get("status") == "timeout") for r in recs)
            real = any(r.get("kind") == "mismatch" and r.get("status") not in ("timeout", "missing") for r in recs)
            if not timed_out or real:
                break
        if any(r.get("kind") == "runner_crash" for r in recs):
            self.count("server_crash")
            return None
        self.count("fast")
        verdict = self.verdict_from(m, recs, back)
        if verdict[0] != "killed" and any(r.get("kind") == "approx" for r in recs):
            self.count("approx")
            if APPROX_CONFIRM:
                return None
        return verdict

    def verdict_from(self, m, recs: list, back: dict) -> tuple:
        first = next((back.get(r.get("key")) for r in recs
                      if r.get("kind") == "mismatch" and r.get("status") != "missing"), None)
        why = next((("%s:%s:%s" % (r.get("kind"), r.get("status"), r.get("exc") or "")) for r in recs
                    if r.get("kind") in ("mismatch", "module_error", "runner_timeout", "runner_crash")), "")
        if m:
            m["why"] = why
        if any(r.get("kind") == "library_error" or (r.get("kind") == "module_error" and r.get("cause") == "library")
               for r in recs):
            return "error", None
        bad = next((r for r in recs if r.get("kind") == "module_error"), None)
        if bad is not None:
            if m:
                m["why"] = "import:%s:%s" % (bad.get("module"), (bad.get("error") or "")[:160])
                self.import_fragile.setdefault(bad.get("module"), "%s, %s" % (
                    m["show"].strip().split("\n")[-1].strip(), (bad.get("error") or "")[:160]))
            return "survived", None
        if any(r.get("kind") == "mismatch" and r.get("status") == "missing" for r in recs):
            return "error", None
        kinds = {r.get("kind") for r in recs}
        if kinds & {"mismatch", "module_error", "runner_timeout"}:
            return "killed", first
        if "runner_crash" in kinds:
            return "error", None
        return "survived", None

    def run_check_slow(self, m, root: str, keys: list) -> tuple:
        self.count("slow")
        try:
            expected, back = {}, {}
            for k in keys:
                r = self.records[k]
                cls = r.get("cls") if self.pins_classes(k.split("::")[0]) else None
                expected[self.test_key(k)] = {"status": r["status"], "src": r.get("src"), "cls": cls}
                back[self.test_key(k)] = k
            secs = sum(self.records[k].get("secs", 0.01) for k in keys)
            longest = max(self.records[k].get("secs", 0.01) for k in keys)
        except Exception:
            return "error", None
        path = os.path.join(root, m["file"]) if m else None
        src = None
        if m:
            with open(os.path.join(self.src_root, m["file"]), "rb") as fh:
                src = fh.read()
        try:
            if m:
                with open(path, "wb") as fh:
                    fh.write(src[:m["start"]] + m["repl"] + src[m["end"]:])
            tkeys = list(expected)
            recs = None
            for factor in (1, 3):
                cfg = {"mode": "check", "modules": sorted({k.split("::")[0] for k in tkeys}), "cases": tkeys,
                       "expected": expected, "stop_on_first": True, "timeout": factor * max(5.0, 10 * longest)}
                recs = self.runner(cfg, timeout=factor * (30 + 5 * secs),
                                   pythonpath=os.pathsep.join([root] + self.extra_path),
                                   sys_path=[root] + self.extra_path + [self.record_dir])
                timed_out = any(r.get("kind") == "runner_timeout" or
                                (r.get("kind") == "mismatch" and r.get("status") == "timeout") for r in recs)
                real = any(r.get("kind") == "mismatch" and r.get("status") not in ("timeout", "missing") or
                           r.get("kind") == "module_error" for r in recs)
                if not timed_out or real:
                    break
        finally:
            if m:
                with open(path, "wb") as fh:
                    fh.write(src)
        return self.verdict_from(m, recs, back)

    def start_pool(self) -> None:
        if self.pool_threads:
            return
        self.ensure_workers()
        for root in self.worker_roots:
            if root not in self.servers:
                self.servers[root] = _MutServer(self, root)
            t = threading.Thread(target=self._pool_worker, args=(root,), daemon=True)
            t.start()
            self.pool_threads.append(t)
        self.pool_submit(list(self.mutants.values()), foreground=False)

    def stop_pool(self) -> None:
        with self.pool_cv:
            self.pool_stop = True
            self.pool_cv.notify_all()
            end = time.time() + 20
            while self.pool_busy and time.time() < end:
                self.pool_cv.wait(0.5)
        for server in self.servers.values():
            server.stop()
        if self.fast_stats:
            log("[MUT] checks: %s" % self.fast_stats)

    def pool_submit(self, mutants: list, foreground: bool, full: bool = False):
        import heapq
        waiter = {"left": len(mutants)} if foreground else None
        with self.pool_cv:
            if not foreground:
                # a change already waiting in the background queue or being checked right now gets no second entry
                busy = {e[2] for e in self.pool_queue if e[4] is None} | set(self.pool_running)
                mutants = [m for m in mutants if m["id"] not in busy]
            for m in mutants:
                self.pool_seq += 1
                prio = (0, self.pool_seq) if foreground else (1,) + self.mutant_priority(m)
                heapq.heappush(self.pool_queue, (prio, self.pool_seq, m["id"], full, waiter))
            self.pool_cv.notify_all()
        return waiter

    def pool_wait(self, waiter, timeout: float) -> None:
        end = time.time() + timeout
        with self.pool_cv:
            while waiter["left"] > 0 and time.time() < end and not self.pool_stop:
                self.pool_cv.wait(min(1.0, max(0.05, end - time.time())))

    def pool_pending(self) -> int:
        with self.pool_cv:
            return len(self.pool_queue)

    def pause_pool(self, paused: bool) -> None:
        with self.pool_cv:
            self.pool_paused = paused
            self.pool_cv.notify_all()
            if paused:
                end = time.time() + 30
                while self.pool_busy and time.time() < end:
                    self.pool_cv.wait(0.5)

    def _pool_worker(self, root: str) -> None:
        import heapq
        server = self.servers.get(root)
        while True:
            with self.pool_cv:
                while (not self.pool_queue or self.pool_paused) and not self.pool_stop:
                    self.pool_cv.wait(1.0)
                if self.pool_stop:
                    return
                prio, _, mid, full, waiter = heapq.heappop(self.pool_queue)
                skip = waiter is None and mid in self.mutant_status
                if not skip:
                    self.pool_busy += 1
                    self.pool_running[mid] = self.pool_running.get(mid, 0) + 1
            status = None
            if not skip and self.left() > 20:
                try:
                    if server is not None:
                        server.ensure()
                    status = self.check_mutant(self.mutants[mid], root, full)
                except Exception:
                    log("[MUT] check failed: %s" % traceback.format_exc()[-400:])
                    status = "error"
            with self.pool_cv:
                if not skip:
                    self.pool_busy -= 1
                    if self.pool_running.get(mid, 0) <= 1:
                        self.pool_running.pop(mid, None)
                    else:
                        self.pool_running[mid] -= 1
                if status and status != "error":
                    current = self.mutant_status.get(mid)
                    if status == "killed" or current is None or full or not self.mutant_full.get(mid):
                        self.mutant_status[mid] = status
                        self.mutant_full[mid] = full
                if waiter is not None:
                    waiter["left"] -= 1
                self.pool_cv.notify_all()

    def evaluate_now(self, mutants: list, timeout: float, full: bool = True) -> None:
        if not mutants:
            return
        if not self.pool_threads:
            self.start_pool()
        for m in mutants:
            self.mutant_status.pop(m["id"], None)
            self.mutant_full.pop(m["id"], None)
        self.pool_wait(self.pool_submit(mutants, foreground=True, full=full), timeout)

    def mutation_stats(self) -> dict:
        stats = {}
        for st in list(self.mutant_status.values()):
            stats[st] = stats.get(st, 0) + 1
        return stats

    def drop_import_fragile(self) -> None:
        for test_mod, change in list(self.import_fragile.items()):
            if test_mod in self.import_fragile_done:
                continue
            self.import_fragile_done.add(test_mod)
            module = next((n for n in self.case_sources if self.test_module(n) == test_mod), None)
            if module is None or module in self.module_problems:
                continue
            self.module_problems[module] = ["runs library code when the file is imported"]
            for key in self.usable_keys(module):
                self.excluded[key] = "its file runs library code when imported"
            self.notes.append(
                "%s.py was removed from the suite: it runs library code when the file is imported, and under a small "
                "change of the library (%s) the file could not be imported at all, which breaks the whole test run. "
                "Write its cases again in a new file; create library objects, and classes built on library classes, "
                "inside helper functions that the cases call." % (module, change))
            self.note_validation_failure(module, module + ".py", change,
                                         cause="during module import in local mutation replay")
            log("[MUT] %s.py dropped: fails to import under a change (%s)" % (module, change[:120]))
            self.rebuild_coverage()

    def mutant_priority(self, m: dict) -> tuple:
        words = self.scope_words()
        parts = [p for p in m["func"].split(".") if p and p != "<module>"]
        named = any(p in words for p in parts)
        return (self.set_aside(m), not named, m["file"] not in self.scope_files, m["module_level"], int(m["id"][1:]))

    def set_aside(self, m: dict) -> bool:
        key = (m["file"], m["func"])
        stats = self.func_feedback.get(key)
        return bool(stats) and stats[0] >= 3 and stats[1] == 0

    def note_feedback(self, batch: list) -> None:
        for m in batch:
            key = (m["file"], m["func"])
            stats = self.func_feedback.setdefault(key, [0, 0])
            if m["id"] in self.skipped:
                stats[0] += 1
            elif self.mutant_status.get(m["id"]) == "killed":
                stats[1] += 1

    def scope_words(self) -> set:
        if not hasattr(self, "_scope_words"):
            self._scope_words = set(re.findall(r"[A-Za-z_]\w+", self.scope_text()))
        return self._scope_words

    def function_source(self, m: dict, limit: int = 60) -> str:
        try:
            with open(os.path.join(self.src_root, m["file"]), errors="replace") as fh:
                lines = fh.read().split("\n")
        except OSError:
            return ""
        a, b = m.get("func_span") or (m["line"], m["line"])
        if m.get("module_level") or b - a < 3:
            a, b = max(1, m["line"] - 6), min(len(lines), m["line"] + 6)
        if b - a + 1 > limit:
            a = max(a, m["line"] - limit // 2)
            b = min(b, a + limit - 1)
        return "\n".join("%5d%s %s" % (i, ">" if i == m["line"] else " ", lines[i - 1]) for i in range(a, b + 1))

    def scope_items(self) -> list:
        lines = self.statement.split("\n")
        start = next((i + 1 for i, line in enumerate(lines) if re.match(r"\s*#+\s*(?:the\s+)?scope\b", line, re.I)),
                     None)
        if start is None:
            return []
        items, cur = [], None
        for line in lines[start:]:
            if re.match(r"\s*#+\s", line):
                break
            mm = re.match(r" {0,3}[-*]\s+(.*)", line)
            if mm:
                if cur:
                    items.append(cur)
                cur = mm.group(1).strip()
            elif cur is not None and line.strip() and line[:1] in (" ", "\t"):
                cur += " " + line.strip()
            elif cur is not None:
                items.append(cur)
                cur = None
        if cur:
            items.append(cur)
        return [x for x in items if len(x) > 3]

    def make_shares(self, n: int) -> list:
        items = self.scope_items()
        if len(items) >= n:
            total = float(sum(len(x) for x in items))
            groups, cur, acc = [], [], 0.0
            for i, x in enumerate(items):
                cur.append(x)
                acc += len(x)
                groups_after = n - len(groups) - 1
                items_after = len(items) - i - 1
                if groups_after > 0 and (items_after == groups_after or
                                         (acc >= total * (len(groups) + 1) / n and items_after >= groups_after)):
                    groups.append(cur)
                    cur = []
            if cur:
                groups.append(cur)
            return ["\n".join("- " + x for x in g) for g in groups]
        shares = ["- " + x for x in items]
        lenses = SHARE_LENSES if items else [SHARE_LENSES[3], SHARE_LENSES[2], SHARE_LENSES[0], SHARE_LENSES[1]]
        for lens in lenses:
            if len(shares) >= n:
                break
            shares.append(lens)
        return shares[:n]

    def claim_name(self, name: str, writer: int) -> str:
        stem = name[:-3]
        owner = self.case_owner.get(stem)
        if stem not in self.case_sources or owner is None or owner == writer:
            return name
        k = 2
        while True:
            cand = "%s_w%d%s" % (stem, writer + 1, "" if k == 2 else "_%d" % k)
            if cand not in self.case_sources or self.case_owner.get(cand) == writer:
                return cand + ".py"
            k += 1

    def busy_writers(self) -> set:
        """Writers whose reply from an earlier exchange is still on its way."""
        return {w for w, job in self.inflight.items() if job["thread"].is_alive()}

    def write_round(self, jobs: list, label: str, effort: str = "", until: float = 0.0) -> None:
        """Ask the writers in parallel and take in their case files.

        A slow writer does not hold up the others: once enough of them have answered (or the round has run
        long), the round goes on with the replies it has. The slow writer's call keeps running, it gets no new
        job meanwhile, and its reply is taken in by a later round. With no jobs, the round only waits for such
        a reply: until one arrives, or until `until` seconds have passed."""
        t0 = time.time()
        self.llm.fail_since = None  # time between rounds, when no call runs, is no failure streak
        jobs = [job if len(job) == 3 else (job[0], job[1], "") for job in jobs]
        busy = self.busy_writers()
        jobs = [job for job in jobs if job[0] not in busy]

        def work(w, text, job_effort, slot):
            try:
                _transcript("user", "[writer %d, %s]\n%s" % (w + 1, slot["label"], text))
                slot["result"] = self.llm.ask(text, conv=self.writers[w], effort=job_effort or effort)
                _transcript("assistant", "[writer %d, %s]\n%s" % (w + 1, slot["label"], slot["result"][0]))
            except Exception:
                log("[LLM] writer %d failed: %s" % (w + 1, traceback.format_exc()[-300:]))

        for w, text, job_effort in jobs:
            slot = {"label": label, "result": (None, None), "t0": t0}
            slot["thread"] = threading.Thread(target=work, args=(w, text, job_effort, slot), daemon=True)
            self.inflight[w] = slot
            slot["thread"].start()
        n = len(jobs)
        mine = [w for w, _, _ in jobs]
        quorum = -(-n // 2)
        hard = self.wall * (DRAFT_WAIT_FRAC if label == "draft" else ROUND_WAIT_FRAC)
        t_quorum = None
        while True:
            pending = [w for w in self.inflight if self.inflight[w]["thread"].is_alive()]
            answered = [w for w in mine if w not in pending and self.inflight[w]["result"][0]]
            if self.left() <= 5 or (mine and not any(w in pending for w in mine)):
                break
            elapsed = time.time() - t0
            if not mine and (len(pending) < len(self.inflight) or elapsed >= until):
                break
            if mine and t_quorum is None and len(answered) >= quorum:
                t_quorum = elapsed
            if answered and elapsed >= hard:
                break
            if t_quorum is not None and elapsed >= t_quorum + max(STRAGGLER_GRACE, 0.5 * t_quorum):
                break
            time.sleep(0.5)
        done = [(w, self.inflight.pop(w)) for w in sorted(self.inflight) if not self.inflight[w]["thread"].is_alive()]
        late = [w for w, slot in done if slot["label"] != label]
        waiting = sorted(w + 1 for w in self.inflight)
        log("[WRITE] %s: %d writers answered in %.0fs%s%s" % (
            label, sum(1 for w, slot in done if slot["label"] == label and slot["result"][0]), time.time() - t0,
            "; late replies from writers %s" % [w + 1 for w in late] if late else "",
            "; still waiting for writers %s" % waiting if waiting else ""))
        replies = [(w, slot["label"], slot["result"]) for w, slot in done]
        written, files_of = [], {}
        for w, reply_label, (reply, finish) in replies:
            notes = []
            if not reply:
                self.writer_notes[w] = notes + list(self.validation_pending.get(w) or [])
                continue
            self.validation_pending.pop(w, None)
            blocks, _done, truncated = _parse_reply(reply, finish)
            if any(kind in ("case", "case-unnamed") for kind, _, _ in blocks):
                self.writer_empty.discard(w)
            elif reply_label == "draft":
                self.writer_empty.add(w)
                notes.append("Your reply contained no case files, only text, so nothing of your share has been "
                             "saved yet. Write the case files now as ```python cases_<topic>.py blocks.")
            for kind, name, body in blocks:
                if kind == "bash":
                    notes.append("(Shell commands are not run here; everything needed is in the task context.)")
                    continue
                if kind == "case-unnamed":
                    k = 1
                    while "cases_unnamed_%d" % k in self.case_sources:
                        k += 1
                    name = "cases_unnamed_%d.py" % k
                name = self.claim_name(name, w)
                kept = self.keep_previous_cases(name[:-3], body)
                if kept:
                    self.case_owner[kept[0]] = w
                    written.append(kept[0])
                    files_of.setdefault(w, []).append(kept[0])
                err = self.add_case_file(name, body)
                if err:
                    notes.append(err)
                    continue
                self.case_owner[name[:-3]] = w
                written.append(name[:-3])
                files_of.setdefault(w, []).append(name[:-3])
            for line in re.findall(r"(?im)^\W*SKIP\W*:?(.*)$", reply):
                for mid in re.findall(r"\bm\d+\b", line):
                    if w in self.shown_to.get(mid, ()):
                        self.skipped.add(mid)
            if truncated:
                notes.append("Your last reply was cut off; its unfinished file was ignored. Write smaller files.")
            self.writer_notes[w] = notes
        if not written:
            return
        self.pause_pool(True)
        try:
            report = self.record_modules(sorted(set(written)))
        finally:
            self.pause_pool(False)
        for name, r in sorted(report.items()):
            log("[REC] %s (writer %s): %d cases, %d values, %d raise, %d problems%s" % (
                name, (self.case_owner.get(name, -1) + 1), r["cases"], r["values"], len(r["raises"]),
                len(r["problems"]), ", MODULE UNUSABLE" if r["module"] else ""))
        self.writer_losses = {}
        for w, names in files_of.items():
            mine = {n: report[n] for n in set(names) if n in report}
            if mine:
                self.writer_notes.setdefault(w, []).append(self.format_report(mine))
                self.writer_losses[w] = (sum(1 for r in mine.values() if r["module"]),
                                         sum(len(r["problems"]) for r in mine.values()))
        log("[WRITE] %s: %d usable cases in the suite, $%.4f spent" % (
            label, sum(1 for k in self.records if k not in self.excluded), self.llm.spent))

    def pins_any_classes(self) -> bool:
        return any(self.pins_classes(n) for n in self.case_sources if n not in self.module_problems)

    def needs_fixing(self, w: int) -> bool:
        unusable, dropped = self.writer_losses.get(w, (0, 0))
        return unusable > 0 or dropped >= 10 or w in self.writer_empty

    def votes(self, m: dict) -> dict:
        votes = {}
        for k in self.relevant_keys(m):
            w = self.case_owner.get(k.split("::")[0])
            if w is not None:
                votes[w] = votes.get(w, 0) + 1
        return votes

    def mutant_item(self, m: dict, with_code: bool) -> str:
        item = "[%s] %s:%d, in %s\n%s" % (m["id"], m["file"], m["line"], m["func"], m["show"])
        if with_code:
            item += "\n  code:\n" + self.function_source(m)
        return item

    def usage_note(self, rel: str, line: int) -> str:
        tree = _read_source(os.path.join(self.src_root, rel))[1]
        if tree is None:
            return ""
        names = set()
        for st in tree.body:
            if st.lineno <= line <= (st.end_lineno or st.lineno) and isinstance(st, (ast.Assign, ast.AnnAssign)):
                for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                    if isinstance(t, ast.Name):
                        names.add(t.id)
        if not names:
            return ""
        uses, readers = [], set()
        for frel in self.package_files():
            ftree = _read_source(os.path.join(self.src_root, frel))[1]
            if ftree is None:
                continue
            spans = [(q, f.lineno, f.end_lineno or f.lineno) for q, f in _functions(ftree)]
            for node in ast.walk(ftree):
                hit = (isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Load)) or \
                      (isinstance(node, ast.Attribute) and node.attr in names)
                if not hit:
                    continue
                owner = max((sp for sp in spans if sp[1] <= node.lineno <= sp[2]), key=lambda sp: sp[1], default=None)
                where = owner[0] if owner else "module level"
                uses.append("%s:%d (in %s)" % (frel, node.lineno, where))
                if owner:
                    readers.add(owner[0].split(".")[-1])
        if not uses:
            return ""
        callers = []
        for frel in self.package_files():
            ftree = _read_source(os.path.join(self.src_root, frel))[1]
            if ftree is None:
                continue
            spans = [(q, f.lineno, f.end_lineno or f.lineno) for q, f in _functions(ftree)]
            for node in ast.walk(ftree):
                if isinstance(node, ast.Call):
                    f = node.func
                    called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if called in readers:
                        owner = max((sp for sp in spans if sp[1] <= node.lineno <= sp[2]), key=lambda sp: sp[1],
                                    default=None)
                        callers.append("%s:%d (in %s, calls %s)" % (frel, node.lineno, owner[0] if owner else
                                                                      "module level", called))
        text = "  %s is read at: %s" % (", ".join(sorted(names)), ", ".join(dict.fromkeys(uses[:8])))
        if callers:
            text += "\n  those functions are called at: " + ", ".join(dict.fromkeys(callers[:8]))
        return text

    def group_item(self, members: list, with_code: bool) -> str:
        m0 = members[0]
        try:
            with open(os.path.join(self.src_root, m0["file"]), errors="replace") as fh:
                src = fh.read().split("\n")
        except OSError:
            src = []
        by_line = {}
        for m in members:
            by_line.setdefault(m["line"], []).append(m)
        bullets = []
        for line in sorted(by_line):
            was = src[line - 1].strip() if 0 < line <= len(src) else "?"
            variants = []
            for m in by_line[line]:
                now = [x.strip()[4:].strip() for x in m["show"].split("\n") if x.strip().startswith("now:")]
                variants.append("[%s] %s" % (m["id"], (now[0] if now else "?")[:120]))
            bullets.append("  - line %d: %s\n      changed to: %s" % (line, was[:140], "; ".join(variants)))
        text = ("%s:%d-%d, data at module level that the code looks up. No case notices any of the changes below. "
                "Write one case per bullet whose result depends on exactly that entry: inputs that make the code "
                "read that entry (where it is read is listed below) and show its value in the result:\n%s" % (
                    m0["file"], m0["stmt_start"], m0["stmt_end"], "\n".join(bullets)))
        note = self.usage_note(m0["file"], m0["stmt_start"])
        if note:
            text += "\n" + note
        if with_code:
            a, b = m0["stmt_start"], min(m0["stmt_end"], m0["stmt_start"] + 39)
            if src:
                text += "\n  code:\n" + "\n".join("%5d  %s" % (i, src[i - 1]) for i in range(a, b + 1) if i <= len(src))
        return text

    def round_jobs(self, rnd: int) -> list:
        n = len(self.writers)
        free = [w for w in range(n) if w not in self.busy_writers()]
        if not free:
            return []
        self.drop_import_fragile()
        pins = self.pins_any_classes()
        alive = [m for m in self.mutants.values() if self.mutant_status.get(m["id"]) == "survived"
                 and not (m["kind"] == "exception" and not pins)
                 and (self.shown_count.get(m["id"], 0) == 0
                      or (self.shown_count.get(m["id"], 0) == 1 and m["id"] not in self.second_looked))]
        def tier(m):
            shown = self.shown_count.get(m["id"], 0)
            if shown and m["id"] not in self.skipped:
                return 0
            if not shown:
                return 1 if m["file"] in self.scope_files else 3
            return 2
        alive.sort(key=lambda m: (tier(m),) + self.mutant_priority(m))
        def members(u):
            return u if isinstance(u, list) else [u]

        units, groups = [], {}
        for m in alive:
            if m["module_level"]:
                key = (m["file"], m["stmt_start"])
                if key not in groups:
                    groups[key] = []
                    units.append(groups[key])
                if len(groups[key]) < 24:
                    groups[key].append(m)
            else:
                units.append(m)

        capacity = MUTANTS_PER_WRITER * len(free)
        per_func, picked, taken = {}, [], set()
        tiers = {}
        for u in units:
            tiers.setdefault(min(tier(m) for m in members(u)), []).append(u)
        for t in sorted(tiers):
            for spread in ((False,) if t == 0 else (True, False)):
                for u in tiers[t]:
                    if len(picked) >= capacity:
                        break
                    if id(u) in taken:
                        continue
                    if isinstance(u, dict):
                        k = (u["file"], u["func"])
                        if spread and per_func.get(k, 0) >= 2:
                            continue
                        per_func[k] = per_func.get(k, 0) + 1
                    picked.append(u)
                    taken.add(id(u))
        votes = []
        for u in picked:
            v = {}
            for m in members(u):
                for w, k in self.votes(m).items():
                    v[w] = v.get(w, 0) + k
            votes.append(v)
        need = -(-len(picked) // MUTANTS_PER_WRITER)
        totals = {w: sum(v.get(w, 0) for v in votes) for w in free}
        chosen = sorted(free, key=lambda w: (-totals[w], w))[:need]
        order = sorted(zip(picked, votes), key=lambda uv: not all(
            self.shown_count.get(m["id"], 0) for m in members(uv[0])))
        def assign(pool):
            load = [0] * n
            batches = {w: [] for w in range(n)}
            repeated = 0
            for u, v in order:
                seen = set()
                for m in members(u):
                    seen |= self.shown_to.get(m["id"], set())
                for w in sorted(pool, key=lambda w: (w in seen, -v.get(w, 0), load[w], w)):
                    if load[w] < MUTANTS_PER_WRITER:
                        batches[w].append(u)
                        load[w] += 1
                        repeated += sum(w in self.shown_to.get(m["id"], set())
                                        for m in members(u) if self.shown_count.get(m["id"], 0))
                        break
            return repeated, batches

        repeated, batches = assign(chosen)
        if repeated and n <= 8:
            import itertools
            fixing = {w for w in free if self.needs_fixing(w)}
            calls = len(set(chosen) | fixing)
            for pool in itertools.combinations(free, need):
                if len(set(pool) | fixing) != calls:
                    continue
                count, trial = assign(pool)
                if count < repeated:
                    repeated, batches, chosen = count, trial, list(pool)
                    if not repeated:
                        break
        cov = {w: [] for w in range(n)}
        if True:
            items = [x for x in self.coverage_report(detail=True, as_list=True)
                     if self.cov_shown.get(x.split("\n")[0], 0) < 2]
            for i, item in enumerate(items):
                self.cov_shown[item.split("\n")[0]] = self.cov_shown.get(item.split("\n")[0], 0) + 1
                fn = re.search(r": ([\w.<>]+)(?: \(line \d+\) is never called|: lines)", item)
                short = fn.group(1).split(".")[-1] if fn else ""
                owner = next((w for w in range(n) if short and re.search(r"\b%s\b" % re.escape(short), self.shares[w])),
                             None)
                pool = chosen or free[:1]
                if owner not in pool:
                    owner = pool[i % len(pool)]
                cov[owner].append(item)
        jobs = []
        shown_total = 0
        for w in free:
            batch = batches[w]
            if not batch and not cov[w] and not self.needs_fixing(w):
                continue
            parts = [ROUND_HEAD] + list(self.writer_notes.get(w) or [])
            if cov[w]:
                parts.append("Code in the scope that no case runs yet; the lines that never run are shown. Write "
                             "cases that reach them through the public API (an invalid argument or an unusual "
                             "option often leads there); ignore anything outside the task's scope:\n" +
                             "\n".join(cov[w]))
            again = [u for u in batch if all(self.shown_count.get(m["id"], 0) for m in members(u))]
            fresh = [u for u in batch if not any(u is x for x in again)]
            again.sort(key=lambda u: not isinstance(u, list))
            fresh.sort(key=lambda u: not isinstance(u, list))

            def render(u, second):
                if isinstance(u, list):
                    return self.group_item(u, True)
                return self.mutant_item(u, second or u["file"] not in self.context_files)
            if fresh:
                parts.append(MUTATION_INTRO)
                parts += [render(u, False) for u in fresh]
            if again:
                if not fresh:
                    parts.append(MUTATION_INTRO)
                parts.append(SECOND_LOOK)
                parts += [render(u, True) for u in again]
                for u in again:
                    ids = [m["id"] for m in members(u)]
                    self.second_looked.update(ids)
                    self.skipped.difference_update(ids)
            for u in batch:
                for m in members(u):
                    self.shown_count[m["id"]] = self.shown_count.get(m["id"], 0) + 1
                    self.shown.add(m["id"])
                    self.shown_to.setdefault(m["id"], set()).add(w)
                    shown_total += 1
            parts.append(ROUND_TAIL)
            jobs.append((w, "\n\n".join(parts), SECOND_LOOK_REASONING if again else ROUND_REASONING))

            def label(u):
                tag = "2nd:" if any(u is x for x in again) else ""
                if isinstance(u, list):
                    return "%stable[%s](%s:%d)" % (tag, ",".join(m["id"] for m in u), os.path.basename(u[0]["file"]),
                                                   u[0]["stmt_start"])
                return "%s%s(%s:%d %s)" % (tag, u["id"], os.path.basename(u["file"]), u["line"], u["kind"])
            log("[SHOW] round %d writer %d: %s%s" % (rnd, w + 1, " ".join(label(u) for u in batch),
                "; coverage: " + " | ".join(c.split("\n")[0][:90] for c in cov[w]) if cov[w] else ""))
        stats = self.mutation_stats()
        log("[ROUND] %d: %d writers, %d changes shown, %d coverage items; changes %s, skipped %d" % (
            rnd, len(jobs), shown_total, sum(len(c) for c in cov.values()), stats, len(self.skipped)))
        return jobs

    def more_kinds(self) -> bool:
        if self.kinds > 1 or self.frac() >= SECOND_KINDS_FRAC or not self.llm.affordable():
            return False
        self.kinds = 2
        n = self.refresh_mutants()
        log("[MUT] further kinds of changes: %d new" % n)
        if n:
            self.wait_pool(min(SWEEP_SECONDS, max(10.0, self.left() * 0.1)))
        return n > 0

    def wait_pool(self, timeout: float) -> None:
        end = time.time() + timeout
        with self.pool_cv:
            while (self.pool_queue or self.pool_busy) and time.time() < end and self.left() > 60:
                self.pool_cv.wait(min(1.0, max(0.05, end - time.time())))

    def execute(self) -> None:
        self.setup()
        if not self.packages:
            log("[RUN] no package found; nothing to test")
            return
        context = self.build_context()
        log("[RUN] context %d chars, scope files: %s" % (len(context), ", ".join(self.scope_files[:8])))
        self.shares = self.make_shares(max(1, WRITERS))
        n = len(self.shares)
        target = 100 if n >= 4 else 140 if n >= 2 else 220
        self.writers = [[{"role": "system", "content": SYSTEM_PROMPT}] for _ in range(n)]
        for w, share in enumerate(self.shares):
            log("[RUN] writer %d share: %s" % (w + 1, _clip(share.replace("\n", " | "), 160)))
        self.write_round([(w, context + "\n\n---\n\n" + AUTHOR_TASK.format(writers=n, share=self.shares[w],
                                                                          target=target))
                          for w in range(n)], "draft")
        self.revive_if_empty()
        self.checkpoint()
        self.snapshot("checkpoint")
        self.refresh_mutants()
        self.start_pool()
        t0 = time.time()
        self.wait_pool(SWEEP_SECONDS)
        known = self.mutation_stats().get("survived", 0)  # a snapshot: the pool keeps adding statuses
        if self.pool_pending() and known < MUTANTS_PER_WRITER * len(self.writers) // 2:
            self.wait_pool(SWEEP_SECONDS)
        log("[MUT] %d changes over %d files after %.0fs: %s, checks %s" % (
            len(self.mutants), len(self.cov_lines), time.time() - t0, self.mutation_stats(), self.fast_stats))
        for rnd in range(1, ROUNDS_MAX + 1):
            if self.frac() > ROUNDS_UNTIL or not self.llm.affordable():
                break
            counts_before = dict(self.shown_count)
            jobs = self.round_jobs(rnd)
            if not jobs and self.pool_pending() and self.frac() < 0.45:
                self.wait_pool(min(120.0, max(10.0, self.left() * 0.15)))
                jobs = self.round_jobs(rnd)
            if not jobs and self.more_kinds():
                jobs = self.round_jobs(rnd)
            if not jobs and self.busy_writers():
                self.write_round([], "round %d" % rnd, until=max(10.0, (ROUNDS_UNTIL - self.frac()) * self.wall))
                continue
            if not jobs:
                break
            this_round = [m for m in self.mutants.values()
                          if self.shown_count.get(m["id"], 0) > counts_before.get(m["id"], 0)]
            shown = [m for m in self.mutants.values() if m["id"] in self.shown
                     and self.mutant_status.get(m["id"]) == "survived"]
            self.write_round(jobs, "round %d" % rnd, ROUND_REASONING)
            self.snapshot("round")
            if rnd >= ROUNDS_MAX:
                break
            t0 = time.time()
            round_ids = {m["id"] for m in this_round}
            recheck = [m for m in shown if m["id"] not in self.skipped and
                       (m["id"] in round_ids or
                        (self.shown_count.get(m["id"], 0) == 1 and m["id"] not in self.second_looked))]
            self.evaluate_now(recheck, min(60.0, max(20.0, self.left() * 0.1)))
            unchecked = [m for m in self.mutants.values() if m["id"] not in self.mutant_status]
            if unchecked:
                self.pool_submit(unchecked, foreground=False)
            self.note_feedback(shown)
            gained = sum(1 for m in this_round if self.mutant_status.get(m["id"]) == "killed")
            log("[MUT] after round %d (%.0fs): %s; %d of the %d changes shown now detected" % (
                rnd, time.time() - t0, self.mutation_stats(), gained, len(this_round)))
            if rnd >= 2 and gained <= 2 and not self.more_kinds():
                break
        while self.busy_writers() and self.frac() < ROUNDS_UNTIL:
            # a reply that comes after its round still brings cases; wait for it while rounds could still run
            self.write_round([], "late replies", until=(ROUNDS_UNTIL - self.frac()) * self.wall)
        if self.inflight:
            self.write_round([], "late replies", until=0.0)
        alive = [m for m in self.mutants.values() if self.mutant_status.get(m["id"]) == "survived"]
        for m in sorted(alive, key=self.mutant_priority)[:150]:
            now = [x for x in m["show"].split("\n") if x.strip().startswith("now:")]
            log("[SURV] %s %s:%d %s %s shown=%d%s :: %s" % (
                m["id"], m["file"], m["line"], m["func"], m["kind"], self.shown_count.get(m["id"], 0),
                " skipped" if m["id"] in self.skipped else "", (now[0].strip() if now else "")[:120]))

    def snapshot(self, label: str) -> None:
        root = os.getenv("TG_SNAPSHOT_DIR")
        if not root:
            return
        try:
            files, _ = self.build_suite()
            self.snap_n = getattr(self, "snap_n", 0) + 1
            d = os.path.join(root, "%03d_%s_%ds_%.4f" % (self.snap_n, label, time.time() - self.t_start, self.llm.spent))
            os.makedirs(d, exist_ok=True)
            for rel, text in files.items():
                with open(os.path.join(d, rel), "w") as fh:
                    fh.write(text)
        except Exception:
            pass

    def dump_mutants(self) -> None:
        path = os.getenv("TG_DEBUG_MUTANTS")
        if not path:
            return
        try:
            rows = [{"id": m["id"], "file": m["file"], "line": m["line"], "func": m["func"], "kind": m["kind"],
                     "status": self.mutant_status.get(m["id"]), "shown": m["id"] in self.shown,
                     "shown_count": self.shown_count.get(m["id"], 0), "skipped": m["id"] in self.skipped,
                     "repl": m["repl"].decode("utf-8", "replace")[:120],
                     "cases": len(self.relevant_keys(m))} for m in self.mutants.values()]
            with open(path, "w") as fh:
                json.dump(rows, fh)
        except Exception:
            pass

    def usable_count(self) -> int:
        return sum(1 for k in self.records if k not in self.excluded)

    def revive_if_empty(self) -> None:
        """If optional packages being hidden left the suite empty, record again with them present."""
        if self.usable_count() or not self.absent_dir or not self.case_sources:
            return
        log("[EXTRAS] no recorded case held with optional packages hidden; recording as installed")
        self.absent_dir = None
        self.module_problems.clear()
        self.case_problems.clear()
        self.records.clear()
        self.excluded.clear()
        try:
            self.record_modules(sorted(self.case_sources))
        except Exception:
            log("[EXTRAS] re-record failed: %s" % traceback.format_exc()[-300:])
        log("[EXTRAS] after as-installed: %d usable cases" % self.usable_count())

    def checkpoint(self) -> None:
        if self.left() > 30:
            self.verify_suite(confirm_runs=1)
            log("[RUN] checkpoint: %d usable cases, $%.3f spent" % (
                self.usable_count(), self.llm.spent))

    def debug_dump(self) -> None:
        out = os.getenv("TG_DEBUG_DIR")
        if not out:
            return
        try:
            os.makedirs(os.path.join(out, "cases"), exist_ok=True)
            for name, src in self.case_sources.items():
                with open(os.path.join(out, "cases", name + ".py"), "w") as fh:
                    fh.write("# writer %s\n%s" % (self.case_owner.get(name, -1) + 1, src))
            with open(os.path.join(out, "problems.json"), "w") as fh:
                json.dump({"case": self.case_problems, "module": self.module_problems,
                           "excluded": self.excluded}, fh, indent=1)
            with open(os.path.join(out, "mutants.jsonl"), "w") as fh:
                for m in self.mutants.values():
                    fh.write(json.dumps({"id": m["id"], "file": m["file"], "line": m["line"], "func": m["func"],
                                         "kind": m["kind"], "status": self.mutant_status.get(m["id"]),
                                         "shown": self.shown_count.get(m["id"], 0), "skipped": m["id"] in self.skipped,
                                         "killer": m.get("killer"), "fast": m.get("fast", True),
                                         "why": m.get("fast_why"), "show": m["show"]}) + "\n")
            for w, conv in enumerate(self.writers):
                with open(os.path.join(out, "writer%d.json" % (w + 1)), "w") as fh:
                    json.dump(conv, fh, indent=1)
        except Exception:
            log("[DEBUG] dump failed: %s" % traceback.format_exc()[-300:])

    def contract_check(self) -> None:
        low = self.statement.lower()
        if not re.search(r"message text|error messages?|str\(\)` and `repr\(\)|repr\(\)", low):
            return
        root = os.path.join(self.work, "variant")
        if not os.path.isdir(root):
            shutil.copytree(self.src_root, root)
            changed = 0
            for dirpath, _, fs in os.walk(root):
                for f in fs:
                    if not f.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, f)
                    try:
                        with open(path, "rb") as fh:
                            new = _contract_variant(fh.read())
                    except Exception:
                        new = None
                    if new is not None:
                        with open(path, "wb") as fh:
                            fh.write(new)
                        changed += 1
            _make_readable(root)
            log("[CONTRACT] variant with reworded messages/repr: %d files changed" % changed)
        files, idmap = self.build_suite()
        if not files or self.end_left() < 60:
            return
        res = self.ci_run(files, src_root=root)
        failed = [tid for tid, t in res["tests"].items() if t["outcome"] != "passed"]
        if res["rc"] not in (0, 1) or not res["tests"]:
            log("[CONTRACT] variant run unusable (rc=%s); skipped" % res["rc"])
            return
        for tid in failed:
            key = idmap.get(tid)
            if key:
                self.excluded[key] = "depends on error message text or repr()"
        if failed:
            self.rebuild_coverage()
        log("[CONTRACT] %d tests depend on message text or repr(); dropped" % len(failed))

    def rewrite_check(self) -> None:
        root = os.path.join(self.work, "variant_rw")
        if not os.path.isdir(root):
            shutil.copytree(self.src_root, root)
            changed = 0
            for dirpath, _, fs in os.walk(root):
                for f in fs:
                    if not f.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, f)
                    try:
                        with open(path, "rb") as fh:
                            new = _rewrite_variant(fh.read())
                    except Exception:
                        new = None
                    if new is not None:
                        with open(path, "wb") as fh:
                            fh.write(new)
                        changed += 1
            _make_readable(root)
            log("[REWRITE] refactored copy: %d files changed" % changed)
        files, idmap = self.build_suite()
        if not files or self.end_left() < 60:
            return
        res = self.ci_run(files, src_root=root)
        if res["rc"] not in (0, 1) or not res["tests"]:
            log("[REWRITE] run on the refactored copy unusable (rc=%s); skipped" % res["rc"])
            return
        failed = [tid for tid, t in res["tests"].items() if t["outcome"] != "passed"]
        if len(failed) > max(3, int(0.02 * len(res["tests"]))):
            log("[REWRITE] %d of %d tests fail on the refactored copy; the copy is suspect, nothing dropped" % (
                len(failed), len(res["tests"])))
            return
        for tid in failed:
            key = idmap.get(tid)
            if key:
                self.excluded[key] = "fails on a refactored copy of the library"
        if failed:
            self.rebuild_coverage()
        log("[REWRITE] %d tests fail on the refactored copy; dropped %s" % (len(failed), failed[:5]))

    def finalize(self) -> str:
        self.stop_pool()
        self.revive_if_empty()
        self.drop_import_fragile()
        self.debug_dump()
        self.dump_mutants()
        files = {}
        try:
            if self.end_left() > 90:
                self.contract_check()
        except Exception:
            log("[CONTRACT] failed: %s" % traceback.format_exc()[-600:])
        try:
            if self.end_left() > 120:
                self.rewrite_check()
        except Exception:
            log("[REWRITE] failed: %s" % traceback.format_exc()[-600:])
        try:
            if self.end_left() > 40:
                files = self.verify_suite(confirm_runs=2)
        except Exception:
            log("[FINAL] verify failed: %s" % traceback.format_exc()[-800:])
        files = files or self.best_files or self.fallback_files()
        n_tests = sum(len(re.findall(r"(?m)^def test_", t)) for t in files.values())
        log("[FINAL] %d files, %d tests, $%.4f over %d calls" % (len(files), n_tests, self.llm.spent, self.llm.calls))
        return self.write_patch(files)

    def fallback_files(self) -> dict:
        try:
            files, _ = self.build_suite()
            if files:
                log("[FINAL] shipping the last recorded suite instead of smoke tests")
                return files
        except Exception:
            pass
        if not self.packages:
            return {}
        lines = ['"""Smoke tests for the public API."""', "import importlib", ""]
        for p in self.packages[:1]:
            lines += ["", "def test_package_imports():", "    module = importlib.import_module(%r)" % p["name"],
                      "    assert module is not None", ""]
        return {"test_smoke.py": "\n".join(lines)}

    def write_patch(self, files: dict) -> str:
        tdir = os.path.join(self.repo, self.test_rel)
        existed = os.path.isdir(tdir)
        initial = self.initial_test_entries if self.initial_test_entries is not None else set()
        written = []
        patch = ""
        try:
            if existed:
                for entry in os.listdir(tdir):
                    if entry in initial:
                        continue
                    full = os.path.join(tdir, entry)
                    if os.path.isdir(full):
                        shutil.rmtree(full, ignore_errors=True)
                    else:
                        os.remove(full)
            os.makedirs(tdir, exist_ok=True)
            for rel, text in files.items():
                if rel in initial:
                    continue
                path = os.path.join(tdir, rel)
                with open(path, "w") as fh:
                    fh.write(text if text.endswith("\n") else text + "\n")
                written.append(path)
            idx = os.path.join(self.work, "index.tmp")
            env = dict(os.environ, GIT_INDEX_FILE=idx)
            subprocess.run(["git", "read-tree", "HEAD"], cwd=self.repo, env=env, capture_output=True, timeout=60)
            rels = [os.path.relpath(p, self.repo) for p in written]
            subprocess.run(["git", "add", "-f", "--"] + rels, cwd=self.repo, env=env, capture_output=True, timeout=60)
            r = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--cached", "--binary", "--no-color",
                                "--no-ext-diff", "HEAD", "--"] + rels, cwd=self.repo, env=env,
                               capture_output=True, timeout=60)
            patch = r.stdout.decode(errors="replace")
        except Exception:
            log("[PATCH] building the diff failed: %s" % traceback.format_exc()[-500:])
        finally:
            for path in written:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if not existed:
                shutil.rmtree(tdir, ignore_errors=True)
        if not patch.strip():
            patch = _manual_patch(self.test_rel, {k: v for k, v in files.items() if k not in initial})
        return patch

_PARSED: dict = {}

def _read_source(path: str) -> tuple:
    """(text, tree) of a source file, cached while the file is unchanged; tree is None when it does not parse.

    Callers only read the tree.
    """
    try:
        st = os.stat(path)
    except OSError:
        return "", None
    key = (path, st.st_mtime_ns, st.st_size)
    hit = _PARSED.get(key)
    if hit is None:
        try:
            with open(path, errors="replace") as fh:
                text = fh.read()
        except OSError:
            return "", None
        try:
            tree = ast.parse(text)
        except Exception:
            tree = None
        hit = _PARSED[key] = (text, tree)
    return hit

def _seal(d: str) -> None:
    try:
        os.chmod(d, 0o555)
    except OSError:
        pass

def _remove_dir(d: str) -> None:
    try:
        os.chmod(d, 0o755)
    except OSError:
        pass
    shutil.rmtree(d, ignore_errors=True)

def _make_readable(root: str) -> None:
    for dirpath, dirs, fs in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in fs:
            os.chmod(os.path.join(dirpath, f), 0o644)

def _number(text: str) -> str:
    return "\n".join("%5d  %s" % (i + 1, line) for i, line in enumerate(text.split("\n")))

def _outline(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "(could not parse)"
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append("line %d: def %s(...)" % (node.lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            out.append("line %d: class %s" % (node.lineno, node.name))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append("    line %d: def %s(...)" % (sub.lineno, sub.name))
    return "\n".join(out[:150]) or "(no functions or classes)"

def _ranges(lines: list) -> str:
    out = []
    start = prev = None
    for n in sorted(lines):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = n
    if start is not None:
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(out)

def _walk_no_nested(stmts):
    stack = list(stmts)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))

def _lint_case(fn) -> str:
    if isinstance(fn, ast.AsyncFunctionDef):
        return "case functions must not be async (use asyncio.run inside a plain function)"
    if fn.decorator_list:
        return "case functions must not be decorated"
    a = fn.args
    params = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    if a.vararg or a.kwarg or any(p != "tmp_path" for p in params):
        return "a case takes no parameters (or only `tmp_path`)"
    returns = 0
    for node in _walk_no_nested(fn.body):
        if isinstance(node, ast.Return) and node.value is not None:
            returns += 1
        if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)):
            return "line %d: a case must not be a generator or coroutine" % node.lineno
    if not returns:
        return "a case must return what it observes (`return <expression>`)"
    return ""

def _simple_case(fn) -> bool:
    if not fn.body or not isinstance(fn.body[-1], ast.Return) or fn.body[-1].value is None:
        return False
    return not any(isinstance(node, ast.Return) for node in _walk_no_nested(fn.body[:-1]))

def _library_helpers(tree, lib_aliases: set) -> set:
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    used = {k: {x.id for x in ast.walk(n) if isinstance(x, ast.Name)} for k, n in defs.items()}
    tainted = set()
    changed = True
    while changed:
        changed = False
        for k, names in used.items():
            if k not in tainted and names & (lib_aliases | tainted):
                tainted.add(k)
                changed = True
    return tainted

def _import_time_library_use(node, lib_aliases: set, lib_helpers: set) -> str:
    def lib_ref(exprs):
        for e in exprs:
            for x in ast.walk(e):
                if isinstance(x, ast.Name) and x.id in lib_aliases:
                    return x.id
        return ""

    def helper_call(exprs):
        for e in exprs:
            for x in ast.walk(e):
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id in lib_helpers:
                    return x.func.id + "()"
        return ""

    def helper_decorator(decorators):
        for d in decorators:
            base = d.func if isinstance(d, ast.Call) else d
            if isinstance(base, ast.Name) and base.id in lib_helpers:
                return base.id
        return ""

    def check_function(fn, owner):
        a = fn.args
        defaults = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
        name = lib_ref(fn.decorator_list) or helper_call(fn.decorator_list) or helper_decorator(fn.decorator_list)
        if name:
            return "the decorator of `%s%s` (`%s`)" % (owner, fn.name, name)
        name = lib_ref(defaults) or helper_call(defaults)
        if name:
            return "a default value of `%s%s` (`%s`)" % (owner, fn.name, name)
        return ""

    def check_class(cls, owner):
        head = list(cls.bases) + [k.value for k in cls.keywords] + list(cls.decorator_list)
        name = lib_ref(head) or helper_call(head) or helper_decorator(cls.decorator_list)
        if name:
            return "class `%s%s` (its bases or decorators use `%s`)" % (owner, cls.name, name)
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                what = check_function(item, owner + cls.name + ".")
            elif isinstance(item, ast.ClassDef):
                what = check_class(item, owner + cls.name + ".")
            else:
                name = lib_ref([item]) or helper_call([item])
                what = "the body of class `%s%s` (`%s`)" % (owner, cls.name, name) if name else ""
            if what:
                return what
        return ""

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return check_function(node, "")
    if isinstance(node, ast.ClassDef):
        return check_class(node, "")
    value = getattr(node, "value", None)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and value is not None:
        name = lib_ref([value]) or helper_call([value])
        if name:
            return "this module-level value (`%s`)" % name
    return ""



_FORBIDDEN_ATTRS = {'__code__', '__closure__', '__globals__', '__dict__', '__file__', '__spec__', '__loader__', '__cached__', '__path__', '__subclasses__'}
_FORBIDDEN_CALLS = {'id': 'object ids differ between runs', 'hash': 'hash values differ between runs', 'globals': 'process state', 'locals': 'process state', 'breakpoint': 'interactive', 'vars': 'it exposes implementation details (the attribute dictionary)', 'dir': 'it lists private and imported names, which a refactoring may change'}
_FORBIDDEN_DOTTED = {'time.time': 'the real clock', 'time.monotonic': 'the real clock', 'time.perf_counter': 'the real clock', 'time.sleep': 'sleeping', 'time.process_time': 'the real clock', 'datetime.now': 'the real clock', 'datetime.utcnow': 'the real clock', 'datetime.today': 'the real clock', 'date.today': 'the real clock', 'os.environ': 'environment variables', 'os.getenv': 'environment variables', 'os.getcwd': 'the working directory', 'os.getpid': 'process state', 'inspect.getsource': 'source code', 'inspect.getsourcelines': 'source code', 'inspect.getfile': 'file paths', 'sys.modules': 'process state', 'importlib.reload': 'process state', 'uuid.uuid4': 'randomness', 'uuid.uuid1': 'time and host'}

def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""

def _forbidden(node) -> str:
    if isinstance(node, ast.Attribute):
        a = node.attr
        if a in _FORBIDDEN_ATTRS:
            return "`.%s` is an implementation detail" % a
        if a.startswith("_") and not (a.startswith("__") and a.endswith("__")):
            if not (isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")):
                return "`.%s` is a private attribute" % a
        d = _dotted(node)
        for k, why in _FORBIDDEN_DOTTED.items():
            if d == k or d.endswith("." + k):
                return "`%s` depends on %s" % (d, why)
    if isinstance(node, ast.Name) and node.id == "__file__":
        return "`__file__` ties the test to file paths"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
        return "`%s()` is not allowed: %s" % (node.func.id, _FORBIDDEN_CALLS[node.func.id])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
            node.func.id in ("getattr", "hasattr", "setattr", "delattr") and len(node.args) >= 2 and \
            isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str) and \
            node.args[1].value.startswith("_"):
        return "`%s(..., %r)` reaches a private or special attribute" % (node.func.id, node.args[1].value)
    return ""


def _owner_top(tree, target):
    line = getattr(target, "lineno", None)
    if line is None:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if first <= line <= (node.end_lineno or node.lineno):
                return node.name
    return None

def _helper_users(tree, cases) -> dict:
    tops = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    refs = {name: {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and x.id in tops and x.id != name}
            for name, node in tops.items()}
    users = {}
    for c in cases:
        seen, todo = set(), list(refs.get(c, ()))
        while todo:
            h = todo.pop()
            if h in seen:
                continue
            seen.add(h)
            todo += refs.get(h, ())
        for h in seen:
            users.setdefault(h, []).append(c)
    return users


def _functions(tree):
    out = []

    def visit(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + node.name
                out.append((q, node))
                visit(node.body, q + ".")
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")
    visit(tree.body, "")
    return out

def _stmt_spans(body):
    """(line, first, last) for each statement that runs code: it ran when any line from first to last did.

    A multi-line statement reports the line of the part being evaluated (an `if (` header reports the line of
    its condition), and `global`, `nonlocal` and bare constants compile to nothing, so they are left out.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Global, ast.Nonlocal)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        inner = getattr(node, "body", None)
        if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
            is_try = isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try)))
            last = max(node.lineno, inner[0].lineno - (0 if is_try else 1))
        elif hasattr(ast, "Match") and isinstance(node, ast.Match) and node.cases:
            last = max(node.lineno, node.cases[0].pattern.lineno)
        else:
            last = node.end_lineno or node.lineno
        yield node.lineno, node.lineno, last
        for field in ("body", "orelse", "finalbody"):
            sub = getattr(node, field, None)
            if isinstance(sub, list):
                yield from _stmt_spans(sub)
        for h in getattr(node, "handlers", []) or []:
            yield from _stmt_spans(h.body)
        for c in getattr(node, "cases", []) or []:
            yield from _stmt_spans(c.body)

def _parses(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except (SyntaxError, ValueError):
        return False

def _adopt_test_names(source: str) -> str:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        if not re.search(r"(?m)^def case_\w+\s*\(", source) and not re.search(r"(?m)^\s+assert\b", source):
            return re.sub(r"(?m)^def test_(\w+\s*\()", r"def case_\1", source)
        return source
    lines = source.split("\n")
    taken = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        inside = list(ast.walk(node))
        returns = any(isinstance(x, ast.Return) and x.value is not None for x in inside)
        asserts = any(isinstance(x, ast.Assert) for x in inside)
        new = "case_" + node.name[5:]
        if returns and not asserts and new not in taken and all(a.arg == "tmp_path" for a in node.args.args):
            lines[node.lineno - 1] = re.sub(r"^def test_", "def case_", lines[node.lineno - 1])
            taken.add(new)
    return "\n".join(lines)

def _top_level_span(lines: list, at: int) -> tuple:
    def starts_block(text: str) -> bool:
        return bool(text.strip()) and text[:1] not in " \t)]}#" and not text.startswith(('"""', "'''"))
    first = min(max(at, 0), max(0, len(lines) - 1))
    while first > 0 and not starts_block(lines[first]):
        first -= 1
    while first > 0 and lines[first - 1].startswith("@"):
        first -= 1
    last = first + 1
    while last < len(lines) and (not starts_block(lines[last]) or lines[last].startswith("@") and last <= at):
        last += 1
    return first, max(last, min(at + 1, len(lines)))

def _repair_source(source: str) -> tuple:
    removed = 0
    for _ in range(12):
        try:
            ast.parse(source)
            return source, removed
        except (SyntaxError, ValueError) as error:
            lines = source.split("\n")
            first, last = _top_level_span(lines, (getattr(error, "lineno", None) or len(lines)) - 1)
            if first == 0 and last >= len(lines):
                return "", removed + 1
            source = "\n".join(lines[:first] + lines[last:])
            removed += 1
    return (source, removed) if _parses(source) else ("", removed)

def _localize_library_values(source: str, pkg_names: set) -> tuple:
    """Move module-level statements that run library code at import into the functions that use them.

    A file whose module level calls the library cannot even be imported when a change breaks that call, so
    every one of its tests is lost. Such a statement (a value, a class with library bases, a function with a
    library decorator or default), and any module-level statement that depends on it, is removed from module
    level and repeated at the start of every top-level function that uses one of the names it binds.
    Returns (source, number of statements moved)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, 0
    lib_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in pkg_names:
                    lib_aliases.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] in pkg_names:
            lib_aliases.update(a.asname or a.name for a in node.names)
    if not lib_aliases:
        return source, 0
    lib_helpers = _library_helpers(tree, lib_aliases)

    def bound(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return {node.name}
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        return {x.id for t in targets if t is not None for x in ast.walk(t) if isinstance(x, ast.Name)}

    def used(node):
        return {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}

    movable = (ast.Assign, ast.AnnAssign, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    moved, names = [], set()
    for node in tree.body:
        if not isinstance(node, movable):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("case_"):
            continue
        flagged = _import_time_library_use(node, lib_aliases, lib_helpers)
        # a plain function body runs only when called; only its decorators and defaults depend on `names`
        head = (node.decorator_list + node.args.defaults + [d for d in node.args.kw_defaults if d]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else [node])
        if flagged or any(used(h) & names for h in head):
            if not bound(node) or any(isinstance(x, (ast.Global, ast.Nonlocal)) for x in ast.walk(node)):
                return source, 0
            moved.append(node)
            names |= bound(node)
    if not moved:
        return source, 0
    lines = source.split("\n")

    def first_line(node):
        return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])

    blocks = {id(n): textwrap.dedent("\n".join(lines[first_line(n) - 1:n.end_lineno])) for n in moved}
    users = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node not in moved:
            need = used(node) & names
            if need:
                users.append(node)
    inserts = {}
    for fn in users:
        # bring in every moved statement the function needs, with what those statements need in turn
        want = used(fn) & names
        while True:
            more = set().union(*[used(n) for n in moved if bound(n) & want]) & names
            if more <= want:
                break
            want |= more
        body = fn.body
        if any(isinstance(x, (ast.Global, ast.Nonlocal)) and set(x.names) & want for x in ast.walk(fn)):
            return source, 0
        indent = re.match(r"\s*", lines[body[0].lineno - 1]).group(0)
        text = "\n".join(blocks[id(n)] for n in moved if bound(n) & want)
        inserts[body[0].lineno - 1] = textwrap.indent(text, indent).split("\n")
    drop = set()
    for n in moved:
        drop.update(range(first_line(n) - 1, n.end_lineno))
    out = []
    for i, line in enumerate(lines):
        if i in inserts:
            out.extend(inserts[i])
        if i not in drop:
            out.append(line)
    new = "\n".join(out)
    try:
        ast.parse(new)
    except SyntaxError:
        return source, 0
    return new, len(moved)

def _mend_imports(source: str, error: str) -> tuple:
    name = re.search(r"cannot import name '(\w+)'", error)
    missing = re.search(r"No module named '([\w.]+)'", error)
    if not name and not missing:
        return "", []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "", []
    lines = source.split("\n")
    gone, edits = set(), []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if name and any(a.name == name.group(1) for a in node.names):
                keep = [a for a in node.names if a.name != name.group(1)]
                gone.update(a.asname or a.name for a in node.names if a.name == name.group(1))
                edits.append((node, keep))
            elif missing and node.module and (node.module == missing.group(1) or
                                              node.module.startswith(missing.group(1) + ".")):
                gone.update(a.asname or a.name for a in node.names)
                edits.append((node, []))
        elif isinstance(node, ast.Import) and missing:
            keep = [a for a in node.names if not (a.name == missing.group(1) or
                                                  a.name.startswith(missing.group(1) + "."))]
            if len(keep) != len(node.names):
                gone.update((a.asname or a.name).split(".")[0] for a in node.names if a not in keep)
                edits.append((node, keep))
    if not edits:
        return "", []
    # whatever refers to a dropped name goes too: cases, the helpers and classes they call, module-level
    # values, and in turn everything that refers to those
    tainted, dropped = set(gone), []
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) or any(node is d for d in dropped):
                continue
            if not any(isinstance(x, ast.Name) and x.id in tainted for x in ast.walk(node)):
                continue
            dropped.append(node)
            changed = True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                tainted.add(node.name)
            else:
                targets = list(getattr(node, "targets", None) or [])
                if getattr(node, "target", None) is not None:
                    targets.append(node.target)
                for target in targets:
                    tainted.update(x.id for x in ast.walk(target) if isinstance(x, ast.Name))
    using = [n.name for n in dropped if isinstance(n, ast.FunctionDef) and n.name.startswith("case_")]
    changes = [(node, keep) for node, keep in edits] + [(node, None) for node in dropped]
    for node, keep in sorted(changes, key=lambda e: -e[0].lineno):
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        if keep is None:
            del lines[first - 1:node.end_lineno]
        elif keep:
            node.names = keep
            lines[node.lineno - 1:node.end_lineno] = [ast.unparse(node)]
        else:
            lines[node.lineno - 1:node.end_lineno] = ["" for _ in range(node.end_lineno - node.lineno + 1)]
    return "\n".join(lines), using

def _contract_variant(source: bytes):
    text = source.decode("utf-8")
    tree = ast.parse(text)
    starts = [0]
    for line in source.split(b"\n"):
        starts.append(starts[-1] + len(line) + 1)

    def off(lineno, col):
        return starts[lineno - 1] + col

    in_fstring = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for sub in ast.walk(node):
                in_fstring.add(id(sub))
    edits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            for sub in ast.walk(node.exc):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value and id(sub) not in in_fstring:
                    edits.append((off(sub.lineno, sub.col_offset), off(sub.end_lineno, sub.end_col_offset),
                                  repr(sub.value + " (reworded)")))
        if isinstance(node, ast.ClassDef):
            is_exc = node.name.endswith(("Error", "Exception")) or any(
                (_dotted(b) or "").split(".")[-1].endswith(("Error", "Exception", "Warning")) for b in node.bases)
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and (item.name == "__repr__" or (is_exc and item.name == "__str__")):
                    for sub in _walk_no_nested(item.body):
                        if isinstance(sub, ast.Return) and sub.value is not None:
                            v = sub.value
                            edits.append((off(v.lineno, v.col_offset), off(v.end_lineno, v.end_col_offset),
                                          "(str(%s) + ' ~')" % ast.unparse(v)))
    if not edits:
        return None
    edits.sort(reverse=True)
    out, last = source, None
    for start, end, repl in edits:
        if last is not None and end > last:
            continue
        out = out[:start] + repl.encode("utf-8") + out[end:]
        last = start
    try:
        compile(out, "<variant>", "exec", dont_inherit=True)
    except Exception:
        return None
    return out

_DYNAMIC_NAMES = {"locals", "vars", "eval", "exec", "dir", "globals", "_getframe", "currentframe", "f_locals"}

def _bound_outside_comprehensions(body):
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node
        stack.extend(ast.iter_child_nodes(node))

def _rewrite_variant(source: bytes):
    text = source.decode("utf-8")
    tree = ast.parse(text)
    starts = [0]
    for line in source.split(b"\n"):
        starts.append(starts[-1] + len(line) + 1)
    taken = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)} | set(dir(__builtins__))
    edits = []
    for _, fn in _functions(tree):
        inner = [n for stmt in fn.body for n in ast.walk(stmt)]
        if any((isinstance(n, ast.Name) and n.id in _DYNAMIC_NAMES) or
               (isinstance(n, ast.Attribute) and n.attr in _DYNAMIC_NAMES) or
               isinstance(n, (ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom)) for n in inner):
            continue
        a = fn.args
        params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
        params |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
        nested = set()
        for n in inner:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nested.add(n.name)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.JoinedStr)):
                for x in ast.walk(n):
                    if isinstance(x, ast.Name):
                        nested.add(x.id)
                    elif isinstance(x, ast.arg):
                        nested.add(x.arg)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                nested.add(n.name)
            elif type(n).__name__ in ("MatchAs", "MatchStar") and getattr(n, "name", None):
                nested.add(n.name)  # a `case` pattern binds it: renaming the name elsewhere would split it
            elif type(n).__name__ == "MatchMapping" and getattr(n, "rest", None):
                nested.add(n.rest)
        comp_targets = {x.id for n in inner if isinstance(n, ast.comprehension)
                        for x in ast.walk(n.target) if isinstance(x, ast.Name)}
        assigned = {n.id for n in inner if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        local = {x for x in assigned - params - nested
                 if x not in comp_targets or x in {n.id for n in _bound_outside_comprehensions(fn.body)}}
        if not local:
            continue
        rename = {}
        for name in sorted(local):
            new = "%s_rw" % name
            while new in taken:
                new += "_"
            taken.add(new)
            rename[name] = new
        for n in inner:
            if isinstance(n, ast.Name) and n.id in rename:
                edits.append((starts[n.lineno - 1] + n.col_offset, starts[n.end_lineno - 1] + n.end_col_offset,
                              rename[n.id]))
    if not edits:
        return None
    out = source
    for start, end, new in sorted(set(edits), reverse=True):
        out = out[:start] + new.encode("utf-8") + out[end:]
    try:
        compile(out, "<variant>", "exec", dont_inherit=True)
    except Exception:
        return None
    return out

def _repair_reply_markers(body: str) -> str:
    try:
        ast.parse(body)
        return body
    except SyntaxError:
        pass
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(body).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return body
    protected = set()
    fstrings = []
    for token in tokens:
        if token.type == tokenize.STRING:
            protected.update(range(token.start[0], token.end[0] + 1))
        elif token.type == getattr(tokenize, "FSTRING_START", -1):
            fstrings.append(token.start[0])
        elif token.type == getattr(tokenize, "FSTRING_END", -1) and fstrings:
            protected.update(range(fstrings.pop(), token.end[0] + 1))
    if fstrings:
        return body
    lines = body.splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines, 1):
        if i not in protected and re.match(r"\s*(SKIP\s*:|SKIP\s+m\d|DONE\s*$)", line):
            lines[i - 1] = "\n" if line.endswith("\n") else ""
            changed = True
    if not changed:
        return body
    repaired = "".join(lines)
    try:
        ast.parse(repaired)
    except SyntaxError:
        return body
    return repaired

def _parse_reply(reply: str, finish):
    blocks = []
    pos = 0
    pattern = re.compile(r"```([^\n`]*)\n(.*?)\n?```", re.S)
    for m in pattern.finditer(reply):
        info, body = m.group(1).strip(), m.group(2)
        pos = m.end()
        low = info.lower()
        name = None
        mm = re.search(r"(cases_[A-Za-z0-9_]+)\.py", info)
        if mm:
            name = mm.group(1).lower() + ".py"
        lines = body.split("\n")
        first_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first_idx is not None:
            first = lines[first_idx].strip()
            mm = re.fullmatch(r"(?:#\s*)?(?:file(?:name)?:?\s*)?`?(cases_[A-Za-z0-9_]+)\.py`?:?", first)
            if mm:
                name = name or mm.group(1).lower() + ".py"
                del lines[first_idx]
                body = "\n".join(lines)
        is_python = low.startswith("python") or low in ("py", "") or name is not None
        if is_python:
            body = _repair_reply_markers(body)
            body = _adopt_test_names(body)
        if is_python and re.search(r"EXCEPTION_CLASSES\s*=\s*True", info) and \
                not re.search(r"(?m)^EXCEPTION_CLASSES\s*=", body):
            body = "EXCEPTION_CLASSES = True\n" + body
        if name:
            blocks.append(("case", name, body.strip("\n") + "\n"))
        elif low in ("bash", "sh", "shell", "console"):
            if body.strip():
                blocks.append(("bash", None, body.strip()))
        elif is_python and re.search(r"(?m)^def case_\w+\(", body):
            blocks.append(("case-unnamed", None, body.strip("\n") + "\n"))
    truncated = "```" in reply[pos:] or finish == "length"
    done = bool(re.search(r"(?m)^\W*DONE\W*$", reply))
    return blocks, done, truncated

def _test_names(case_source: str) -> dict:
    tree = ast.parse(case_source)
    used = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    names = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("case_"):
            name = "test_" + node.name[len("case_"):]
            while name in used:
                name += "_"
            used.add(name)
            names[node.name] = name
    return names

def _test_module_source(case_source: str, topic: str, names: dict, keep: set, records: dict | None = None) -> str:
    tree = ast.parse(case_source)
    have_math = have_pytest = False
    for node in tree.body:
        if isinstance(node, ast.Import):
            have_math |= any(a.name == "math" and not a.asname for a in node.names)
            have_pytest |= any(a.name == "pytest" and not a.asname for a in node.names)
    header = [ast.Expr(ast.Constant("Regression tests: %s." % topic.replace("_", " ")))]
    if not have_math:
        header.append(ast.Import(names=[ast.alias(name="math")]))
    if not have_pytest:
        header.append(ast.Import(names=[ast.alias(name="pytest")]))
    for rec in (records or {}).values():
        cls = rec.get("cls") if rec.get("status") == "raises" else None
        if cls and "." in cls:
            header.append(ast.Import(names=[ast.alias(name=cls.rpartition(".")[0])]))
    body = []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            header.insert(1, node)  # must come first, right after the docstring
            continue
        if isinstance(node, ast.FunctionDef) and node.name.startswith("case_"):
            if node.name not in keep or node.name not in names:
                continue
            rec = records.get(node.name) if records is not None else {"status": "record"}
            if rec is None:
                continue
            body.append(_case_to_test(node, rec, names[node.name]))
            continue
        body.append(node)
    seen_imports, uniq = set(), []
    for node in header:
        key = ast.dump(node)
        if key not in seen_imports:
            seen_imports.add(key)
            uniq.append(node)
    helper = ast.parse(SAME_SRC).body
    module = ast.Module(body=uniq + body + helper, type_ignores=[])
    ast.fix_missing_locations(module)
    text = ast.unparse(module) + "\n"
    compile(text, "<generated>", "exec")
    return text

def _case_to_test(fn: ast.FunctionDef, rec: dict, name: str) -> ast.FunctionDef:
    stmts = [copy.deepcopy(s) for s in fn.body]
    doc = None
    if len(stmts) > 1 and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, ast.Constant) \
            and isinstance(stmts[0].value.value, str):
        doc = stmts.pop(0)
    if not _simple_case(fn):
        inner = ast.FunctionDef(name="_case_body", args=copy.deepcopy(fn.args), body=stmts, decorator_list=[],
                                returns=None, type_comment=None)
        if sys.version_info >= (3, 12):
            inner.type_params = []
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
        call = ast.Call(func=ast.Name("_case_body", ast.Load()), args=[ast.Name(p, ast.Load()) for p in params],
                        keywords=[])
        stmts = [inner]
        ret = ast.Return(value=call)
    else:
        ret = stmts.pop()
    local_names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    var = "actual"
    while var in local_names:
        var += "_"
    if rec["status"] == "raises":
        wrapped = not _simple_case(fn)
        inner = ([] if wrapped else stmts) + [ast.Expr(ret.value)]
        cls = ast.parse(rec.get("cls") or "Exception", mode="eval").body
        new_body = (stmts if wrapped else []) + [ast.With(items=[ast.withitem(
            context_expr=ast.Call(func=ast.Attribute(ast.Name("pytest", ast.Load()), "raises", ast.Load()),
                                  args=[cls], keywords=[]),
            optional_vars=None)], body=inner)]
    else:
        stmts.append(ast.Assign(targets=[ast.Name(var, ast.Store())], value=ret.value))
        if rec["status"] == "record":
            stmts.append(ast.Return(ast.Name(var, ast.Load())))
        else:
            expected = ast.parse(rec["src"], mode="eval").body
            stmts.append(ast.Assert(test=ast.Call(func=ast.Name("_same_value", ast.Load()),
                                                  args=[ast.Name(var, ast.Load()), expected], keywords=[]), msg=None))
        new_body = stmts
    if doc is not None:
        new_body.insert(0, doc)
    new = ast.FunctionDef(name=name, args=copy.deepcopy(fn.args), body=new_body, decorator_list=[], returns=None,
                          type_comment=None)
    if sys.version_info >= (3, 12):
        new.type_params = []
    return new

def _manual_patch(test_rel: str, files: dict) -> str:
    out = []
    for rel in sorted(files):
        path = "%s/%s" % (test_rel.strip("/"), rel)
        text = files[rel] if files[rel].endswith("\n") else files[rel] + "\n"
        lines = text.split("\n")[:-1]
        out.append("diff --git a/%s b/%s\nnew file mode 100644\n--- /dev/null\n+++ b/%s\n@@ -0,0 +1,%d @@\n" % (
            path, path, path, len(lines)))
        out.append("".join("+" + line + "\n" for line in lines))
    return "".join(out)



_CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
             ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}
_BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Div,
             ast.Mod: ast.FloorDiv, ast.Pow: ast.Mult, ast.LShift: ast.RShift, ast.RShift: ast.LShift,
             ast.BitAnd: ast.BitOr, ast.BitOr: ast.BitAnd, ast.BitXor: ast.BitOr}
_LOG_CALL = re.compile(r"(^|\.)(warn|warning|warnings|debug|info|error|exception|critical|log|print|trace)$")
_EXC_SWAP = {"ValueError": "TypeError", "TypeError": "ValueError", "KeyError": "IndexError",
             "IndexError": "KeyError", "AttributeError": "TypeError", "RuntimeError": "ValueError"}
_TYPING_CALLS = {"TypeVar", "NewType", "ParamSpec", "TypeVarTuple", "cast", "overload"}
_ORDER_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
_TWINS = {}
for _a, _b in (("min", "max"), ("any", "all"), ("startswith", "endswith"), ("lstrip", "rstrip"), ("ljust", "rjust"),
               ("find", "rfind"), ("index", "rindex"), ("split", "rsplit"), ("partition", "rpartition"),
               ("upper", "lower"), ("floor", "ceil"), ("bisect_left", "bisect_right"),
               ("insort_left", "insort_right"), ("appendleft", "append"), ("popleft", "pop")):
    _TWINS[_a], _TWINS[_b] = _b, _a
_NO_SWAP_CALLS = {"isinstance", "issubclass", "getattr", "setattr", "hasattr", "super", "range", "enumerate"}
_SKIP_FUNCS = {"__repr__", "__str__", "__format__", "__rich_repr__", "__del__", "__hash__"}
_NOT_PACKAGES = {"tests", "test", "testing", "docs", "doc", "examples", "example", "scripts", "tools", "ci", "build",
                 "dist"}





def _default_use_note(index_of_calls, fn, param: str, index, method: bool) -> str:
    calls, owner = index_of_calls
    omit, passes = [], 0
    for node in calls.get(fn.name, ()):
        f = node.func
        pos = -1 if index is None else index - (1 if method and isinstance(f, ast.Attribute) else 0)
        if any(k.arg == param or k.arg is None for k in node.keywords) or \
                any(isinstance(a, ast.Starred) for a in node.args) or (pos >= 0 and len(node.args) > pos):
            passes += 1
        else:
            omit.append("line %d (in %s)" % (node.lineno, owner(node.lineno)))
    if omit:
        return "  the default is used by the calls that leave `%s` out: %s" % (
            param, ", ".join(omit[:6]) + (", ..." if len(omit) > 6 else ""))
    if passes:
        return "  every call of `%s` in this file passes `%s`; only callers that leave it out see the change" % (
            fn.name, param)
    return ""

_REGEX_CALL = re.compile(r"(^|\.)(re|regex)\.(compile|sub|subn|match|search|fullmatch|split|findall|finditer)$")

def _call_index(tree) -> tuple:
    spans = [(q, f.lineno, f.end_lineno or f.lineno) for q, f in _functions(tree)]

    def owner(line):
        best = None
        for q, a, b in spans:
            if a <= line <= b and (best is None or a >= best[1]):
                best = (q, a)
        return best[0] if best else "module level"
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name:
                calls.setdefault(name, []).append(node)
    return calls, owner


def _difference(a: dict, x) -> str:
    if x is None:
        return "; a later run did not finish"
    if x["status"] != a["status"]:
        def outcome(r):
            if r["status"] == "value":
                return "returned a value"
            if r["status"] == "raises":
                return "raised " + str(r.get("exc"))
            return "could not be recorded (%s)" % (r.get("error") or r["status"])
        return "; one run %s, another %s" % (outcome(a), outcome(x))
    s, t = a.get("src") or "", x.get("src") or ""
    i = next((k for k in range(min(len(s), len(t))) if s[k] != t[k]), min(len(s), len(t)))
    lo = max(0, i - 50)
    return ("; first difference: one run gave ...%s... and another ...%s... (keep the case's inputs and calls as they are "
            "and leave only the varying part out of the result, such as ids or counters that setup calls return, "
            "times, or process details)" % (s[lo:i + 40], t[lo:i + 40]))

def _regex_variants(pattern: str, limit: int = 4) -> list:
    out = []
    i, n = 0, len(pattern)
    while i < n and len(out) < limit:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            items = []
            while j < n and pattern[j] != "]":
                k = j + 2 if pattern[j] == "\\" else j + 1
                if k + 1 < n and pattern[k] == "-" and pattern[k + 1] != "]":
                    k = k + 3 if pattern[k + 1] == "\\" else k + 2
                items.append((j, k))
                j = k
            if j < n and len(items) >= 2:
                for a, b in items[-2:]:
                    out.append(pattern[:a] + pattern[b:])
            i = j + 1
            continue
        if c in "+*" and i > 0:
            out.append(pattern[:i] + ("*" if c == "+" else "+") + pattern[i + 1:])
        elif c == "{":
            mm = re.match(r"\{(\d+)(,?)(\d*)\}", pattern[i:])
            if mm:
                out.append(pattern[:i] + "{%d%s%s}" % (int(mm.group(1)) + 1, mm.group(2), mm.group(3)) +
                           pattern[i + mm.end():])
        elif c in "^$" and (c == "$" or i == 0):
            out.append(pattern[:i] + pattern[i + 1:])
        i += 1
    good = []
    for v in out:
        if v == pattern or v in good:
            continue
        try:
            re.compile(v)
        except Exception:
            continue
        good.append(v)
    return good[:limit]

def _generate_mutants(rel: str, source: bytes, covered: set, more: bool = False) -> list:
    call_index = [None]
    text = source.decode("utf-8")
    tree = ast.parse(text)
    line_starts = [0]
    for line in source.split(b"\n"):
        line_starts.append(line_starts[-1] + len(line) + 1)
    lines_text = text.split("\n")
    out = []
    seen = set()
    func_spans = {}
    for qual, fn in _functions(tree):
        func_spans[qual] = (fn.lineno, fn.end_lineno or fn.lineno)

    def span(node):
        return (line_starts[node.lineno - 1] + node.col_offset,
                line_starts[node.end_lineno - 1] + node.end_col_offset)

    def add(node, replacement: str, stmt: tuple, func: str, kind: str, scope_lines=None, module_level=False):
        fspan = func_spans.get(func, (node.lineno, node.end_lineno))
        start, end = span(node)
        repl = replacement.encode("utf-8")
        if source[start:end] == repl or (start, end, repl) in seen:
            return
        seen.add((start, end, repl))
        a, b = node.lineno, node.end_lineno
        head = source[line_starts[a - 1]:start].decode("utf-8", errors="replace")
        tail = source[end:max(end, line_starts[b] - 1)].decode("utf-8", errors="replace")
        before = lines_text[a - 1:b][:4]
        after = (head + replacement + tail).split("\n")[:4]
        show = "\n".join(["  was:  " + x.strip() for x in before] + ["  now:  " + x.strip() for x in after])
        out.append({"file": rel, "line": a, "func": func or "<module>", "start": start, "end": end, "repl": repl,
                    "show": show, "stmt_start": stmt[0], "stmt_end": stmt[1], "scope_lines": scope_lines,
                    "module_level": module_level, "kind": kind, "func_span": fspan})

    def expr_mutations(root, stmt, func, scope_lines=None, module_level=False):
        stack = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, ast.JoinedStr):
                continue
            if isinstance(node, ast.Call) and (_LOG_CALL.search(_dotted(node.func) or "") or
                                               (_dotted(node.func) or "").split(".")[-1] in _TYPING_CALLS):
                continue
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.expr, ast.comprehension, ast.keyword, ast.arguments)):
                    stack.append(child)
            kw = {"scope_lines": scope_lines, "module_level": module_level}
            if isinstance(node, ast.Compare):
                for i, op in enumerate(node.ops):
                    rep = _CMP_SWAP.get(type(op))
                    if rep:
                        new = copy.deepcopy(node)
                        new.ops[i] = rep()
                        add(node, "(%s)" % ast.unparse(new), stmt, func, "compare", **kw)
                for operand in [node.left] + list(node.comparators):
                    if isinstance(operand, ast.Constant) and type(operand.value) is int:
                        add(operand, "(%d)" % (operand.value - 1), stmt, func, "constant", **kw)
            elif isinstance(node, ast.BinOp) and type(node.op) in _BIN_SWAP:
                if isinstance(node.op, ast.Mod) and isinstance(node.left, (ast.Constant, ast.JoinedStr)):
                    continue
                new = copy.deepcopy(node)
                new.op = _BIN_SWAP[type(node.op)]()
                add(node, "(%s)" % ast.unparse(new), stmt, func, "arith", **kw)
            elif isinstance(node, ast.BoolOp):
                new = copy.deepcopy(node)
                new.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
                add(node, "(%s)" % ast.unparse(new), stmt, func, "bool", **kw)
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
                add(node, "(%s)" % ast.unparse(node.operand), stmt, func, "unary", **kw)
            elif isinstance(node, ast.IfExp):
                new = copy.deepcopy(node)
                new.test = ast.UnaryOp(ast.Not(), new.test)
                add(node, "(%s)" % ast.unparse(new), stmt, func, "condition", **kw)
            if isinstance(node, ast.Call) and _REGEX_CALL.search(_dotted(node.func) or "") and node.args and \
                    isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                for variant in _regex_variants(node.args[0].value):
                    add(node.args[0], repr(variant), stmt, func, "regex", **kw)
            if isinstance(node, ast.Call) and (node.keywords or any(isinstance(a, ast.Starred) for a in node.args)):
                removable = [("kw", i) for i in range(len(node.keywords))] + \
                            [("star", i) for i, a in enumerate(node.args) if isinstance(a, ast.Starred)]
                for what, i in removable[:3]:
                    new = copy.deepcopy(node)
                    if what == "kw":
                        del new.keywords[i]
                    else:
                        del new.args[i]
                    add(node, "(%s)" % ast.unparse(new), stmt, func, "argument", **kw)
            elif isinstance(node, ast.Constant):
                v = node.value
                if isinstance(v, bool):
                    add(node, repr(not v), stmt, func, "constant", **kw)
                elif isinstance(v, int):
                    add(node, "(%d)" % (0 if v == 1 else v + 1), stmt, func, "constant", **kw)
                elif isinstance(v, float):
                    add(node, "(%r)" % (v + 1.0), stmt, func, "constant", **kw)
                elif isinstance(v, str):
                    add(node, repr("" if v else "XX"), stmt, func, "constant", **kw)
                elif isinstance(v, bytes):
                    add(node, repr(b"" if v else b"XX"), stmt, func, "constant", **kw)
            if more and not module_level:
                more_mutations(node, stmt, func, kw)

    def more_mutations(node, stmt, func, kw):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _ORDER_OPS:
            for alt in _ORDER_OPS:
                if alt is not type(node.ops[0]):
                    new = copy.deepcopy(node)
                    new.ops[0] = alt()
                    add(node, "(%s)" % ast.unparse(new), stmt, func, "compare-all", **kw)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                                                                    ast.Mod)):
            if not (isinstance(node.op, ast.Mod) and isinstance(node.left, (ast.Constant, ast.JoinedStr))):
                add(node, "(%s)" % ast.unparse(node.left), stmt, func, "operand", **kw)
                add(node, "(%s)" % ast.unparse(node.right), stmt, func, "operand", **kw)
        elif isinstance(node, ast.BoolOp):
            for i in range(len(node.values)):
                rest = node.values[:i] + node.values[i + 1:]
                text = ast.unparse(rest[0]) if len(rest) == 1 else ast.unparse(ast.BoolOp(node.op, rest))
                add(node, "(%s)" % text, stmt, func, "operand", **kw)
        elif isinstance(node, ast.IfExp):
            add(node, "(%s)" % ast.unparse(node.body), stmt, func, "condition-fixed", **kw)
            add(node, "(%s)" % ast.unparse(node.orelse), stmt, func, "condition-fixed", **kw)
        elif isinstance(node, ast.Call):
            name = (_dotted(node.func) or "").split(".")[-1]
            if name in _TWINS:
                target = node.func.attr if isinstance(node.func, ast.Attribute) else name
                new = copy.deepcopy(node)
                if isinstance(new.func, ast.Attribute):
                    new.func.attr = _TWINS[target]
                else:
                    new.func = ast.Name(_TWINS[target], ast.Load())
                add(node, "(%s)" % ast.unparse(new), stmt, func, "twin", **kw)
            positional = [a for a in node.args if not isinstance(a, ast.Starred)]
            if len(positional) == len(node.args) and len(node.args) >= 2 and name not in _NO_SWAP_CALLS:
                for i in range(min(2, len(node.args) - 1)):
                    if ast.dump(node.args[i]) == ast.dump(node.args[i + 1]):
                        continue
                    new = copy.deepcopy(node)
                    new.args[i], new.args[i + 1] = new.args[i + 1], new.args[i]
                    add(node, "(%s)" % ast.unparse(new), stmt, func, "argument-swap", **kw)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            sl = node.slice
            if isinstance(sl, ast.Slice):
                for part in ("lower", "upper"):
                    bound = getattr(sl, part)
                    if bound is not None and not isinstance(bound, ast.Constant):
                        for op in (ast.Add(), ast.Sub()):
                            new = copy.deepcopy(node)
                            setattr(new.slice, part, ast.BinOp(copy.deepcopy(bound), op, ast.Constant(1)))
                            add(node, "(%s)" % ast.unparse(new), stmt, func, "index", **kw)
            elif not isinstance(sl, (ast.Constant, ast.Tuple)) and not (
                    isinstance(sl, ast.UnaryOp) and isinstance(sl.operand, ast.Constant)):
                for op in (ast.Add(), ast.Sub()):
                    new = copy.deepcopy(node)
                    new.slice = ast.BinOp(copy.deepcopy(sl), op, ast.Constant(1))
                    add(node, "(%s)" % ast.unparse(new), stmt, func, "index", **kw)

    def is_docstring(s):
        return isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)

    def header_range(s):
        body = getattr(s, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
            return s.lineno, max(s.lineno, body[0].lineno - 1)
        return s.lineno, s.end_lineno

    def covered_range(a, b):
        return any(n in covered for n in range(a, b + 1))

    def visit(stmts, func, in_func, skip):
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = (func + "." if func else "") + s.name
                fskip = skip or s.name in _SKIP_FUNCS
                first, last = s.body[0].lineno, s.end_lineno or s.lineno
                if not fskip and covered_range(first, last):
                    body_lines = list(range(first, last + 1))
                    params = s.args.posonlyargs + s.args.args
                    method = bool(params) and params[0].arg in ("self", "cls")
                    with_defaults = [(params[len(params) - len(s.args.defaults) + j], d, True)
                                     for j, d in enumerate(s.args.defaults)]
                    with_defaults += [(p, d, False) for p, d in zip(s.args.kwonlyargs, s.args.kw_defaults)
                                      if d is not None]
                    for p, d, positional in with_defaults:
                        n0 = len(out)
                        expr_mutations(d, (s.lineno, s.lineno), qual, scope_lines=body_lines)
                        if isinstance(d, ast.Constant) and d.value is None:
                            add(d, "0", (s.lineno, s.lineno), qual, "constant", scope_lines=body_lines)
                        if len(out) > n0:
                            if call_index[0] is None:
                                call_index[0] = _call_index(tree)
                            note = _default_use_note(call_index[0], s, p.arg, params.index(p) if positional else None,
                                                     method)
                            d_start, d_end = span(d)
                            index = next((j for j, x in enumerate(s.args.defaults) if x is d), -1)
                            for mm in out[n0:]:
                                changed = (source[d_start:mm["start"]] + mm["repl"] +
                                           source[mm["end"]:d_end]).decode("utf-8", errors="replace")
                                mm["show"] += ("\n  (the default of parameter `%s` of `%s`: a call that leaves `%s` out "
                                               "now gets %s instead of %s; write a case that calls it without `%s`, "
                                               "passing other arguments as needed, where its value changes the result)"
                                               % (p.arg, qual, p.arg, changed.strip()[:60], ast.unparse(d)[:60], p.arg))
                                if note:
                                    mm["show"] += "\n" + note
                                if not in_func:
                                    expr = (source[d_start:mm["start"]] + mm["repl"] + source[mm["end"]:d_end])
                                    mm["default"] = {"name": s.name, "line": s.lineno, "param": p.arg,
                                                     "positional": positional, "index": index,
                                                     "expr": expr.decode("utf-8", errors="replace")}
                visit(s.body, qual, True, fskip)
                continue
            if isinstance(s, ast.ClassDef):
                visit(s.body, (func + "." if func else "") + s.name, in_func, skip)
                continue
            if skip or is_docstring(s) or isinstance(s, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
                                                         ast.Pass, ast.Assert)):
                continue
            if hasattr(ast, "Match") and isinstance(s, ast.Match):
                continue
            if isinstance(s, ast.If) and "TYPE_CHECKING" in ast.unparse(s.test):
                continue
            if isinstance(s, (ast.Assign, ast.AnnAssign)):
                targets = s.targets if isinstance(s, ast.Assign) else [s.target]
                if any(isinstance(t, ast.Name) and t.id in ("__all__", "__version__", "__slots__", "__author__")
                       for t in targets):
                    continue
            a, b = header_range(s)
            module_level = not in_func
            is_cov = covered_range(a, b) if in_func else bool(covered)
            is_log = isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and \
                _LOG_CALL.search(_dotted(s.value.func) or "")
            if is_cov and not is_log:
                if in_func:
                    if isinstance(s, (ast.Assign, ast.AugAssign, ast.Expr, ast.Raise, ast.Delete)) or (
                            isinstance(s, ast.AnnAssign) and s.value is not None):
                        add(s, "pass", (a, b), func, "delete")
                    if isinstance(s, ast.Return) and s.value is not None and not (
                            isinstance(s.value, ast.Constant) and s.value.value is None):
                        add(s, "return None", (a, b), func, "return")
                    if isinstance(s, ast.Break):
                        add(s, "continue", (a, b), func, "loop")
                    if isinstance(s, ast.Continue):
                        add(s, "break", (a, b), func, "loop")
                    if isinstance(s, (ast.If, ast.While)):
                        add(s.test, "(not (%s))" % ast.unparse(s.test), (a, b), func, "condition")
                    if more and isinstance(s, ast.If):
                        add(s.test, "True", (a, b), func, "condition-fixed")
                        add(s.test, "False", (a, b), func, "condition-fixed")
                    if more and isinstance(s, ast.AugAssign):
                        add(s, "%s = %s" % (ast.unparse(s.target), ast.unparse(s.value)), (a, b), func, "assign")
                    if more and isinstance(s, ast.Try):
                        for h in s.handlers:
                            if h.type is not None:
                                add(h.type, "()", (h.lineno, h.lineno), func, "handler")
                    if isinstance(s, ast.AugAssign) and type(s.op) in _BIN_SWAP:
                        new = copy.deepcopy(s)
                        new.op = _BIN_SWAP[type(s.op)]()
                        add(s, ast.unparse(new), (a, b), func, "arith")
                    if isinstance(s, ast.Raise) and s.exc is not None:
                        target = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
                        if isinstance(target, (ast.Name, ast.Attribute)):
                            name = _dotted(target).split(".")[-1]
                            if name and name[0].isupper():
                                add(target, _EXC_SWAP.get(name, "RuntimeError"), (a, b), func, "exception")
                if not isinstance(s, ast.Raise):
                    for field, value in ast.iter_fields(s):
                        if field in ("body", "orelse", "finalbody", "handlers", "cases", "decorator_list",
                                     "annotation", "returns", "type_comment", "type_params", "targets", "target"):
                            continue
                        for v in (value if isinstance(value, list) else [value]):
                            if isinstance(v, ast.expr):
                                expr_mutations(v, (a, b), func, module_level=module_level)
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(s, field, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    visit(sub, func, in_func, skip)
            for h in getattr(s, "handlers", []) or []:
                visit(h.body, func, in_func, skip)

    visit(tree.body, "", False, False)
    return out

def agent_main(input):
    statement = input.get("problem_statement", "") if isinstance(input, dict) else str(input)
    run = Run(statement)
    try:
        run.execute()
    except Exception:
        log("[RUN] failed: %s" % traceback.format_exc()[-2000:])
    try:
        patch = run.finalize()
    except Exception:
        log("[RUN] finalize failed: %s" % traceback.format_exc()[-2000:])
        patch = _manual_patch(run.test_rel, run.best_files or run.fallback_files())
    return patch

if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--tg-runner":
    _code = 0
    try:
        _runner_main(sys.argv[2])
    except BaseException:
        traceback.print_exc()
        _code = 1
    # a thread a case left running (a timer, a worker pool) must not keep the process alive
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.flush()
        except Exception:
            pass
    os._exit(_code)
if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--tg-server":
    _server_main(sys.argv[2])

_REPLICA_BUILD_STAMP = "up2-ship-mend"
# spare