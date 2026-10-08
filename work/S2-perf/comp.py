import time, argparse, json, os, ast
import importlib.util
for mod in ("argparse", "json.decoder", "email._header_value_parser", "typing"):
    m = importlib.import_module(mod)
    src = open(m.__file__, "rb").read()
    n = src.count(b"\n")
    t = time.perf_counter()
    for _ in range(10):
        compile(src, m.__file__, "exec", dont_inherit=True)
    c = (time.perf_counter() - t) / 10
    print("%-30s lines %5d compile %.2f ms" % (mod, n, c * 1000))
