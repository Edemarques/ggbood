set -e
W=/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58
D=~/tgopt/S2-perf
cp $W/work/S2-perf/e2e.py $W/work/S2-perf/mkpatch.py $D/
cp $W/codexv2.12_orig.py $D/orig.py
cd $D
python3 mkpatch.py orig.py patched.py
python3 -c "import ast;ast.parse(open('patched.py').read())"
for i in 1 2; do
python3 e2e.py $D/orig.py | head -2
python3 e2e.py $D/patched.py X_FREEZE=0 X_SRC=0 | head -2
python3 e2e.py $D/patched.py X_FREEZE=1 X_SRC=0 | head -2
python3 e2e.py $D/patched.py X_FREEZE=0 X_SRC=1 | head -2
python3 e2e.py $D/patched.py | head -2
done
ls out_*
python3 - <<'EOF'
import json, glob
outs = {f: json.load(open(f)) for f in glob.glob("out_*.json")}
ref = outs.pop("out_orig.json")
for f, o in outs.items():
    print(f, "identical" if o == ref else "DIFFERENT")
print("first recs:", ref[0], ref[1], ref[-1])
EOF
