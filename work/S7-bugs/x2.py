import os, sys, json, time, subprocess, tempfile, shutil
here = os.path.dirname(os.path.abspath(__file__))
agent_path = os.path.join(here, "..", "..", "codexv2.12_orig.py")
d = tempfile.mkdtemp()
shutil.copy(agent_path, os.path.join(d, "agent.py"))
with open(os.path.join(d, "test_x.py"), "w") as fh:
    fh.write("import threading\n\ndef test_a():\n    t = threading.Timer(20, lambda: None)\n    t.start()\n    return 1\n")
cfg = {"mode": "record", "modules": ["test_x"], "cases": ["test_x::test_a"], "sys_path": [d],
       "out": os.path.join(d, "out.jsonl"), "timeout": 5, "tmp_base": d}
with open(os.path.join(d, "cfg.json"), "w") as fh:
    json.dump(cfg, fh)
t0 = time.time()
try:
    p = subprocess.run([sys.executable, os.path.join(d, "agent.py"), "--tg-runner", os.path.join(d, "cfg.json")],
                       capture_output=True, timeout=8)
    print("rc", p.returncode)
except subprocess.TimeoutExpired:
    print("TIMEOUT after", round(time.time() - t0, 1))
print(open(cfg["out"]).read())
print("elapsed", round(time.time() - t0, 1))
