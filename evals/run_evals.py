"""Which model for which agent? Small, labelled evaluations, run against every candidate model in Microsoft Foundry.

Each background agent has one job, so each is tested on that job, with the instructions and structured replies
it uses in the product:

* Referee: does a line from the viewer earn a yellow card? 22 lines in English, Mandarin and Spanish, banter and
  abuse mixed. Azure AI Content Safety is scored alongside as a baseline.
* Memory (the Ammo agent's bet book): from a stretch of conversation, are bets noted two-way and predictions kept
  apart, without inventing either?
* Match agent: from the match so far, is the storyline one short line with every claim true to the facts? Judged
  by gpt-5.5, a stronger model than any it grades.

    python evals/run_evals.py            # every role, every candidate (a few pence)
    python evals/run_evals.py referee    # one role

Results are written to evals/RESULTS.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kickoff.agents.llm import azure_v1_base_url, load_env, options_for  # noqa: E402
from kickoff.rival_agents import RivalAgents  # noqa: E402

HERE = Path(__file__).parent
CANDIDATES = {
    "referee": ["gpt-5.4-nano", "gpt-5.4-mini", "Phi-4", "Phi-4-mini-instruct", "Phi-4-reasoning"],
    "memory": ["gpt-5.4-nano", "gpt-5.4-mini", "Phi-4", "Phi-4-mini-instruct", "Phi-4-reasoning"],
    "match": ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.5", "Phi-4", "Phi-4-mini-instruct", "Phi-4-reasoning"],
}
JUDGE = "gpt-5.5"
CALL_TIMEOUT_S = 30


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / "data" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


class Clients:
    """One chat client per model, on Foundry's OpenAI-compatible endpoint."""

    def __init__(self):
        load_env()
        self.base = azure_v1_base_url(os.environ["AZURE_OPENAI_ENDPOINT"])
        self.key = os.environ["AZURE_OPENAI_API_KEY"]
        self.cache: dict[str, OpenAIChatCompletionClient] = {}

    def __call__(self, model: str) -> OpenAIChatCompletionClient:
        if model not in self.cache:
            client = OpenAIChatCompletionClient(model=model, api_key=self.key, base_url=self.base)
            client.kickoff_model = model
            self.cache[model] = client
        return self.cache[model]


async def timed(coro):
    start = time.monotonic()
    try:  # a live product can't wait longer than this for an agent, so neither does the test
        return await asyncio.wait_for(coro, CALL_TIMEOUT_S), (time.monotonic() - start) * 1000, None
    except Exception as exc:  # a model that can't do the job counts as a miss, not a crash
        return None, (time.monotonic() - start) * 1000, f"{type(exc).__name__}: {str(exc)[:120]}"


# -- the Referee ------------------------------------------------------------------------------------------------

async def referee(clients: Clients, model: str) -> dict:
    agents = RivalAgents(lambda role: clients(model))
    hits, times, errors = 0, [], 0
    cases = load("referee")
    for case in cases:
        booking, ms, err = await timed(agents.booking(case["said"]))
        times.append(ms)
        errors += bool(err)
        hits += booking is not None and booking.card == case["card"]
    return {"score": hits / len(cases), "detail": f"{hits}/{len(cases)} right", "ms": statistics.median(times),
            "errors": errors}


async def content_safety(clients: Clients) -> dict:
    """The baseline: Azure AI Content Safety alone, a card for anything it rates Low or worse."""
    url = (f"https://{os.environ['AZURE_OPENAI_ENDPOINT'].split('//')[1].split('.')[0]}.cognitiveservices.azure.com"
           "/contentsafety/text:analyze?api-version=2024-09-01")
    hits, times = 0, []
    cases = load("referee")
    async with httpx.AsyncClient(timeout=20) as http:
        for case in cases:
            start = time.monotonic()
            r = await http.post(url, headers={"Ocp-Apim-Subscription-Key": clients.key},
                                json={"text": case["said"], "outputType": "FourSeverityLevels"})
            times.append((time.monotonic() - start) * 1000)
            worst = max((c["severity"] for c in r.json().get("categoriesAnalysis", [])), default=0)
            hits += (worst >= 2) == case["card"]
    return {"score": hits / len(cases), "detail": f"{hits}/{len(cases)} right", "ms": statistics.median(times),
            "errors": 0}


