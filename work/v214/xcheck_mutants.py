"""For each agent mutant, apply it to a copy of the library and run the final suite with pytest; compare to agent verdict."""
import json, os, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
out, py = sys.argv[1], sys.argv[2]
app = os.path.join(out, "app")
data = json.load(open(os.path.join(out, "agent_mutants.json")))
patch = open(os.path.join(out, "patch.diff")).read()
base = tempfile.mkdtemp(prefix="xc-")
clean = os.path.join(base, "clean"); shutil.copytree(app, clean, symlinks=True, ignore=shutil.ignore_patterns(".git"))
shutil.rmtree(os.path.join(clean, "regression_tests"), ignore_errors=True)
subprocess.run(["git", "init", "-q"], cwd=clean); subprocess.run(["git", "apply", "-"], cwd=clean, input=patch.encode(), check=True)
def one(m):
    d = tempfile.mkdtemp(dir=base); w = os.path.join(d, "w"); shutil.copytree(clean, w, symlinks=True)
    f = os.path.join(w, m["file"]); src = open(f, "rb").read()
    open(f, "wb").write(src[:m["start"]] + m["repl"].encode() + src[m["end"]:])
    try:
        p = subprocess.run([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "regression_tests"], cwd=w,
                           env=dict(os.environ, PYTHONPATH=w, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1"), capture_output=True, timeout=120)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    shutil.rmtree(d, ignore_errors=True)
    return m, rc
mism = []
with ThreadPoolExecutor(8) as ex:
    for m, rc in ex.map(one, data["mutants"]):
        real = "killed" if rc != 0 else "survived"
        st = m["status"]
        if st in ("killed", "survived") and st != real:
            mism.append((m["id"], m["file"], m["line"], m["kind"], st, real, rc, m["repl"][:60]))
print("total", len(data["mutants"]), "mismatches", len(mism))
for x in mism: print(x)
