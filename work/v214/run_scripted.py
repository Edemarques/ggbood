"""Offline run of an agent on a ridges task with scripted LLM replies (draft only), then compare verdicts."""
import importlib.util, io, json, os, re, shutil, subprocess, sys, time, contextlib
agent_path, task, cases_file, out = sys.argv[1:5]
BENCH="/home/user/ridgesai/ridges-bench/test-generation"; ROOT="/opt/tg"
shutil.rmtree(out, ignore_errors=True); os.makedirs(out); os.chmod(out,0o755)
app=os.path.join(out,"app"); shutil.copytree(os.path.join(ROOT,task,"repo"),app,symlinks=True)
subprocess.run(["chmod","-R","a+rX",out])
st=open(os.path.join(BENCH,task,"instruction.md")).read().replace("/app",app)
os.environ.update({"AGENT_TIMEOUT":os.environ.get("AGENT_TIMEOUT","600"),"OPENROUTER_API_KEY":"fake","PYTHONPATH":app,"TG_DEBUG_DIR":os.path.join(out,"debug")})
os.chdir(app)
spec=importlib.util.spec_from_file_location("agent_under_test",agent_path); A=importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
cases=open(cases_file).read()
DRAFT="```python cases_main.py\n"+cases+"\n```\n"
T0=time.time()
run=A.Run(st)
def _attempts(llm, max_tokens, messages, effort=""):
    text=messages[-1]["content"] if messages else ""
    w=next((i for i,c in enumerate(run.writers or []) if c is messages),None)
    k=sum(1 for m in messages if m.get("role")=="user")-1
    reply = DRAFT if (w==0 and k==0) else "DONE\n"
    with llm.lock:
        llm.spent+=0.001; llm.last_call_cost=0.001; llm.calls+=1
    return reply,"stop"
A.LLM._attempts=_attempts
run.execute(); patch=run.finalize()
open(os.path.join(out,"patch.diff"),"w").write(patch)
# agent verdicts
res={"stats":run.mutation_stats(),"mutants":[]}
for m in run.mutants.values():
    res["mutants"].append({"id":m["id"],"file":m["file"],"line":m["line"],"kind":m["kind"],"start":m["start"],"end":m["end"],"repl":m["repl"].decode(),"status":run.mutant_status.get(m["id"])})
json.dump(res,open(os.path.join(out,"agent_mutants.json"),"w"),indent=0)
res["unjudged"]=sum(1 for m in run.mutants.values() if run.mutant_status.get(m["id"]) not in ("killed","survived","no-case"))
res["fast_stats"]=run.fast_stats; res["secs"]=round(time.time()-T0,1)
res["tests"]=len(re.findall(r"(?m)^\+def test_", patch))
json.dump(res,open(os.path.join(out,"agent_mutants.json"),"w"),indent=0)
print("STATS",json.dumps({k:v for k,v in res.items() if k!="mutants"}))
