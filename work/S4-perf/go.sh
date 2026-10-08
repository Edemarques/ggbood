cd ~
W=/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/work/S4-perf
python3 $W/patched.py
export PATCHED=1
python3 $W/keep.py 2>&1 | tail -3 | cut -c1-400
python3 $W/mend.py 2>&1 | tail -4 | cut -c1-500
python3 $W/bench2.py 150 2>&1 | grep -E "sequential|run |parallel"
