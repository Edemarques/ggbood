import importlib.util, os, sys, time, ast
HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(HERE, "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
import sysconfig
lib = sysconfig.get_paths()["stdlib"]
files = ["textwrap.py", "fractions.py", "json/decoder.py", "argparse.py", "email/_header_value_parser.py"]
tot = {False: 0, True: 0}
for f in files:
    src = open(os.path.join(lib, f), "rb").read()
    n = src.count(b"\n") + 1
    cov = set(range(1, n + 1))
    for more in (False, True):
        t = time.perf_counter()
        ms = agent._generate_mutants(f, src, cov, more=more)
        dt = time.perf_counter() - t
        tot[more] += dt
        print("%-32s lines=%5d more=%d mutants=%5d  %.3fs" % (f, n, more, len(ms), dt))
    # compile cost (server plan)
    t = time.perf_counter()
    for _ in range(5):
        compile(src, f, "exec", dont_inherit=True)
    print("   compile whole file: %.1f ms" % ((time.perf_counter() - t) / 5 * 1000))
    t = time.perf_counter()
    ast.parse(src)
    print("   ast.parse: %.1f ms" % ((time.perf_counter() - t) * 1000))
print("total", tot)
# Partial coverage typical: 40%
import random
random.seed(1)
for f in files:
    src = open(os.path.join(lib, f), "rb").read()
    n = src.count(b"\n") + 1
    cov = set(x for x in range(1, n + 1) if random.random() < 0.4)
    t = time.perf_counter()
    ms = agent._generate_mutants(f, src, cov, more=False)
    print("partial40 %-30s mutants=%5d %.3fs" % (f, len(ms), time.perf_counter() - t))
