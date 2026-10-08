import importlib.util, os, sys, shutil, time, threading
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", SRC)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

root = os.path.expanduser("~/tgopt/S1-perf/lib")
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root)
import fractions, textwrap, difflib, csv, string, ipaddress
for m in (fractions, textwrap, difflib, ipaddress):
    shutil.copy(m.__file__, os.path.join(root, "my_" + os.path.basename(m.__file__)))
sys.path.insert(0, root)
import my_fractions, my_textwrap, my_difflib, my_ipaddress

def w1():
    s = my_fractions.Fraction(0)
    for i in range(1, 400):
        s += my_fractions.Fraction(1, i)
    return str(s)[:10]

def w2():
    t = ("lorem ipsum dolor sit amet " * 400)
    return len(my_textwrap.wrap(t, 37)) + len(my_textwrap.dedent("  a\n  b\n"))

def w3():
    a = ["line %d" % (i % 37) for i in range(600)]
    b = ["line %d" % (i % 41) for i in range(600)]
    return len(list(my_difflib.unified_diff(a, b)))

def w4():
    net = my_ipaddress.ip_network("10.0.0.0/22")
    return sum(1 for h in net.hosts() if h.is_private)

def gen():
    def g():
        for x in my_textwrap.wrap("a b c d e f " * 20, 5):
            yield x
    return list(g())

WORK = [w1, w2, w3, w4, gen]

class MonTracer:
    """sys.monitoring-based: each (code, line) reported once per case."""
    TOOL = 4
    def __init__(self, root):
        self.root = root
        self.lines = set()
        mon = sys.monitoring
        mon.use_tool_id(self.TOOL, "tg")
        ev = mon.events
        mon.register_callback(self.TOOL, ev.PY_START, self._start)
        mon.register_callback(self.TOOL, ev.LINE, self._line)
    def _start(self, code, offset):
        if code.co_filename.startswith(self.root):
            self.lines.add((code.co_filename, code.co_firstlineno))
        return sys.monitoring.DISABLE
    def _line(self, code, line):
        if code.co_filename.startswith(self.root):
            self.lines.add((code.co_filename, line))
        return sys.monitoring.DISABLE
    def start(self):
        self.lines = set()
        sys.monitoring.restart_events()
        ev = sys.monitoring.events
        sys.monitoring.set_events(self.TOOL, ev.PY_START | ev.LINE)
    def stop(self):
        sys.monitoring.set_events(self.TOOL, 0)
        rel = {}
        out = []
        for f, n in self.lines:
            r = rel.get(f)
            if r is None:
                r = rel[f] = os.path.relpath(f, self.root)
            out.append([r, n])
        out.sort()
        return out

def run(tr, fn, reps=1):
    best = 1e9
    for _ in range(reps):
        t0 = time.perf_counter()
        tr.start()
        try:
            fn()
        finally:
            lines = tr.stop()
        best = min(best, time.perf_counter() - t0)
    return best, lines

old = agent._LineTracer(root + os.sep)
new = MonTracer(root + os.sep)
for fn in WORK:
    fn()
    t0 = time.perf_counter(); fn(); base = time.perf_counter() - t0
    to, lo = run(old, fn, 3)
    tn, ln = run(new, fn, 3)
    so, sn = set(map(tuple, lo)), set(map(tuple, ln))
    print("%-4s untraced %.4fs settrace %.4fs (%.1fx) monitoring %.4fs (%.1fx) lines old=%d new=%d only_old=%s only_new=%s"
          % (fn.__name__, base, to, to / base, tn, tn / base, len(so), len(sn), sorted(so - sn)[:5], sorted(sn - so)[:5]))

# relpath cost in stop()
lines = set(("%sfile%d.py" % (root + os.sep, i % 7), i) for i in range(3000))
t0 = time.perf_counter()
x = sorted([os.path.relpath(f, root + os.sep), n] for f, n in lines)
t1 = time.perf_counter()
rel = {}
y = []
for f, n in lines:
    r = rel.get(f)
    if r is None:
        r = rel[f] = os.path.relpath(f, root + os.sep)
    y.append([r, n])
y.sort()
t2 = time.perf_counter()
print("stop() for 3000 lines: relpath each %.4fs, cached %.4fs, equal=%s" % (t1 - t0, t2 - t1, x == y))
