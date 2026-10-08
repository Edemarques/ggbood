import importlib.util, time, os, ast, sysconfig, sys
here = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(here, "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

lib = sysconfig.get_paths()["stdlib"]
for pkg in sys.argv[1:] or ["email", "asyncio"]:
    r = object.__new__(agent.Run)
    r.src_root = lib
    r.packages = [{"rel": pkg}]
    files = r.package_files()
    nlines = 0
    t0 = time.time()
    for f in files:
        with open(os.path.join(lib, f)) as fh:
            t = fh.read()
        nlines += t.count("\n")
        ast.parse(t)
    tparse = time.time() - t0
    # pick module-level assignment
    target = None
    for f in files:
        tree = ast.parse(open(os.path.join(lib, f)).read())
        for st in tree.body:
            if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and len(st.targets[0].id) > 4:
                target = (f, st.lineno, st.targets[0].id)
                break
        if target:
            break
    t0 = time.time()
    note = r.usage_note(target[0], target[1])
    tu = time.time() - t0
    print(pkg, "files", len(files), "lines", nlines, "parse all %.3fs" % tparse, "usage_note %.3fs" % tu, target,
          len(note))
