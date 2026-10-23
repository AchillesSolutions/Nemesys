# Choosing a model for each agent

Nemesys has six agents, and they do very different jobs. Some run once a match and can take their time; some run on
every line the viewer says and must be fast; one must not get a fact wrong. So instead of one model for everything,
each agent was tested on its own job, with the exact instructions and structured replies it uses in the product,
against the candidate models deployed in Microsoft Foundry. `run_evals.py` runs the tests; the latest numbers are
in [RESULTS.md](RESULTS.md).

## What we chose, and why

| Agent | Job | Model | Why |
|---|---|---|---|
| Lobby Host | Language, name, club, rival | Azure AI Speech, **no LLM** | A fixed conversation: rules are instant and can't go off script. Multilingual voice (Ada) and speech recognition that hears English, Mandarin and Spanish. |
| News Agent | Writes the fortnight's club news before kick-off | **gpt-5.5** | Runs once per match, so quality wins over speed. |
| Match Agent | Reads the stats into a storyline; writes the full-time report | **gpt-5.5** | Must never state a wrong fact. All three OpenAI models passed the three test situations, but in an earlier probe gpt-5.4-nano said the wrong side was down to ten men; gpt-5.5 was correct and reads best, at the same speed. |
| Ammo Agent | Picks the next dig | **gpt-5.4-mini** | Wit and judgement, quick enough for a lull in conversation. |
| Memory (Ammo's bet book) | Notes bets two-way and predictions apart | **gpt-5.4-nano** | 5/5, against 4/5 for gpt-5.4-mini: a small, cheap model is best at this structured extraction. |
| Referee | Yellow card or banter, on every line the viewer says | **Azure AI Content Safety, then gpt-5.4-mini** | Content Safety alone got 13/22: it rated banter ("your keeper's useless") the same as abuse and missed Mandarin swearing. So it is the fast first gate (anything Medium or worse is a card at once), and gpt-5.4-mini judges the rest: 22/22, at 0.9s, faster than gpt-5.4-nano (1.7s) with the same accuracy. |
| The Rival | The voice | **GPT-Live** | The only model that listens and talks at the same time (full duplex), steered live by the other agents. |

## Microsoft's own models

Phi-4-reasoning and Phi-4 were both deployed and tested, because a Microsoft model would have been a natural choice
in a Microsoft hackathon. Neither suited these real-time jobs: Phi-4-reasoning reasons at length before answering,
so most calls ran past the 30-second limit a live conversation can allow (and its one completed match read put a
goal to the wrong club); Phi-4 gave no structured reply within the limit on a probe call.

## Running the tests

    python evals/run_evals.py            # every agent, every candidate: a few pence
    python evals/run_evals.py referee    # one agent

Needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` for a Foundry resource with the candidate models deployed.
