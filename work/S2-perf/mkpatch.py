import sys
src = open(sys.argv[1], encoding="utf-8").read()

def rep(old, new):
    global src
    assert src.count(old) == 1, old
    src = src.replace(old, new)

rep('''            errors.append("%s: %s" % (name, _fmt_exc(e)))
    src_root = os.path.realpath(cfg["src_root"])''',
    '''            errors.append("%s: %s" % (name, _fmt_exc(e)))
    import inspect  # noqa: F401  preloaded once here instead of in every forked check (_run_cases imports them)
    import pathlib  # noqa: F401
    src_root = os.path.realpath(cfg["src_root"])''')

rep('''    while True:
        line = proto_in.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("op") == "quit":
            break
        pl = None
        if req.get("mut"):
            try:
                pl = plan(req["mut"])''',
    '''    compiled_src = {}
    last_plan = [None, None]
    if MODE_FREEZE:
        gc.freeze()
    while True:
        line = proto_in.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("op") == "quit":
            break
        for e in (req.get("expected") or {}).values():
            s = e.get("src") if isinstance(e, dict) else None
            if MODE_SRC and isinstance(s, str) and e.get("status") == "value":
                c = compiled_src.get(s)
                if c is None:
                    try:
                        c = compile(s, "<string>", "eval")
                    except Exception:
                        c = s
                    compiled_src[s] = c
                e["src"] = c
        pl = None
        if req.get("mut"):
            try:
                mkey = json.dumps(req["mut"], sort_keys=True)
                if last_plan[0] != mkey:
                    last_plan[:] = [None, None]
                    last_plan[:] = [mkey, plan(req["mut"])]
                pl = last_plan[1]''')
rep('''_T0 = time.time()''', '''_T0 = time.time()
MODE_FREEZE = os.getenv("X_FREEZE", "1") == "1"
MODE_SRC = os.getenv("X_SRC", "1") == "1"''')
open(sys.argv[2], "w", encoding="utf-8").write(src)
