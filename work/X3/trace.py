import importlib.util, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(HERE, "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
import sysconfig, fractions, argparse, email.parser, email.policy, textwrap, json
lib = sysconfig.get_paths()["stdlib"] + os.sep

def case_fr():
    s = fractions.Fraction(0)
    for i in range(1, 200):
        s += fractions.Fraction(1, i)
    return str(s)[:10]

def case_ap():
    p = argparse.ArgumentParser(prog="x")
    p.add_argument("--a", type=int, default=3)
    p.add_argument("b", nargs="*")
    return vars(p.parse_args(["--a", "4", "x", "y"]))

def case_email():
    m = email.parser.Parser(policy=email.policy.default).parsestr(
        "From: A <a@b.c>\nTo: x@y.z, \"Q\" <q@r.s>\nSubject: hi =?utf-8?q?there?=\n\nbody\n")
    return [str(m["to"]), str(m["subject"])]

def case_tw():
    return textwrap.fill("hello world " * 50, width=30)

cases = [case_fr, case_ap, case_email, case_tw]
for fn in cases:
    fn()
N = 20
for fn in cases:
    t = time.perf_counter()
    for _ in range(N):
        fn()
    plain = (time.perf_counter() - t) / N
    tr = agent._LineTracer(lib)
    t = time.perf_counter()
    tstop = 0.0
    nlines = 0
    for _ in range(N):
        tr.start()
        fn()
        t1 = time.perf_counter()
        sys.settrace(None)
        import threading; threading.settrace(None)
        lines = tr.lines
        nlines = len(lines)
        res = sorted([os.path.relpath(f, tr.root), n] for f, n in lines)
        tstop += time.perf_counter() - t1
    traced = (time.perf_counter() - t) / N
    # faster stop
    t1 = time.perf_counter()
    for _ in range(N):
        rel = {}
        out = []
        for f, n in lines:
            r = rel.get(f)
            if r is None:
                r = rel[f] = os.path.relpath(f, tr.root)
            out.append([r, n])
        out.sort()
    tfast = (time.perf_counter() - t1) / N
    assert out == res
    print("%-12s plain=%.2fms traced=%.2fms (x%.1f) stop=%.2fms stop_cached=%.2fms lines=%d" % (
        fn.__name__, plain * 1e3, traced * 1e3, traced / plain, tstop / N * 1e3, tfast * 1e3, nlines))

# sys.monitoring alternative
if hasattr(sys, "monitoring"):
    mon = sys.monitoring
    TOOL = mon.COVERAGE_ID
    for fn in cases:
        lines = set()
        root = lib
        def on_line(code, ln):
            if code.co_filename.startswith(root):
                lines.add((code.co_filename, ln))
            return mon.DISABLE
        mon.use_tool_id(TOOL, "cov")
        mon.register_callback(TOOL, mon.events.LINE, on_line)
        t = time.perf_counter()
        for _ in range(N):
            lines.clear()
            mon.set_events(TOOL, mon.events.LINE)
            fn()
            mon.set_events(TOOL, 0)
            mon.restart_events()
        dt = (time.perf_counter() - t) / N
        mon.register_callback(TOOL, mon.events.LINE, None)
        mon.free_tool_id(TOOL)
        print("%-12s monitoring=%.2fms lines=%d" % (fn.__name__, dt * 1e3, len(lines)))
