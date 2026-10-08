import subprocess, time, sys, os, statistics
def t(args, n=15):
    xs = []
    for _ in range(n):
        s = time.perf_counter(); subprocess.run(args, check=False, capture_output=True); xs.append(time.perf_counter() - s)
    return statistics.median(xs) * 1000
py = sys.executable
print("bare python -c pass   %.1f ms" % t([py, "-c", "pass"]))
print("import std set w/o urllib %.1f ms" % t([py, "-c", "import ast, copy, io, json, math, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, tokenize, traceback, types"]))
print("same + urllib.request %.1f ms" % t([py, "-c", "import ast, copy, io, json, math, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, tokenize, traceback, types, urllib.error, urllib.request"]))
