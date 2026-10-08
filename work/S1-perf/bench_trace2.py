import os, sys, shutil, time, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_trace as B  # reuses tracers, root, agent
import argparse, configparser, shlex, string, pprint
for m in (argparse, configparser, shlex, pprint):
    shutil.copy(m.__file__, os.path.join(B.root, "my2_" + os.path.basename(m.__file__)))
importlib.invalidate_caches()

def a1():
    import my2_argparse as ap
    p = ap.ArgumentParser(prog="x", exit_on_error=False)
    p.add_argument("--n", type=int, default=3)
    p.add_argument("pos", nargs="*")
    try:
        p.parse_args(["--n", "zz"])
    except ap.ArgumentError as e:
        r = str(e)
    return p.parse_args(["--n", "5", "a", "b"]).n, r, p.format_help()

def a2():
    import my2_configparser as cp
    c = cp.ConfigParser()
    c.read_string("[a]\nx = 1\ny = %(x)s2\n[b]\nz: q\n")
    try:
        c.get("a", "nope")
    except cp.NoOptionError:
        pass
    return {s: dict(c[s]) for s in c.sections()}

def a3():
    import my2_shlex as sh, my2_pprint as pp
    return sh.split("a 'b c' \"d e\" f\\ g"), pp.pformat({i: list(range(i)) for i in range(30)}, width=40)

def a4():
    import threading
    out = []
    def t():
        import my2_shlex as sh
        out.append(sh.quote("a b"))
    th = threading.Thread(target=t); th.start(); th.join()
    return out

for fn in (a1, a2, a3, a4):
    for name in [n for n in list(sys.modules) if n.startswith("my2_")]:
        del sys.modules[name]
    to, lo = B.run(B.old, fn, 1)
    for name in [n for n in list(sys.modules) if n.startswith("my2_")]:
        del sys.modules[name]
    tn, ln = B.run(B.new, fn, 1)
    so, sn = set(map(tuple, lo)), set(map(tuple, ln))
    print("%s settrace %.4fs monitoring %.4fs lines old=%d new=%d only_old=%s only_new=%s"
          % (fn.__name__, to, tn, len(so), len(sn), sorted(so - sn)[:6], sorted(sn - so)[:6]))
    # warm (module already imported)
    to, lo = B.run(B.old, fn, 3)
    tn, ln = B.run(B.new, fn, 3)
    so, sn = set(map(tuple, lo)), set(map(tuple, ln))
    print("   warm settrace %.4fs monitoring %.4fs old=%d new=%d diff=%s %s" % (to, tn, len(so), len(sn), sorted(so - sn)[:6], sorted(sn - so)[:6]))
