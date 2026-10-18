"""Turn a task's reference pytest suite into agent case files (a realistic-size scripted draft).

Each test becomes `case_<name>` returning a list of observations: the truth of each assert, and for each
`pytest.raises` block the name of the exception class raised (or "no exception"). Parametrized tests loop
over their parameter values. Tests that need fixtures other than tmp_path are left out.

usage: ref_to_cases.py SOLVE_SH OUT_CASES_PY
"""
import ast
import re
import sys


def suite_source(solve):
    text = open(solve).read()
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
    return m.group(1)


class Rewrite(ast.NodeTransformer):
    def visit_Assert(self, node):
        call = ast.parse("_obs.append(bool(X))").body[0]
        call.value.args[0].args[0] = node.test
        return ast.copy_location(call, node)

    def visit_With(self, node):
        self.generic_visit(node)
        item = node.items[0].context_expr if len(node.items) == 1 else None
        if (isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "raises"
                and isinstance(item.func.value, ast.Name) and item.func.value.id == "pytest"):
            exc = item.args[0]
            tmpl = ast.parse(
                "try:\n    pass\n    _obs.append('no exception')\nexcept BaseException as _e:\n"
                "    _obs.append(type(_e).__name__ if isinstance(_e, EXC) else 'other:' + type(_e).__name__)").body[0]
            tmpl.body = node.body + [tmpl.body[1]]
            handler = tmpl.handlers[0]
            handler.body[0].value.args[0].test.args[1] = exc
            return ast.copy_location(tmpl, node)
        return node


def convert(src):
    tree = ast.parse(src)
    out = []
    simple = {}
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and any("fixture" in ast.unparse(d) for d in node.decorator_list)
                and not node.args.args and len(node.body) == 1 and isinstance(node.body[0], ast.Return)):
            simple[node.name] = node.body[0].value
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            params = None
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and "parametrize" in ast.unparse(d.func):
                    names = d.args[0].value if isinstance(d.args[0], ast.Constant) else None
                    if names is None:
                        params = "skip"
                        break
                    params = ([n.strip() for n in names.split(",") if n.strip()], d.args[1])
            if params == "skip":
                continue
            argnames = [a.arg for a in node.args.args]
            loopvars = params[0] if params else []
            fixtures = [a for a in argnames if a not in loopvars]
            inline = [f for f in fixtures if f in simple]
            fixtures = [f for f in fixtures if f not in simple]
            if any(f != "tmp_path" for f in fixtures):
                continue
            body = [Rewrite().visit(s) for s in node.body]
            body = [ast.fix_missing_locations(s) for s in body]
            if params:
                target = (ast.Name(loopvars[0], ast.Store()) if len(loopvars) == 1 else
                          ast.Tuple([ast.Name(n, ast.Store()) for n in loopvars], ast.Store()))
                body = [ast.For(target=target, iter=params[1], body=body, orelse=[])]
            body = [ast.Assign(targets=[ast.Name(f, ast.Store())], value=simple[f], lineno=0) for f in inline] + body
            fn = ast.FunctionDef(
                name="case_" + node.name[5:],
                args=ast.arguments(posonlyargs=[], args=[ast.arg(a) for a in fixtures], kwonlyargs=[],
                                   kw_defaults=[], defaults=[]),
                body=[ast.parse("_obs = []").body[0]] + body + [ast.parse("return _obs").body[0]],
                decorator_list=[], returns=None, type_params=[])
            out.append(fn)
        elif isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef,
                               ast.ClassDef)):
            if isinstance(node, ast.FunctionDef) and any("fixture" in ast.unparse(d) for d in node.decorator_list):
                continue
            out.append(node)
    mod = ast.Module(body=out, type_ignores=[])
    return ast.unparse(ast.fix_missing_locations(mod)) + "\n"


if __name__ == "__main__":
    open(sys.argv[2], "w").write(convert(suite_source(sys.argv[1])))
