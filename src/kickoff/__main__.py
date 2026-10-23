"""Command line: ``python -m kickoff serve`` runs Nemesys; ``build`` regenerates the synthetic world and match;
``news`` has the News agent rewrite the shipped club news."""

from __future__ import annotations

import argparse
import asyncio

from .data_store import build_all, load


def main() -> None:
    parser = argparse.ArgumentParser(prog="kickoff", description="Nemesys")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="Generate the world and match JSON files")
    sub.add_parser("news", help="Have the News agent write the shipped build-up stories (data/news.json)")
    sv = sub.add_parser("serve", help="Run the web server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "build":
        world, match = build_all()
        print(f"Built {world.league}: {len(world.table)} clubs, match {match.id} with {len(match.events)} events")
    elif args.command == "news":
        from . import news
        from .agents.llm import client_factory

        world, match = load()
        dossier = asyncio.run(news.write_news(world, match, client_factory()))
        news.save(dossier)
        for s in dossier.stories:
            print(f"[{s.club:>4} {s.kind:<8}] {s.headline}: {s.detail}")
    elif args.command == "serve":
        import uvicorn

        from . import tracing
        from .server import create_app
        tracing.setup()
        uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
