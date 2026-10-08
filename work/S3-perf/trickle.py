import http.server, threading, time, urllib.request, socketserver

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b" " * 12 + b'{"ok": 1}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(12):
            self.wfile.write(b" "); self.wfile.flush(); time.sleep(0.5)
        self.wfile.write(b'{"ok": 1}'); self.wfile.flush()
    def log_message(self, *a): pass

srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % srv.server_address[1]

def plain():
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST"), timeout=2) as resp:
        data = resp.read()
    print("plain read in thread: %.1fs (timeout=2) -> %r" % (time.time() - t0, data[-9:]))

def bounded():
    t0 = time.time(); limit = 2
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST"), timeout=2) as resp:
            chunks = []
            while True:
                c = resp.read1(65536)
                if not c:
                    break
                chunks.append(c)
                if time.time() - t0 > limit:
                    raise TimeoutError("total %.0f s" % limit)
        print("bounded ok")
    except TimeoutError as e:
        print("bounded read raised after %.1fs: %s" % (time.time() - t0, e))

for f in (plain, bounded):
    t = threading.Thread(target=f); t.start(); t.join()
