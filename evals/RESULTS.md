# Which model for which agent

Each agent was tested on its own job with the instructions it uses in the product (`evals/run_evals.py`, data in `evals/data/`). Speed is the median time per call.

## Referee: yellow card or banter?

| Model | Score | Median time | Notes |
|---|---|---|---|
| Azure AI Content Safety (alone) | 59% | 0.1s | 13/22 right |
| gpt-5.4-nano | 95% | 1.5s | 21/22 right |
| gpt-5.4-mini | 100% | 0.9s | 22/22 right |
| Phi-4 | 0% | 0.3s | 0/22 right; 22 failed to answer |
| Phi-4-mini-instruct | 0% | 0.3s | 0/22 right; 22 failed to answer |
| Phi-4-reasoning | 5% | 30.0s | 1/22 right; 21 failed to answer |

## Memory: bets noted two-way, predictions apart

| Model | Score | Median time | Notes |
|---|---|---|---|
| gpt-5.4-nano | 100% | 2.4s | 5/5 right |
| gpt-5.4-mini | 80% | 1.2s | 4/5 right |
| Phi-4 | 0% | 0.4s | 0/5 right; 5 failed to answer |
| Phi-4-mini-instruct | 0% | 0.6s | 0/5 right; 5 failed to answer |
| Phi-4-reasoning | 20% | 1.8s | 1/5 right; 2 failed to answer |

## Match agent: storyline true to the facts, in one line

| Model | Score | Median time | Notes |
|---|---|---|---|
| gpt-5.4-nano | 33% | 1.4s | 1/3 true and short (Harbour City have 3 shots on target, but only 2 goals/finishes. Saying) |
| gpt-5.4-mini | 100% | 1.3s | 3/3 true and short |
| gpt-5.5 | 100% | 1.3s | 3/3 true and short |
| Phi-4 | 0% | 0.3s | 0/3 true and short; 3 failed to answer |
| Phi-4-mini-instruct | 0% | 0.6s | 0/3 true and short; 3 failed to answer |
| Phi-4-reasoning | 0% | 4.6s | 0/3 true and short (Kilburn Athletic did not equalize with Ollie Whitcombe's penalty; it m) |

