"""Run magicpin's official judge_simulator.py scenarios against the bot WITHOUT an LLM key.

A stub LLM replaces the scorer, so numeric scores here are meaningless — this checks
the HTTP flow + the scripted pass/fail logic (auto-reply, intent transition, hostile).
For real scores, run judge_simulator.py itself with your API key.

Usage: python tests/run_judge_stub.py /path/to/magicpin-ai-challenge [scenario]
"""
import json
import sys
from pathlib import Path

pkg = Path(sys.argv[1] if len(sys.argv) > 1 else "../challenge").resolve()
scenario = sys.argv[2] if len(sys.argv) > 2 else "all"
sys.path.insert(0, str(pkg))
import judge_simulator as js  # noqa: E402


class StubLLM(js.LLMProvider):
    def name(self):
        return "stub"

    def complete(self, prompt, system=None):
        return json.dumps({k: 5 for k in ["specificity", "category_fit", "merchant_fit", "decision_quality", "engagement_compulsion"]})


js.BOT_URL = "http://localhost:8080"
judge = js.JudgeSimulator(StubLLM())
judge.client = js.BotClient(js.BOT_URL)
ok = judge.run(scenario)
print("\nRESULT:", "OK" if ok else "FAILED")
