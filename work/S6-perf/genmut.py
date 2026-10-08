import importlib.util, time, os, ast, sysconfig, sys
here = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(here, "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
lib = sysconfig.get_paths()["stdlib"]
for pkg in sys.argv[1:]:
    r = object.__new__(agent.Run); r.src_root = lib; r.packages = [{"rel": pkg}]
    files = r.package_files()
    tot = 0; n = 0; lines = 0
    for f in files:
        src = open(os.path.join(lib, f), "rb").read()
        cov = set(range(1, src.count(b"\n") + 2))
        lines += len(cov)
        for more in (False,):
            t0 = time.time()
            ms = agent._generate_mutants(f, src, cov, more=more)
            tot += time.time() - t0; n += len(ms)
    print(pkg, "lines", lines, "mutants", n, "gen %.2fs" % tot)
