# Which model for which agent

Each agent was tested on its own job with the instructions it uses in the product (`evals/run_evals.py`, data in `evals/data/`). Speed is the median time per call.

## Referee: yellow card or banter?

| Model | Score | Median time | Notes |
|---|---|---|---|
| Azure AI Content Safety (alone) | 59% | 0.1s | 13/22 right |
| gpt-5.4-nano | 100% | 1.7s | 22/22 right |
| gpt-5.4-mini | 100% | 0.9s | 22/22 right |
| Phi-4-reasoning | 5% | 30.0s | 1/22 right; 21 failed to answer |

## Memory: bets noted two-way, predictions apart

| Model | Score | Median time | Notes |
|---|---|---|---|
| gpt-5.4-nano | 100% | 1.8s | 5/5 right |
| gpt-5.4-mini | 80% | 1.0s | 4/5 right |
| Phi-4-reasoning | 20% | 30.0s | 1/5 right; 4 failed to answer |

## Match agent: storyline true to the facts, in one line

| Model | Score | Median time | Notes |
|---|---|---|---|
| gpt-5.4-nano | 100% | 1.1s | 3/3 true and short |
| gpt-5.4-mini | 100% | 1.4s | 3/3 true and short |
| gpt-5.5 | 100% | 1.2s | 3/3 true and short |
| Phi-4-reasoning | 0% | 30.0s | 0/3 true and short (The factual stats and score are correct, but the storyline says there ); 2 failed to answer |

