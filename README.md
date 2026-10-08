# Kick-Off: Football for First-Timers

Kick-Off is a live AI companion that explains football to complete beginners as a match unfolds. A team of agents follows every event, decides what is worth saying, and explains it in plain words, tuned to the team the viewer supports and how much help they want.

Built for the Microsoft x Premier League *Inside the Game* hackathon. All clubs, players and data are fictional.

## Status

| Piece | State |
|---|---|
| Fictional world (2 clubs, league table) | Done |
| Style-driven match generator | Done |
| Replayer | Done |
| Match reader (live state, salience, signals) | Done |
| Teacher, Storyteller, League guide, Producer agents | Done (Microsoft Foundry, gpt-4.1-mini) |
| Web server: live WebSocket per viewer, Key moments mode | Done |
| Second screen (2D pitch, explanation cards, learned list, under the hood) | Done |
| Voice (Azure AI Speech; English, Mandarin, Spanish) | Planned |
| Azure deployment | Planned |

## The data

The rules provide no data, so we make it:

- **The Meridian League.** Two fully detailed clubs, Kilburn Athletic (patient passing) and Harbour City (counter-attacking), with squads, traits and playing styles. The other 18 clubs exist only in a table produced by simulating eight matchweeks, so the numbers add up.
- **One match, generated.** Open play is simulated from each club's style, so the patient side really keeps the ball (about 70%) and the counter-attacking side really scores on the break. Story beats are planted on top for the teaching moments: offsides, a free kick with a wall, a VAR penalty, tactical substitutions, a second yellow card, a goal ruled out by VAR and a late comeback to 3-2.

The same seed always produces the same match.

## The agents

```
Match reader ──► session ──► dispatcher ─┬─► Teacher ───────┐
 (plain code)   (when to                 ├─► Storyteller ───┼─► Producer ──► voice + cards
                 speak)                  └─► League guide ──┘
```

- **Match reader** (code): one live picture of the match, how much each event matters, and signals such as a quiet spell or a possession swing.
- **Session** (code, one per viewer): wakes the agents only when something is worth saying, keeps a talk-time budget so lines never pile up, remembers what this viewer has learned, and speaks an instant headline the moment a goal goes in.
- **Teacher**: explains a rule the first time it matters, using accurate rule facts, then gives a shorter reminder and finally a nod.
- **Storyteller**: momentum, turning points and how the two styles are shaping the match.
- **League guide**: what the score means for the table if it stands.
- **Producer**: sees every proposal at once, picks or merges what to say, sets the tone for the viewer's team, and saves less urgent points for a quiet spell.

The specialists run in parallel in a Microsoft Agent Framework workflow (fan-out, fan-in). Rules that must always hold (talk-time, knowledge, never repeating a line) live in code; judgement and wording come from the models.

Viewers choose a team (or neutral), a language (English, Mandarin, Spanish) and a help level: beginner, getting there, big picture, or analyst/broadcaster.

## Running it

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate elsewhere
pip install -e ".[dev]"

python -m kickoff build                       # regenerate src/kickoff/data/*.json
python -m kickoff replay --speed 600          # ten match minutes per second
python -m kickoff replay --start 80 --min-salience 3

# Commentary for one viewer. Offline by default (template answers, no keys needed).
python -m kickoff commentate --team kil --level beginner --reasoning
python -m kickoff commentate --llm azure --language zh --start 30 --end 40
python -m kickoff serve                       # http://127.0.0.1:8000
pytest
```

The web app lives in `web/` (React + TypeScript + Vite). Build it once and the Python server serves it:

```bash
cd web
npm install
npm run build        # output in web/dist, served at http://127.0.0.1:8000
npm run dev          # or: live-reloading dev server on :5173, proxying to the Python server
```

A viewer can be preset from the URL, skipping setup, which is handy for two side-by-side windows:
`http://127.0.0.1:8000/?team=kil&language=zh&level=beginner&speed=1.5`

Model providers are set in `.env` (see `.env.example`): `offline` or `azure` (Microsoft Foundry).

## Layout

```
src/kickoff/
  models.py        shared data models (clubs, players, events)
  world.py         the fictional league and the two clubs
  generator.py     style-driven simulation and story beats
  replayer.py      releases events at match speed
  match_reader.py  live match state, salience and signals for the agents
  league.py        table projections ("if the score stands")
  chapters.py      Key moments: short windows around the big moments
  player.py        plays the match for one viewer (clock, controls, commentary)
  server.py        FastAPI: REST data + one WebSocket per viewer
  agents/          viewer profile, specialists, producer, workflow, session
tests/             data, match reader, agent and server tests
web/               the second screen: pitch, scoreboard, live explanations, learned list
```
