import os, sys, subprocess, tempfile, json, importlib.util, time, glob
P = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
src = open(P).read()
def rep(old, new):
    global src
    assert src.count(old) == 1, old
    src = src.replace(old, new)
# fix A: hard exit after the runner finished
rep('''if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--tg-runner":
    _runner_main(sys.argv[2])''', '''if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--tg-runner":
    _runner_main(sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)''')
# fix B: re-firing timer, armed flag, handler reinstalled per case
rep('''    def on_alarm(signum, frame):
        raise _CaseTimeout()

    signal.signal(signal.SIGALRM, on_alarm)
    for key in cfg["cases"]:''', '''    armed = [False]

    def on_alarm(signum, frame):
        if armed[0]:
            raise _CaseTimeout()

    for key in cfg["cases"]:''')
rep('''        t0 = time.perf_counter()
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:''', '''        t0 = time.perf_counter()
        signal.signal(signal.SIGALRM, on_alarm)
        armed[0] = True
        signal.setitimer(signal.ITIMER_REAL, timeout, 0.5)
        try:''')
rep('''        except BaseException as e:
            status, exc = "fatal", e
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)''', '''        except BaseException as e:
            status, exc = "fatal", e
        finally:
            armed[0] = False
            signal.setitimer(signal.ITIMER_REAL, 0)''')
base = os.path.expanduser("~/tgopt/S1-bugs")
Q = os.path.join(base, "patched.py")
open(Q, "w").write(src)
d = sorted(glob.glob(base + "/tmp*"), key=os.path.getmtime)[-1]
spec = importlib.util.spec_from_file_location("agent", Q)
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
for c in ("cfg.json", "cfg2.json"):
    cfg = json.load(open(os.path.join(d, c)))
    if os.path.exists(cfg["out"]): os.remove(cfg["out"])
    t = time.time()
    rc, out = agent._run_proc([sys.executable, Q, "--tg-runner", os.path.join(d, c)], cwd=d, env=dict(os.environ), timeout=12)
    print(c, "rc", rc, "wall %.1f" % (time.time() - t), out[-300:])
    print(open(cfg["out"]).read())
