"""Kick-Off web server.

REST endpoints serve the fixed data (clubs, table, chapters). Each viewer
opens one WebSocket, which gets its own MatchPlayer: the match streams down,
and control messages come up.

Messages from the server: ready, chapter, event, commentary, profile, finished, error.
Messages to the server, as {"action": ...}:
    pause | resume
    speed        {"value": 1.5}
    mode         {"mode": "full" | "key_moments"}
    next_chapter
    seek_chapter {"chapter": 3}
    seek         {"minute": 52}
    profile      {"team": "kil" | null, "language": "zh", "level": "beginner"}  (any subset)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .agents import HelpLevel, Language, ViewerProfile
from .agents.llm import ClientFactory, client_factory
from .chapters import build_chapters
from .data_store import load
from .models import Match, World
from .player import MatchPlayer

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(world: World | None = None, match: Match | None = None, clients: ClientFactory | None = None) -> FastAPI:
    if world is None or match is None:
        world, match = load()
    clients = clients or client_factory()
    chapters = build_chapters(match)
    app = FastAPI(title="Kick-Off", description="Live football explained for first-time viewers.")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/world")
    def get_world() -> dict:
        return world.model_dump(mode="json") | {
            "table": [row.model_dump() | {"points": row.points, "goal_difference": row.goal_difference}
                      for row in world.table]}

    @app.get("/api/match")
    def get_match() -> dict:
        return {"id": match.id, "home": match.home, "away": match.away, "venue": match.venue,
                "matchweek": match.matchweek, "duration": match.events[-1].elapsed,
                "chapters": [c.model_dump(mode="json") for c in chapters]}

    @app.websocket("/ws")
    async def live(ws: WebSocket, team: str | None = None, language: Language = Language.EN,
                   level: HelpLevel = HelpLevel.BEGINNER, mode: str = Query("key_moments"),
                   speed: float = 1.0) -> None:
        await ws.accept()
        send_lock = asyncio.Lock()

        async def send(message: dict) -> None:
            async with send_lock:
                await ws.send_json(message)

        team = team if team in (match.home, match.away) else None
        profile = ViewerProfile(team=team, language=language, level=level)
        player = MatchPlayer(world, match, profile, clients, send,
                             mode="full" if mode == "full" else "key_moments", speed=speed)
        runner = asyncio.create_task(player.run())
        try:
            while True:
                message = await ws.receive_json()
                try:
                    reply = apply_control(player, message)
                except (KeyError, ValueError, StopIteration, ValidationError) as exc:
                    await send({"type": "error", "message": f"Bad control message: {exc}"})
                    continue
                if reply:
                    await send(reply)
        except WebSocketDisconnect:
            pass
        finally:
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner
            await player.close()

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


def apply_control(player: MatchPlayer, message: dict) -> dict | None:
    """Apply one control message; return a message to send back, if any."""
    match message["action"]:
        case "pause":
            player.pause()
        case "resume":
            player.resume()
        case "speed":
            player.set_speed(float(message["value"]))
        case "mode":
            player.set_mode("full" if message["mode"] == "full" else "key_moments")
        case "next_chapter":
            player.next_chapter()
        case "seek_chapter":
            player.seek_chapter(int(message["chapter"]))
        case "seek":
            player.seek(int(float(message["minute"]) * 60))
        case "profile":
            changes = {k: message[k] for k in ("team", "language", "level") if k in message}
            profile = ViewerProfile.model_validate(player.session.profile.model_dump() | changes)
            player.set_profile(**profile.model_dump(exclude={"id"}))
            return {"type": "profile", "profile": profile.model_dump(mode="json"),
                    "learned": player.session.learned_labels()}
        case other:
            raise ValueError(f"unknown action {other!r}")
    return None
