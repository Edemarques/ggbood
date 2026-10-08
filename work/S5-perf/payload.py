import importlib.util, time, os, sys, random, json
path = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

class R: pass
r = agent.Run.__new__(agent.Run)
r.case_sources, r.test_names, r.records, r.case_order = {}, {}, {}, {}
r.excluded = {}
random.seed(1)
for mi in range(12):
    mod = "cases_topic%d" % mi
    cases = ["c%03d" % i for i in range(60)]
    body = "import lib\n\n" + "".join("def case_%s():\n    x = lib.thing(%d, 'abc')\n    y = x.method()\n    return [x.value, y]\n\n" % (c, i) for i, c in enumerate(cases))
    r.case_sources[mod] = body
    r.test_names[mod] = {c: "test_" + c for c in cases}
    r.case_order[mod] = cases
    for c in cases:
        r.records["%s::%s" % (mod, c)] = {"status": "value", "src": repr([random.random() for _ in range(5)]), "cls": None, "secs": 0.002,
                                         "lines": [("lib/a.py", random.randint(1, 3000)) for _ in range(300)]}
print("src KB per module", len(body) // 1024)
keys = list(r.records)[:400]
t0 = time.perf_counter()
for _ in range(20):
    agent.Run.check_payload(r, keys)
print("check_payload 400 keys: %.2f ms" % ((time.perf_counter() - t0) / 20 * 1000))
t0 = time.perf_counter()
for _ in range(20):
    sorted(keys, key=r.suite_position)
print("sort suite_position 400 keys: %.2f ms" % ((time.perf_counter() - t0) / 20 * 1000))
t0 = time.perf_counter()
for _ in range(20):
    for k in keys: r.pins_classes(k.split("::")[0])
print("pins_classes x400: %.2f ms" % ((time.perf_counter() - t0) / 20 * 1000))
exp = agent.Run.check_payload(r, keys)[0]
t0 = time.perf_counter()
for _ in range(20):
    json.dumps({"expected": exp, "keys": list(exp)})
print("json dumps: %.2f ms" % ((time.perf_counter() - t0) / 20 * 1000))

# coverage
t0 = time.perf_counter()
agent.Run.rebuild_coverage(r)
print("rebuild_coverage (%d records): %.1f ms; line_cases=%d" % (len(r.records), (time.perf_counter() - t0) * 1000, len(r.line_cases)))
m = {"file": "lib/a.py", "stmt_start": 99999, "stmt_end": 99999, "module_level": True, "scope_lines": None}
t0 = time.perf_counter()
for _ in range(5):
    agent.Run.relevant_keys(r, m)
print("relevant_keys module-level fallback: %.1f ms" % ((time.perf_counter() - t0) / 5 * 1000))
