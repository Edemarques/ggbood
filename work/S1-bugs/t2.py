import os, sys, subprocess, glob, json
base = os.path.expanduser("~/tgopt/S1-bugs")
d = sorted(glob.glob(base + "/tmp*"), key=os.path.getmtime)[-1]
site = os.path.join(d, "site"); absent = os.path.join(d, "absent"); tdir = os.path.join(d, "tests")
open(os.path.join(site, "fakeplug.py"), "w").write("def pytest_configure(config):\n    print('PLUGIN LOADED')\n")
print("env autoload:", os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"))
for pp in (site, absent + os.pathsep + site):
    env = dict(os.environ, PYTHONPATH=pp)
    r = subprocess.run([sys.executable, "-m", "pytest", tdir, "-q", "-s", "-p", "no:cacheprovider", "-c", os.devnull], env=env, capture_output=True, text=True)
    print(pp[-30:], r.returncode, r.stdout[-400:], r.stderr[-800:])
import pytest; print(pytest.__version__)
