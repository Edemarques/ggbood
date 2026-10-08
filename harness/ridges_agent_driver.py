"""Calls an agent's agent_main() with a task statement and writes the returned patch.

usage: ridges_agent_driver.py AGENT.py STATEMENT.md PATCH_OUT

With TG_PRICE_AS=<model in the agent's PRICES>, every model name the agent has no price for (an alias such as
personal/gpt-6-luna, or the name the endpoint reports back) is billed at that model's price, so the agent's
budget accounting matches the model actually behind the alias.
"""
import importlib.util
import sys

agent_path, statement_path, patch_path = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("agent", agent_path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

import os  # noqa: E402

alias = os.environ.get("TG_PRICE_AS")
if alias and alias in agent.PRICES:
    class _Prices(dict):
        def __contains__(self, key):
            return bool(key) and isinstance(key, str) or dict.__contains__(self, key)

        def get(self, key, default=None):
            return dict.get(self, key, dict.get(self, alias))

    agent.PRICES = _Prices(agent.PRICES)

with open(statement_path) as fh:
    statement = fh.read()
patch = agent.agent_main({"problem_statement": statement})
with open(patch_path, "w") as fh:
    fh.write(patch or "")
