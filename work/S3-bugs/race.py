import importlib.util, os, sys, threading, json, io, time, urllib.error
os.environ["OPENROUTER_API_KEY"] = "test"
os.environ["SANDBOX_PROXY_URL"] = "http://proxy.invalid"
path = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.log = lambda s: print(s, flush=True)

class Resp:
    def __init__(self, b): self.b = b
    def read(self, *a): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False

def scenario(name, decide):
    barrier = threading.Barrier(4)
    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data)
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.05)
        return decide(req.full_url, body["model"])
    agent.urllib.request.urlopen = fake_urlopen
    llm = agent.LLM(agent.MODEL, 1.0, time.time() + 1000)
    res = [None] * 4
    def w(i):
        res[i] = llm.ask("hi", conv=[{"role": "system", "content": "s"}])[0]
    ts = [threading.Thread(target=w, args=(i,)) for i in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
    print("==", name, "replies:", res, "exhausted:", llm.exhausted, "bad_urls:", llm.bad_urls, "model:", llm.model)

ok = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"cost": 0.001}}).encode()

def model_refused(url, model):
    if model == agent.MODEL:
        raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b'{"error":{"message":"%s is not a valid model ID"}}' % model.encode()))
    return Resp(ok)

def url_404(url, model):
    if "proxy.invalid" in url:
        raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"Not Found"))
    return Resp(ok)

scenario("primary model refused", model_refused)
scenario("proxy url 404", url_404)
