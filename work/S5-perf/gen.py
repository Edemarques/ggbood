import importlib.util, time, os, sys, ast
path = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
import argparse, json.decoder, email.message, textwrap, configparser, csv
tot = 0
for mod in (argparse, configparser, email.message, textwrap, csv):
    p = mod.__file__
    src = open(p, "rb").read()
    tree = ast.parse(src)
    cov = set(agent._stmt_lines(tree.body))
    for qual, fn in agent._functions(tree):
        cov |= set(agent._stmt_lines(fn.body))
    t0 = time.time()
    for more in (False, True):
        muts = agent._generate_mutants(os.path.basename(p), src, cov, more=more)
        print(os.path.basename(p), len(src.split(b"\n")), "more" if more else "", len(muts), "%.3fs" % (time.time() - t0))
        t0 = time.time()
    t0 = time.time()
    tree = ast.parse(src); list(agent._functions(tree))
    print("  parse", "%.3fs" % (time.time() - t0))