# -- Memory: the bet book --------------------------------------------------------------------------------------

def bets_ok(pacts: list[str], expect: dict) -> bool:
    bets = [p for p in pacts if p.lower().startswith("bet")]
    predictions = [p for p in pacts if p.lower().startswith("prediction")]
    if len(bets) != expect["bets"] or len(predictions) != expect["predictions"]:
        return False
    # Two-way: the bet says what happens to each of them.
    return all("the user" in b.lower() and "the rival" in b.lower() for b in bets)


async def memory(clients: Clients, model: str) -> dict:
    agents = RivalAgents(lambda role: clients(model))
    hits, times, errors = 0, [], 0
    cases = load("memory")
    for case in cases:
        pacts, ms, err = await timed(agents.pacts([], case["conversation"], case["sides"]))
        times.append(ms)
        errors += bool(err)
        hits += pacts is not None and bets_ok(pacts, case["expect"])
    return {"score": hits / len(cases), "detail": f"{hits}/{len(cases)} right", "ms": statistics.median(times),
            "errors": errors}


# -- the Match agent -------------------------------------------------------------------------------------------

class Grade(BaseModel):
    all_claims_true: bool
    problem: str = ""


JUDGE_INSTRUCTIONS = """You check a football storyline against the facts it was written from. The brief has the facts; the storyline is one line a fan would say. Set all_claims_true to true only if every claim in the storyline is supported by the facts (who scored, for which club, the score, who has fewer players, who has more possession). Opinion and emphasis are fine; a wrong fact is not. If something is wrong, say what in "problem"."""


async def match(clients: Clients, model: str) -> dict:
    agents = RivalAgents(lambda role: clients(model))
    judge = Agent(clients(JUDGE), JUDGE_INSTRUCTIONS, name="judge",
                  default_options=options_for(clients(JUDGE), {"response_format": Grade, "temperature": 0}))
    hits, times, errors, problems = 0, [], 0, []
    cases = load("match")
    for case in cases:
        story, ms, err = await timed(agents.read_match(case["brief"]))
        times.append(ms)
        errors += bool(err)
        if not story:
            continue
        verdict = (await judge.run(json.dumps({"facts": case["brief"], "storyline": story}))).value
        short = len(story.split()) <= 40
        if isinstance(verdict, Grade) and verdict.all_claims_true and short:
            hits += 1
        else:
            problems.append(verdict.problem if isinstance(verdict, Grade) and not verdict.all_claims_true else "too long")
    return {"score": hits / len(cases), "detail": f"{hits}/{len(cases)} true and short"
            + (f" ({problems[0][:70]})" if problems else ""), "ms": statistics.median(times), "errors": errors}


ROLES = {"referee": referee, "memory": memory, "match": match}
TITLES = {"referee": "Referee: yellow card or banter?", "memory": "Memory: bets noted two-way, predictions apart",
          "match": "Match agent: storyline true to the facts, in one line"}


async def main(roles: list[str]) -> None:
    clients = Clients()
    out = ["# Which model for which agent", "",
           "Each agent was tested on its own job with the instructions it uses in the product "
           "(`evals/run_evals.py`, data in `evals/data/`). Speed is the median time per call.", ""]
    for role in roles:
        print(f"== {role}")
        out += [f"## {TITLES[role]}", "", "| Model | Score | Median time | Notes |", "|---|---|---|---|"]
        rows = []
        if role == "referee":
            rows.append(("Azure AI Content Safety (alone)", await content_safety(clients)))
        for model in CANDIDATES[role]:
            rows.append((model, await ROLES[role](clients, model)))
        for name, r in rows:
            print(f"   {name:32s} {r['score']:.0%}  {r['ms']:.0f}ms  {r['detail']}  errors={r['errors']}")
            note = r["detail"] + (f"; {r['errors']} failed to answer" if r["errors"] else "")
            out.append(f"| {name} | {r['score']:.0%} | {r['ms'] / 1000:.1f}s | {note} |")
        out.append("")
    (HERE / "RESULTS.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1:] or list(ROLES)))
