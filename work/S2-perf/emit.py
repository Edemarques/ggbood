import os, json, select, time

def run(n):
    r, w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        out = os.fdopen(w, "w", buffering=1)
        for i in range(n):
            # simulate a tiny case run in between
            sum(range(200))
            out.write(json.dumps({"kind": "start", "key": "test_mod_%d::case_something_%d" % (i % 7, i)}) + "\n")
        out.write(json.dumps({"kind": "end"}) + "\n")
        out.flush()
        os._exit(0)
    os.close(w)
    chunks = []
    while True:
        ready, _, _ = select.select([r], [], [], 1.0)
        if ready:
            data = os.read(r, 65536)
            if not data:
                break
            chunks.append(data)
    os.close(r)
    os.waitpid(pid, 0)
    recs = []
    for ln in b"".join(chunks).decode().split("\n"):
        if ln.strip():
            recs.append(json.loads(ln))
    s = json.dumps({"recs": recs}) + "\n"
    json.loads(s)
    return time.perf_counter() - t0

for n in (0, 50, 400):
    ts = sorted(run(n) for _ in range(30))
    print(n, "median ms %.2f" % (ts[15] * 1000))
