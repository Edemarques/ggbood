"""Deterministic stand-in for the model in the codexv2.12_ lineage (one conversation per lane, LLM._attempts(max_tokens)).

Replies depend only on what is asked and on which conversation asks:
  * the layout question -> JSON naming the repository from the statement;
  * the first draft request -> the main conversation writes W1_DRAFT, lane 1 W2_DRAFT, other lanes DONE;
  * the k-th mutation round of a conversation -> that conversation's k-th round reply (then DONE);
  * anything else (feedback, coverage) -> DONE.
The case files come from fake_llm.py, including its deliberately broken blocks.
"""
import json
import re
import threading
import time

import fake_llm

DRAFTS = [fake_llm.W1_DRAFT, fake_llm.W2_DRAFT]
ROUNDS = [[fake_llm.W1_ROUND1, fake_llm.W1_ROUND2], [fake_llm.W2_ROUND1, fake_llm.W2_ROUND2]]


class FakeLanesLLM:
    COST_PER_CALL = 0.002

    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.Lock()
        self.order = {}        # id(llm) -> conversation number, in order of first draft request
        self.rounds = {}       # id(llm) -> mutation rounds answered
        self.asks = []

    def conversation(self, llm, first):
        with self.lock:
            if id(llm) not in self.order and first:
                # the main conversation is the one Run keeps as _llm_main; lanes come after it
                self.order[id(llm)] = len(self.order)
            return self.order.get(id(llm))

    def reply_for(self, llm):
        text = llm.messages[-1]["content"] if llm.messages else ""
        if text.startswith("Describe the requested runtime layout"):
            m = re.search(r"repository at `(/[^`]+)`", text)
            return "layout", json.dumps({"repo": m.group(1) if m else None, "tests": "tests/", "suite_limit": 60,
                                         "package_roots": ["minilib"]})
        if text.startswith("Select the repository modules"):
            return "select", json.dumps({"candidates": [0]})
        if "Write the regression cases for the scope described in the task above" in text:
            n = self.conversation(llm, True)
            return "draft%s" % n, DRAFTS[n] if n is not None and n < len(DRAFTS) else "DONE"
        if "Mutation analysis of the current suite" in text:
            n = self.conversation(llm, False)
            with self.lock:
                k = self.rounds.get(id(llm), 0)
                self.rounds[id(llm)] = k + 1
            replies = ROUNDS[n] if n is not None and n < len(ROUNDS) else []
            if k < len(replies):
                ids = re.findall(r"\[(m\d+)\]", text)
                skip = "SKIP: %s (equivalent for the requested behaviour)\n\n" % ids[-1] if ids else ""
                return "round%s.%d" % (n, k), skip + replies[k]
            return "round%s.%d" % (n, k), "DONE"
        return "other", "DONE"

    def install(self, run):
        fake = self
        agent = self.agent
        main = run._llm_main

        def _attempts(llm, max_tokens):
            if llm.deadline - time.time() < 30:
                return None, None
            if llm is main:
                fake.conversation(llm, True)
            kind, reply = fake.reply_for(llm)
            cost = fake.COST_PER_CALL
            llm.spent += cost
            llm.last_call_cost = cost
            llm.calls += 1
            with fake.lock:
                fake.asks.append(kind)
            agent.log("[LLM] call %d: fake %s, out=%d chars cost=$%.4f total=$%.4f" % (
                llm.calls, kind, len(reply), cost, llm.spent))
            return reply, "stop"

        agent.LLM._attempts = _attempts
        return self
