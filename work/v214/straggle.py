"""harness/run_e2e.py with writer 2's draft delayed by DELAY seconds (default 150).

usage: DELAY=400 python work/v214/straggle.py AGENT.py OUT_DIR [AGENT_TIMEOUT_SECONDS]
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "harness"))
import fake_llm, run_e2e
DELAY = float(os.environ.get("DELAY", "150"))
orig = fake_llm.FakeLLM.reply_for
def reply_for(self, messages):
    r = orig(self, messages)
    if r[0] == "writer" and r[1] == 1 and r[2] == 0:
        time.sleep(DELAY)
    return r
fake_llm.FakeLLM.reply_for = reply_for
run_e2e.main()
