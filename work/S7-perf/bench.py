import importlib.util, time, ast, sys, os, cProfile, pstats
base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
path = os.path.join(base, "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

import argparse, difflib, textwrap, email.message, configparser
files = [argparse.__file__, difflib.__file__, configparser.__file__, textwrap.__file__]
for f in files:
    src = open(f, "rb").read()
    n = src.count(b"\n") + 1
    cov = set(range(1, n + 1))
    for more in (False, True):
        t = time.perf_counter()
        m = agent._generate_mutants("x.py", src, cov, more=more)
        dt = time.perf_counter() - t
        print(os.path.basename(f), n, "more" if more else "base", len(m), "%.3fs" % dt)
    t = time.perf_counter(); agent._rewrite_variant(src); print(" rewrite %.3fs" % (time.perf_counter() - t))
    t = time.perf_counter(); agent._contract_variant(src); print(" contract %.3fs" % (time.perf_counter() - t))

src = open(argparse.__file__, "rb").read()
cov = set(range(1, src.count(b"\n") + 2))
pr = cProfile.Profile()
pr.enable()
agent._generate_mutants("x.py", src, cov, more=True)
pr.disable()
pstats.Stats(pr).sort_stats("cumulative").print_stats(18)
