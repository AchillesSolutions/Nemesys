# Choosing a model for each agent

Nemesys has seven agents, and they do very different jobs. Some run once a match and can take their time; some run on
every line the viewer says and must be fast; one must not get a fact wrong. So instead of one model for everything,
each agent was tested on its own job, with the exact instructions and structured replies it uses in the product,
against the candidate models deployed in Microsoft Foundry. `run_evals.py` runs the tests; the latest numbers are
in [RESULTS.md](RESULTS.md).

## What we chose, and why

| Agent | Job | Model | Why |
|---|---|---|---|
| Lobby Host | Language, name, club, rival | Azure AI Speech, **no LLM** | A fixed conversation: rules are instant and can't go off script. Multilingual voice (Ada) and speech recognition that hears English, Mandarin and Spanish. |
| News Agent | Writes the fortnight's club news before kick-off | **gpt-5.5** | Runs once per match, so quality wins over speed. |
| Match Agent | Reads the stats into a storyline; writes the full-time report | **gpt-5.5** | Must never state a wrong fact: gpt-5.5 and gpt-5.4-mini got all three test situations right, gpt-5.4-nano only one (it miscounted Harbour City's chances). gpt-5.5 reads best, at the same speed. |
| Ammo Agent | Picks the next dig | **gpt-5.4-mini** | Wit and judgement, quick enough for a lull in conversation. |
| Memory (Ammo's bet book) | Notes bets two-way and predictions apart | **gpt-5.4-nano** | 5/5, against 4/5 for gpt-5.4-mini: a small, cheap model is best at this structured extraction. |
| Referee | Yellow card or banter, on every line the viewer says | **Azure AI Content Safety, then gpt-5.4-mini** | Content Safety alone got 13/22: it rated banter ("your keeper's useless") the same as abuse and missed Mandarin swearing. So it is the fast first gate (anything Medium or worse is a card at once), and gpt-5.4-mini judges the rest: 22/22 at 0.9s, against 21/22 at 1.5s for gpt-5.4-nano. |
| The Rival | The voice | **GPT-Live** | The only model that listens and talks at the same time (full duplex), steered live by the other agents. |
| Memory Keeper | After the match: what to remember about the viewer next time | **Phi-4** (Microsoft) | No one is waiting for it, so speed doesn't matter; it's a small, private summarising job, the kind that could run on the viewer's own device. Asked for plain JSON, it picked out the right things from a real session in about 3 seconds ("called the stoppage-time winner", "touchy about VAR decisions"). |

## Microsoft's own models

Phi-4, Phi-4-mini-instruct and Phi-4-reasoning were all deployed and tested on the live agents' jobs. None suits
them, for a reason worth knowing: the live agents rely on structured replies checked against a schema (so a
malformed answer can never reach the voice), and on Foundry the Phi models don't accept that contract ("Response
format was json_schema but must be..."), so every call is refused. Phi-4-reasoning does answer, but reasons at
length first (often past the 30-second limit a live conversation allows), and its match reads got the facts wrong.

So Phi-4 has the job that suits it: the **Memory Keeper**, which runs after the final whistle with nobody waiting,
and is simply asked for plain JSON, which it does well.

## Running the tests

    python evals/run_evals.py            # every agent, every candidate: a few pence
    python evals/run_evals.py referee    # one agent

Needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` for a Foundry resource with the candidate models deployed.
