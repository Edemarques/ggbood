import importlib.util, io, json, os, sys, threading, time, urllib.error

path = os.path.join(os.path.dirname(__file__), "..", "..", "codexv2.12_orig.py")
os.environ["OPENROUTER_API_KEY"] = "test"
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
    payload = json.loads(req.data)
    if payload["model"] == agent.MODEL:
        try:
            barrier.wait(timeout=2)   # all four writers have sent to the primary model
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.01 * threading.get_ident() % 7 / 100)
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(
            b'{"error":{"message":"%s is not a valid model ID"}}' % agent.MODEL.encode()))
    body = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"cost": 0.001}, "model": payload["model"]}
    return Resp(json.dumps(body).encode())


agent.urllib.request.urlopen = fake_urlopen
for trial in range(5):
    barrier.reset()
    llm = agent.LLM(agent.MODEL, 1.0, time.time() + 3600)
    results = [None] * 4

    def work(i):
        results[i] = llm.ask("hi", conv=[{"role": "system", "content": "s"}])[0]

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print("trial", trial, "replies", results, "model_i", llm.model_i, "exhausted", llm.exhausted,
          "affordable", llm.affordable())
