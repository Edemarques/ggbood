import threading, time, sys
status = {"m%d" % i: "survived" for i in range(600)}
stop = False
n = [600]
lock = threading.Condition()


def worker():
    while not stop:
        time.sleep(0.002)          # a check finishing (I/O wakeup)
        with lock:
            n[0] += 1
            status["m%d" % n[0]] = "killed"


ts = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
[t.start() for t in ts]
errors = 0
t0 = time.time()
iters = 0
while time.time() - t0 < 10:
    try:
        known = sum(1 for st in status.values() if st == "survived")
    except RuntimeError:
        errors += 1
    iters += 1
    if iters % 50 == 0:
        time.sleep(0.001)
stop = True
print(sys.version.split()[0], "iterations", iters, "RuntimeErrors", errors, "dict size", len(status))
