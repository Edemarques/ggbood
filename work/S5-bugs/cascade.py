import importlib.util, os, sys, shutil, tempfile, subprocess
path = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
conf = agent.CONFTEST_SRC.replace("TIME_LIMIT_SECONDS = 10", "TIME_LIMIT_SECONDS = 1")
d = tempfile.mkdtemp()
t = os.path.join(d, "tests")
os.makedirs(t)
open(os.path.join(t, "conftest.py"), "w").write(conf)
open(os.path.join(t, "test_a.py"), "w").write(
    "import time\n"
    "def test_1():\n    assert True\n"
    "def test_2():\n    time.sleep(2)\n"
    "def test_3():\n    assert True\n"
    "def test_4():\n    assert True\n")
open(os.path.join(t, "test_b.py"), "w").write("def test_5():\n    assert True\n")
j = os.path.join(d, "j.xml")
r = subprocess.run([sys.executable, "-m", "pytest", t, "-q", "-p", "no:cacheprovider", "-c", os.devnull,
                    "--rootdir=" + d, "--junitxml=" + j], capture_output=True, text=True)
print("rc", r.returncode)
print(r.stdout[-1500:])
import xml.etree.ElementTree as ET
for tc in ET.parse(j).getroot().iter("testcase"):
    print(tc.get("classname"), tc.get("name"), [(c.tag, (c.get("message") or "")[:90]) for c in tc])
shutil.rmtree(d)
