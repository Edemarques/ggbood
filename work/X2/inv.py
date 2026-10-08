import ast, re, sys, collections
P = '/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py'
src = open(P, encoding='utf-8').read()
tree = ast.parse(src)
words = collections.Counter(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', src))
print("== top-level names with word count <=1 (or only def) ==")
for node in tree.body:
    names = []
    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
        names = [node.name]
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name): names.append(n.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            names.append((a.asname or a.name).split('.')[0])
    for n in names:
        if words[n] <= 1:
            print(node.lineno, n, words[n])
print("== methods of classes with count<=1 ==")
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        for b in node.body:
            if isinstance(b, ast.FunctionDef) and not b.name.startswith('__'):
                if words[b.name] <= 1:
                    print(node.name, b.lineno, b.name, words[b.name])
print("== self attrs: stores vs loads ==")
stores = collections.defaultdict(list); loads = collections.Counter()
for n in ast.walk(tree):
    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ('self','run','r','R'):
        if isinstance(n.ctx, ast.Store): stores[n.attr].append(n.lineno)
        else: loads[n.attr] += 1
# also count any attribute load anywhere with that name
anyload = collections.Counter()
for n in ast.walk(tree):
    if isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Store):
        anyload[n.attr] += 1
for a, ls in sorted(stores.items()):
    if anyload[a] == 0:
        strs = len(re.findall(r'["\']%s["\']' % a, src))
        print('attr', a, ls, 'getattr-str', strs)
print("== nested function defs never referenced ==")
for n in ast.walk(tree):
    if isinstance(n, ast.FunctionDef):
        for b in ast.walk(n):
            if b is not n and isinstance(b, ast.FunctionDef) and words[b.name] <= 1:
                print('nested', b.lineno, b.name)
print("== local assigned-but-unused names in functions ==")
for f in ast.walk(tree):
    if isinstance(f, (ast.FunctionDef,)):
        st = collections.defaultdict(list); ld = set()
        for b in ast.walk(f):
            if isinstance(b, ast.Name):
                if isinstance(b.ctx, ast.Store): st[b.id].append(b.lineno)
                else: ld.add(b.id)
        # nonlocal/global
        for b in ast.walk(f):
            if isinstance(b, (ast.Nonlocal, ast.Global)):
                ld.update(b.names)
        for k, v in st.items():
            if k not in ld and k != '_' and not k.startswith('_'):
                print('unusedlocal', f.name, f.lineno, k, v)
print("== if True / if False / constant tests ==")
for n in ast.walk(tree):
    if isinstance(n, (ast.If, ast.While)) and isinstance(n.test, ast.Constant):
        print('consttest', n.lineno, n.test.value)
print("== statements after agent_main def / module level non-def ==")
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
        print('mod', node.lineno, ast.unparse(node)[:100].replace('\n',' '))
