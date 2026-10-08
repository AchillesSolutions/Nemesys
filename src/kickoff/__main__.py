"""Command line: ``python -m kickoff build`` or ``python -m kickoff replay``."""

from __future__ import annotations

import argparse
import asyncio

from .data_store import build_all, load
from .match_reader import MatchReader
from .replayer import replay


def _name(world, player_id: str | None) -> str:
    if not player_id:
        return ""
    club = world.club(player_id.split("-")[0])
    return club.player(player_id).name


async def _replay(speed: float, start: float, min_salience: int) -> None:
    world, match = load()
    reader = MatchReader(world, match.home, match.away)
    async for ev in replay(match, speed=speed, start_minute=start):
        update = reader.apply(ev)
        snap = update.snapshot
        if update.salience >= min_salience or update.signals:
            score = f"{snap.home.score}-{snap.away.score}"
            who = _name(world, ev.player)
            extra = f" ({ev.outcome})" if ev.outcome else ""
            print(f"{ev.minute:>5}'  {score}  {ev.type.value:<16} {ev.team or '':<4} {who}{extra}")
            for sig in update.signals:
                print(f"{'':>9}↳ signal: {sig}")
    print(f"\nPossession: {snap.home.name} {snap.home.possession_pct}% - {snap.away.possession_pct}% {snap.away.name}")


async def _commentate(team: str | None, language: str, level: str, start: float, end: float | None,
                      provider: str | None, show_reasoning: bool) -> None:
    from .agents import CommentarySession, ViewerProfile
    from .agents.llm import client_factory

    world, match = load()
    reader = MatchReader(world, match.home, match.away)
    now = {"t": 0.0}
    profile = ViewerProfile(team=team, language=language, level=level)
    session = CommentarySession(world, match, profile, client_factory(provider), clock=lambda: now["t"])
    for ev in match.events:
        update = reader.apply(ev)  # the reader must see every event, even those we skip
        now["t"] = ev.elapsed
        if ev.elapsed < start * 60 or (end is not None and ev.elapsed > end * 60):
            continue
        async for c in session.respond(update):
            for line in c.lines:
                marker = "!" if line.headline else ">"
                print(f"{c.minute:>5}' {marker} [{line.card_title}] ({line.tone.value}) {line.text}")
            if show_reasoning and not c.lines[0].headline:
                print(f"{'':>8}producer: {c.reasoning}")
    print("\nLearned:", ", ".join(session.learned_labels()))


def main() -> None:
    parser = argparse.ArgumentParser(prog="kickoff")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="Generate the world and match JSON files")
    rp = sub.add_parser("replay", help="Replay the match in the terminal")
    rp.add_argument("--speed", type=float, default=600, help="1 = real time, 600 = ten minutes per second")
    rp.add_argument("--start", type=float, default=0, help="Start minute")
    rp.add_argument("--min-salience", type=int, default=2, help="Hide events below this salience")
    cm = sub.add_parser("commentate", help="Run the agents over the match for one viewer")
    cm.add_argument("--team", choices=["kil", "hbr"], default=None, help="Club the viewer supports (default neutral)")
    cm.add_argument("--language", choices=["en", "zh", "es"], default="en")
    cm.add_argument("--level", choices=["beginner", "getting_there", "big_picture", "analyst"], default="beginner")
    cm.add_argument("--start", type=float, default=0, help="Start minute")
    cm.add_argument("--end", type=float, default=None, help="End minute")
    cm.add_argument("--llm", default=None, help="offline | github | azure (default: KICKOFF_LLM or offline)")
    cm.add_argument("--reasoning", action="store_true", help="Show the Producer's reasoning")
    sv = sub.add_parser("serve", help="Run the web server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "build":
        world, match = build_all()
        print(f"Built {world.league}: {len(world.table)} clubs, match {match.id} with {len(match.events)} events")
    elif args.command == "serve":
        import uvicorn

        from .server import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    elif args.command == "replay":
        asyncio.run(_replay(args.speed, args.start, args.min_salience))
    else:
        asyncio.run(_commentate(args.team, args.language, args.level, args.start, args.end, args.llm, args.reasoning))


if __name__ == "__main__":
    main()
