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
    voice        {"speaking": true}  (Achilles started or stopped speaking in the viewer's browser)
    seek_chapter {"chapter": 3}
    seek         {"minute": 52}
    profile      {"team": "kil" | null, "language": "zh", "level": "beginner", "hands_free": false}  (any subset)
    ask          {"text": "why was that goal ruled out?", "language": "en"}  -> a "host" message, plus
                 "profile" if settings changed; "language" is what the speech recogniser heard (optional)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .agents import HelpLevel, Language, ViewerProfile
from .agents.host import Host
from .agents.llm import ClientFactory, client_factory
from .chapters import build_chapters
from .data_store import load
from .models import Match, World
from .player import MatchPlayer
from .rival import Rival, RivalSession, RivalUnavailable
from .speech import Speech, SpeechUnavailable

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class SetupRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    profile: ViewerProfile = Field(default_factory=ViewerProfile)
    spoken: Language | None = Field(None, description="The language the speech recogniser heard")


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: Language = Language.EN
    tone: str = "neutral"


class RivalEvent(BaseModel):
    kind: Literal["goal", "miss", "save"]
    team: str


class RivalSide(BaseModel):
    team: str


def create_app(world: World | None = None, match: Match | None = None, clients: ClientFactory | None = None,
               speech: Speech | None = None, rival: Rival | None = None) -> FastAPI:
    if world is None or match is None:
        world, match = load()
    clients = clients or client_factory()
    speech = speech or Speech()
    rival = rival or Rival(world, match)
    chapters = build_chapters(match)
    app = FastAPI(title="Kick-Off", description="Live football explained for first-time viewers.")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "speech": speech.available}

    @app.get("/api/speech/token")
    async def speech_token() -> dict:
        """A short-lived token so the browser can recognise speech without ever seeing the key."""
        try:
            return {"token": await speech.token(), "region": speech.region}
        except SpeechUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/setup")
    async def setup(req: SetupRequest) -> dict:
        """Before kick-off, the viewer can set up by voice: "I'm new and I support Kilburn"."""
        state = {"profile": req.profile}

        def set_profile(**changes) -> ViewerProfile:
            state["profile"] = ViewerProfile.model_validate(state["profile"].model_dump() | changes)
            return state["profile"]

        host = Host(clients("host"), world, match, get_profile=lambda: state["profile"], set_profile=set_profile)
        reply = await host.ask(req.text, req.spoken.value if req.spoken else None)
        return {"answer": reply.answer, "profile": state["profile"].model_dump(mode="json"), "changes": reply.changes}

    @app.post("/api/speech")
    async def speak(req: SpeechRequest) -> Response:
        """One line of commentary as MP3, in the viewer's language and the Producer's tone."""
        try:
            audio = await speech.synthesise(req.text, req.language.value, req.tone)
        except SpeechUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})

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
                   speed: float = 1.0, hands_free: bool = False, demo: bool = False) -> None:
        await ws.accept()
        send_lock = asyncio.Lock()

        async def send(message: dict) -> None:
            async with send_lock:
                await ws.send_json(message)

        team = team if team in (match.home, match.away) else None
        profile = ViewerProfile(team=team, language=language, level=level, hands_free=hands_free)
        player = MatchPlayer(world, match, profile, clients, send,
                             mode="full" if mode == "full" else "key_moments", speed=speed, show_agents=demo)
        runner = asyncio.create_task(player.run())
        asks: set[asyncio.Task] = set()

        async def answer(text: str, spoken: str | None) -> None:
            try:
                reply = await player.ask(text, spoken)
            except Exception:
                log.exception("host failed")
                await send({"type": "error", "message": "Sorry, I couldn't answer that just now."})
                return
            if reply.changes:
                await send({"type": "profile", "profile": player.session.profile.model_dump(mode="json"),
                            "learned": player.session.learned_labels()})
            if not reply.answer:  # not meant for Kick-Off: say nothing
                await send({"type": "ignored", "question": text})
                return
            await send({"type": "host", "question": text, "answer": reply.answer, "changes": reply.changes,
                        "tools": reply.tools_used, "language": player.session.profile.language.value})

        try:
            while True:
                message = await ws.receive_json()
                if message.get("action") == "ask" and str(message.get("text", "")).strip():
                    spoken = message.get("language") if message.get("language") in ("en", "zh", "es") else None
                    task = asyncio.create_task(answer(str(message["text"])[:500], spoken))
                    asks.add(task)
                    task.add_done_callback(asks.discard)
                    continue
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
            for task in asks:
                task.cancel()
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner
            await player.close()

    @app.post("/api/rival/session")
    async def rival_session(request: Request) -> Response:
        """The browser's WebRTC offer in, GPT-Live's answer out; the session id comes back in a header."""
        offer = (await request.body()).decode()
        if not offer.startswith("v="):
            raise HTTPException(400, "Expected an SDP offer")
        try:
            session_id, answer = await rival.create(offer)
        except RivalUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return Response(answer, media_type="application/sdp", headers={"X-Session-Id": session_id})

    def rival_for(session_id: str) -> RivalSession:
        try:
            return rival.get(session_id)
        except KeyError:
            raise HTTPException(404, "No such rival session") from None

    @app.post("/api/rival/{session_id}/kickoff")
    async def rival_kickoff(session_id: str) -> dict:
        await rival.kick_off(rival_for(session_id))
        return {"ok": True}

    @app.get("/api/rival/{session_id}/state")
    def rival_state(session_id: str) -> dict:
        return rival_for(session_id).state()

    @app.post("/api/rival/{session_id}/side")
    async def rival_side(session_id: str, req: RivalSide) -> dict:
        """The on-screen fallback for 'who are you backing?' when speech doesn't catch it."""
        if req.team not in (match.home, match.away):
            raise HTTPException(400, "Unknown team")
        session = rival_for(session_id)
        await rival.choose_side(session, req.team)
        return session.state()

    @app.post("/api/rival/{session_id}/event")
    async def rival_event(session_id: str, req: RivalEvent) -> dict:
        if req.team not in (match.home, match.away):
            raise HTTPException(400, "Unknown team")
        session = rival_for(session_id)
        text = await rival.event(session, req.kind, req.team)
        return {"sent": text, "score": session.score}

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
        case "voice":  # the browser reports when Achilles starts and stops speaking
            player.set_voice_speaking(bool(message["speaking"]))
        case "seek_chapter":
            player.seek_chapter(int(message["chapter"]))
        case "seek":
            player.seek(int(float(message["minute"]) * 60))
        case "profile":
            changes = {k: message[k] for k in ("team", "language", "level", "hands_free") if k in message}
            profile = ViewerProfile.model_validate(player.session.profile.model_dump() | changes)
            player.set_profile(**profile.model_dump(exclude={"id"}))
            return {"type": "profile", "profile": profile.model_dump(mode="json"),
                    "learned": player.session.learned_labels()}
        case other:
            raise ValueError(f"unknown action {other!r}")
    return None
