import importlib.util, os, sys, types, math, signal, time
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", SRC)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
ns = {"math": math}
exec(agent.SAME_SRC, ns)
same = ns["_same_value"]

mod = types.ModuleType("cases_x")
exec('''
class Bad(Exception):
    def __init__(self, code):
        self.code = code
    def __str__(self):
        return "error %s" % self.detail
def case_a():
    raise Bad(3)
def case_b():
    return 1
''', mod.__dict__)
out = []
try:
    agent._run_cases({"cases": ["cases_x::case_a", "cases_x::case_b"]}, "record", {"cases_x": mod}, out.append, same)
except BaseException as e:
    print("record runner crashed:", type(e).__name__, e)
print([r for r in out])

mod2 = types.ModuleType("cases_y")
exec('''
import signal, time
def case_a():
    signal.signal(signal.SIGALRM, lambda s, f: None)   # library installs its own handler and leaves it
    return 1
def case_b():
    while True:
        time.sleep(0.05)
''', mod2.__dict__)
out = []
signal.signal(signal.SIGTERM, lambda s, f: (_ for _ in ()).throw(SystemExit("killed by outer watchdog")))
import threading
t0 = time.time()
threading.Timer(4.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
try:
    agent._run_cases({"cases": ["cases_y::case_a", "cases_y::case_b"], "timeout": 1.0}, "record", {"cases_y": mod2},
                     out.append, same)
except BaseException as e:
    print("after %.1fs: %s %s" % (time.time() - t0, type(e).__name__, e))
print(out)
