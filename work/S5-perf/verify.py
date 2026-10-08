import importlib.util
path = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.log = lambda m: print(m)
r = agent.Run.__new__(agent.Run)
r.suite_limit = 10.0
r.excluded = {}
r.best_files = {"OLD": "checkpoint suite"}
runs = []
r.end_left = lambda: 1000
r.build_suite = lambda: ({"test_a.py": "x", "conftest.py": "c"}, {"test_a::test_%d" % i: "cases_a::c%d" % i for i in range(20)})
def ci_run(files, hash_seed="0", src_root=None, absent=False):
    runs.append(hash_seed)
    # 3.0s pytest/import overhead + 20 tests x 0.1s = 5.0s > 4.5s threshold; test sum 2.0s <= 3.0s budget
    return {"rc": 0, "tests": {"test_a::test_%d" % i: {"outcome": "passed", "time": 0.1} for i in range(20)},
            "secs": 5.0, "cpu": 4.8, "output": "", "collect_errors": []}
r.ci_run = ci_run
out = agent.Run._verify_suite(r, 2)
print("pytest runs:", len(runs), "returned:", out, "excluded:", len(r.excluded))
