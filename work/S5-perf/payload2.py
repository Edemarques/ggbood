exec(open(__file__.replace("payload2.py", "payload.py")).read().split("# coverage")[0])
def check_payload2(self, keys):
    expected, back, pins = {}, {}, {}
    for k in keys:
        r = self.records[k]
        mod = k.split("::")[0]
        p = pins.get(mod)
        if p is None:
            p = pins[mod] = self.pins_classes(mod)
        tk = self.test_key(k)
        expected[tk] = {"status": r["status"], "src": r.get("src"), "cls": r.get("cls") if p else None}
        back[tk] = k
    secs = sum(self.records[k].get("secs", 0.01) for k in keys)
    longest = max(self.records[k].get("secs", 0.01) for k in keys)
    return expected, back, secs, longest
t0 = time.perf_counter()
for _ in range(20):
    check_payload2(r, keys)
print("check_payload2 400 keys: %.2f ms, same=%s" % ((time.perf_counter() - t0) / 20 * 1000, check_payload2(r, keys) == agent.Run.check_payload(r, keys)))
# 20KB sources
for mod in r.case_sources:
    r.case_sources[mod] = r.case_sources[mod] * 4
t0 = time.perf_counter()
for _ in range(10):
    agent.Run.check_payload(r, keys)
print("orig with 20KB sources: %.2f ms" % ((time.perf_counter() - t0) / 10 * 1000))
t0 = time.perf_counter()
for _ in range(10):
    check_payload2(r, keys)
print("memo with 20KB sources: %.2f ms" % ((time.perf_counter() - t0) / 10 * 1000))
