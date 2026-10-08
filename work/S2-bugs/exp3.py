import sys, types
print(sys.version)
src = '''
X: int = 1
def f(x):
    return x + 1
Y: int = 2

class C:
    a: int = 1
    def m(self):
        return 2
    b: int = 3

def g(x: int) -> int: return x + 1
'''
code = compile(src, "m.py", "exec", dont_inherit=True)
def show(c, ind=0):
    for k in c.co_consts:
        if isinstance(k, types.CodeType):
            lines = [l for _, _, l in k.co_lines() if l is not None]
            print(" " * ind, k.co_name, k.co_firstlineno, (min(lines), max(lines)) if lines else None)
            show(k, ind + 2)
show(code)
