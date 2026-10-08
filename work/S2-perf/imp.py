import sys, os, time, importlib.util
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
print("inspect loaded:", "inspect" in sys.modules, "pathlib loaded:", "pathlib" in sys.modules)
# also simulate SAME_SRC exec
ns = {"math": agent.math}
exec(agent.SAME_SRC, ns)
print("after SAME_SRC inspect:", "inspect" in sys.modules, "pathlib:", "pathlib" in sys.modules)
print("dataclasses", "dataclasses" in sys.modules)

def child_cost():
    r, w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        t = time.perf_counter()
        import inspect, pathlib
        os.write(w, str(time.perf_counter() - t).encode())
        os._exit(0)
    os.close(w)
    d = os.read(r, 100)
    os.waitpid(pid, 0)
    os.close(r)
    return float(d), time.perf_counter() - t0

res = [child_cost() for _ in range(20)]
print("child import inspect+pathlib ms: median %.2f" % (sorted(x[0] for x in res)[10] * 1000))
print("whole fork roundtrip ms: median %.2f" % (sorted(x[1] for x in res)[10] * 1000))

import inspect, pathlib
res = [child_cost() for _ in range(20)]
print("preloaded: child import ms: median %.3f" % (sorted(x[0] for x in res)[10] * 1000))
print("preloaded whole fork roundtrip ms: median %.2f" % (sorted(x[1] for x in res)[10] * 1000))
