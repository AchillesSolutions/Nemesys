# Nemesys: the pitch

Draft text for the submission form, section by section. Edit freely; every claim here is true of the code in this
repo.

Demo video (under 2 minutes): https://youtu.be/e9e_MaoPdts

## Tagline

Enjoy football with a rival. An AI fan of the other club who watches the match with you, by voice.

## The problem

Football is better with someone to argue with. But millions of fans watch alone: abroad, far from friends, or with no
one at home who supports the other side. And often they can't watch at all, on a train, in the car, at work. Today's
match apps give them scores and stats. None gives them company.

## What it does

Nemesys is a voice-only companion: a fan of the other club who watches the match with you, winds you up, celebrates
their goals in your ear, makes bets with you and remembers what you said last time. You just talk.

- **A real conversation.** The rival runs on GPT-Live, which listens and talks at the same time: you can interrupt it,
  and it cuts in the moment a goal goes in.
- **It knows the match.** Big moments break into the conversation as they happen. Ask "what's just happened?" and it
  tells you, in character, from the live match data: Nemesys works when you can't watch, not just alongside the TV.
- **In your language.** Choose English, Mandarin or Spanish by voice in the lobby, and you're matched with a rival who
  speaks it.
- **It remembers you.** Bold claims, bets and predictions are noted as you make them, called in when the match settles
  them, and kept for next time, with the running jokes between you.
- **It stays good-natured.** A Referee checks every line: the rival never goes too far, and you can get a yellow card.
- **Explainable match intelligence.** At full time, a report of the three moments that decided the match, why they
  mattered, and your bets settled.

## How we built it

One voice, steered by seven agents, each on the model that fits its job, chosen by evaluation:

| Agent | Job | Model |
|---|---|---|
| Lobby Host | Language, name, club, rival | Azure AI Speech (no LLM: rules are instant and can't go off script) |
| News Agent | The fortnight's club news, before kick-off | gpt-5.5 |
| Match Agent | Reads the live stats into storylines; the full-time report | gpt-5.5 |
| Ammo Agent | Picks the next dig: the news, the table, your own words | gpt-5.4-mini |
| Memory | Notes bets (two-way) and predictions | gpt-5.4-nano |
| Referee | Azure AI Content Safety as a fast gate, then a model to tell banter from abuse | Content Safety + gpt-5.4-mini |
| Memory Keeper | After the match: what to remember about you next time | Phi-4 |
| The Rival | The voice | GPT-Live, full duplex over WebRTC |

The agents share one state per viewer and hand work on to each other: the Ammo agent's dig goes through the Referee
before it reaches the rival; the Memory's bets come back to the rival when the match settles them. They never speak
themselves: they steer GPT-Live live through its sideband channel, timed so nothing interrupts the rival mid-sentence.
When something fails, the job falls back: the voice reconnects mid-match, the News agent falls back to the last good
stories, the Ammo agent to rule-based digs.

We tested every agent on its own job against the candidate models in Microsoft Foundry: Content Safety alone got 14 of
26 Referee calls right (it can't tell banter from abuse, and missed Mandarin swearing); gpt-5.4-mini got all 26. The
Phi models can't give the schema-checked replies the live agents need, so Phi-4 does the job that suits it: the Memory
Keeper, off the clock after the final whistle.

The data is our own: a synthetic league with two fully detailed clubs and a match simulated from their playing styles
(1,277 events: passes, shots, cards, VAR), deterministic from a seed. Every agent call, safety check and voice session
in a match is one trace in Application Insights, readable in the Foundry project's Tracing view. Deployed on Azure
Container Apps, with spending guards on the voice minutes.

## Challenges we ran into

- **Steering a live voice without interrupting it.** Anything sent to GPT-Live mid-sentence makes it drop the
  sentence, so the agents' work is held until a natural pause; only a goal breaks in.
- **Keeping the voice and the match in step.** Late in a match, big moments come fast; the clock now waits for the
  rival to finish reacting, so it never celebrates a goal VAR has already ruled out.
- **Three languages by voice.** Mandarin has no spaces between words, which quietly broke our Referee until a real
  session with a Mandarin-speaking friend showed it.

## What's next

More languages and rivals; any real fixture through a live data feed; a club's or broadcaster's own app as the home
for Nemesys, where a fan abroad watches with someone from home.

## Testing instructions

Nemesys is free to try, with no login: https://nemesys.victoriouswater-295fb226.swedencentral.azurecontainerapps.io

It is a voice conversation, so please use Chrome or Edge on a laptop or desktop, with headphones and a microphone
(on speakers, the microphone hears the rival too). Press Connect, allow the microphone, and answer the host: your
language ("English", "中文" or "Español"), your name, your club ("Kilburn" or "Harbour City"), then a rival by name.
The match kicks off shortly after the rival greets you and takes about 15 minutes; stay to the end for your match
report. Talk throughout: wind your rival up, make a bet, ask what's just happened or who will win the league.

The screen is a demo view of what's happening behind the voice; the product itself is audio only. Voice minutes are
capped each day to control cost; if the rival is busy or the day's minutes are used up, please try again later.

## Built with

Microsoft Foundry · Microsoft Agent Framework · GPT-Live · GPT-5.5, GPT-5.4 mini and nano · Phi-4 · Azure AI Speech ·
Azure AI Translator · Azure AI Content Safety · Application Insights · Azure Container Apps · Python · FastAPI ·
React · TypeScript
