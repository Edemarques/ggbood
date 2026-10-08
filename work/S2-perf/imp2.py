import sys, os, time, importlib.util
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
before = set(sys.modules)

def child_cost(stmt):
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        t = time.perf_counter()
        exec(stmt)
        new = len(set(sys.modules) - before)
        os.write(w, ("%f %d" % (time.perf_counter() - t, new)).encode())
        os._exit(0)
    os.close(w)
    d = os.read(r, 100)
    os.waitpid(pid, 0)
    os.close(r)
    a, b = d.split()
    return float(a), int(b)

for stmt in ("import pathlib", "import inspect", "import pathlib; pathlib.Path('/tmp/x')"):
    res = [child_cost(stmt) for _ in range(15)]
    print(stmt, "median ms %.2f new modules %d" % (sorted(x[0] for x in res)[7] * 1000, res[0][1]))
print(sys.version)
