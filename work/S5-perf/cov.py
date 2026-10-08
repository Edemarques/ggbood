import importlib.util, time, random
path = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
r = agent.Run.__new__(agent.Run)
random.seed(2)
files = ["lib/f%d.py" % i for i in range(20)]
r.records, r.excluded = {}, {}
for i in range(1500):
    lines = set()
    for f in random.sample(files, 4):
        base = random.randint(1, 1500)
        lines |= {(f, base + j) for j in range(150)}
    r.records["cases_m%d::c%d" % (i % 20, i)] = {"lines": [list(x) for x in lines], "secs": 0.01}
t0 = time.perf_counter(); agent.Run.rebuild_coverage(r); t1 = time.perf_counter()
print("rebuild: %.0f ms, line_cases %d" % ((t1 - t0) * 1000, len(r.line_cases)))
m = {"file": files[3], "stmt_start": 99999, "stmt_end": 99999, "module_level": True, "scope_lines": None}
t0 = time.perf_counter()
for _ in range(5):
    a = agent.Run.relevant_keys(r, m)
print("relevant_keys fallback: %.1f ms (%d keys)" % ((time.perf_counter() - t0) / 5 * 1000, len(a)))
# alternative
fc = {}
t0 = time.perf_counter()
for key, rec in r.records.items():
    for rel in {x[0] for x in rec["lines"]}:
        fc.setdefault(rel, set()).add(key)
print("file_cases build: %.1f ms" % ((time.perf_counter() - t0) * 1000))
t0 = time.perf_counter()
for _ in range(5):
    b = sorted(k for k in fc.get(m["file"], ()) if k in r.records and k not in r.excluded)
print("alt lookup: %.2f ms same=%s" % ((time.perf_counter() - t0) / 5 * 1000, a == b))
