import ast, re, sys, collections
P = '/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py'
src = open(P, encoding='utf-8').read()
tree = ast.parse(src)
# code-level references (Name load or Attribute attr load)
refs = collections.Counter()
for n in ast.walk(tree):
    if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store):
        refs[n.id] += 1
    elif isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Store):
        refs[n.attr] += 1
strrefs = collections.Counter()
for n in ast.walk(tree):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        for w in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', n.value):
            strrefs[w] += 1
defs = collections.defaultdict(list)
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        defs[node.name].append(node.lineno)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name): defs[t.id].append(node.lineno)
print('== redefined top-level ==')
for k, v in defs.items():
    if len(v) > 1: print(k, v)
print('== top-level with 0 code refs ==')
for k, v in defs.items():
    if refs[k] == 0: print(k, v, 'strrefs', strrefs[k])
print('== methods with 0 code refs ==')
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        for b in node.body:
            if isinstance(b, ast.FunctionDef) and not b.name.startswith('__'):
                if refs[b.name] == 0:
                    print(node.name, b.lineno, b.name, 'str', strrefs[b.name])
print('== top-level funcs w/ refs count and lines ==')
cnt = 0
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        cnt += 1
print('nfuncs', cnt)
# attrs stored with self.X anywhere, loaded count
stores = collections.defaultdict(list)
for n in ast.walk(tree):
    if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store):
        stores[n.attr].append(n.lineno)
print('== attrs stored but never loaded (code) ==')
for a, ls in sorted(stores.items()):
    if refs[a] == 0:
        print(a, ls, 'str', strrefs[a])
print('== attrs loaded once ==')
for a, ls in sorted(stores.items()):
    if refs[a] == 1:
        print(a, ls[:5])
# function return value usage: for each top-level function, check call sites in Expr statements
print('== functions returning values but called as bare statements ==')
funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
retfuncs = set()
for name, f in funcs.items():
    for b in ast.walk(f):
        if isinstance(b, ast.Return) and b.value is not None and not (isinstance(b.value, ast.Constant) and b.value.value is None):
            retfuncs.add(name); break
bare = collections.Counter(); used = collections.Counter()
parents = {}
for p in ast.walk(tree):
    for c in ast.iter_child_nodes(p): parents[c] = p
for n in ast.walk(tree):
    if isinstance(n, ast.Call):
        fn = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else None)
        if fn in retfuncs:
            if isinstance(parents.get(n), ast.Expr): bare[fn] += 1
            else: used[fn] += 1
for fn in sorted(retfuncs):
    if bare[fn] and not used[fn]:
        print('ALWAYS bare', fn, funcs[fn].lineno, bare[fn])
# tuple return where member always discarded
print('== tuple returns ==')
for name, f in funcs.items():
    sizes = set()
    for b in ast.walk(f):
        if isinstance(b, ast.Return) and isinstance(b.value, ast.Tuple):
            sizes.add(len(b.value.elts))
    if sizes:
        sites = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else None)
                if fn == name:
                    p = parents.get(n)
                    if isinstance(p, ast.Assign):
                        sites.append(ast.unparse(p.targets[0]))
                    else:
                        sites.append('<%s>' % type(p).__name__)
        print(name, f.lineno, sizes, sites)
