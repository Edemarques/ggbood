import importlib.util, io, json, os, sys, threading, time, urllib.error

path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
os.environ["OPENROUTER_API_KEY"] = "test"
os.environ["SANDBOX_PROXY_URL"] = "http://proxy.invalid"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

barrier = threading.Barrier(4)


class Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    if "proxy.invalid" in req.full_url:
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"Not Found"))
    payload = json.loads(req.data)
    body = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"cost": 0.001}, "model": payload["model"]}
    return Resp(json.dumps(body).encode())


agent.urllib.request.urlopen = fake_urlopen
for trial in range(3):
    barrier.reset()
    llm = agent.LLM(agent.MODEL, 1.0, time.time() + 3600)
    results = [None] * 4

    def work(i):
        results[i] = llm.ask("hi", conv=[{"role": "system", "content": "s"}])[0]

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print("trial", trial, "replies", results, "url_i", llm.url_i, "bad", llm.bad_urls, "exhausted", llm.exhausted)
