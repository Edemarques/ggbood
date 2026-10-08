import os, sys, time, gc, select
mods = ["asyncio", "email.mime.multipart", "http.server", "xml.dom.minidom", "unittest", "argparse", "decimal",
        "json", "logging.handlers", "multiprocessing", "concurrent.futures", "sqlite3", "tarfile", "zipfile",
        "pydoc", "difflib", "inspect", "dataclasses", "typing", "urllib.request", "tkinter", "turtle", "idlelib",
        "lib2to3", "ssl", "csv", "statistics", "fractions", "pathlib", "tomllib", "shelve", "mailbox", "imaplib"]
for m in mods:
    try:
        __import__(m)
    except Exception:
        pass
# a library-like heap: many long-lived objects
heap = [{"k%d" % i: [i, str(i), (i, i)]} for i in range(int(sys.argv[2]))]
print("objects tracked:", len(gc.get_objects()))
if sys.argv[1] == "freeze":
    gc.freeze()

def work():
    # case-like workload: allocations of containers, some cycles
    acc = 0
    for i in range(int(sys.argv[3])):
        d = {"a": [i, {"b": (i,)}]}
        l = [d, d]
        d["self"] = l
        acc += len(l)
    return acc

def child():
    r, w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        work()
        os._exit(0)
    os.close(w)
    os.read(r, 10)
    os.waitpid(pid, 0)
    os.close(r)
    return time.perf_counter() - t0

ts = sorted(child() for _ in range(30))
print(sys.argv[1:], "median ms %.2f" % (ts[15] * 1000))
