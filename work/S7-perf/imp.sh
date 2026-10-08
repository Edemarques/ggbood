set -e
D=~/tgopt/S7-perf
mkdir -p $D
cp "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py" $D/tg_runner.py
cd $D
python3 -c "import py_compile; py_compile.compile('tg_runner.py', cfile='tg_runner.pyc', doraise=True)"
# baseline: interpreter startup only
time (for i in $(seq 20); do python3 -c pass; done)
time (for i in $(seq 20); do python3 tg_runner.pyc; done)
time (for i in $(seq 20); do python3 -c "import urllib.request"; done)
python3 -X importtime tg_runner.pyc 2>&1 | sort -t'|' -k2 -n | tail -12
