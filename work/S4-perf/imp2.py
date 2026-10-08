import subprocess, time, sys, os, statistics, json, py_compile
HOME = os.path.expanduser("~/tgopt/S4-perf")
src = os.path.join(HOME, "agent.py")
text = open(src, encoding="utf-8").read()
lazy = text.replace("import urllib.error\nimport urllib.request\n", "", 1)
open(os.path.join(HOME, "agent_lazy.py"), "w", encoding="utf-8").write(lazy)
cfg = os.path.join(HOME, "cfg.json")
json.dump({"mode": "api", "candidates": [], "out": os.path.join(HOME, "o.jsonl")}, open(cfg, "w"))
for name in ("agent.py", "agent_lazy.py"):
    pyc = py_compile.compile(os.path.join(HOME, name), cfile=os.path.join(HOME, name + "c"))
    xs = []
    for _ in range(25):
        s = time.perf_counter()
        subprocess.run([sys.executable, pyc, "--tg-runner", cfg], capture_output=True, env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"})
        xs.append(time.perf_counter() - s)
    print(name, "median %.1f ms" % (statistics.median(xs) * 1000))
