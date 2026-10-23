"""Nemesys web server.

The lobby (/api/rival/watch, heard, host), the rival's voice (/api/rival/session: the browser's WebRTC offer in,
GPT-Live's answer out), and one stream per watch (/api/rival/{id}/stream) carrying the match as it plays and what
the agents are doing, for the demo view. The fixed data (/api/world, /api/match) draws the fixture. The built web
app is served from web/dist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents.llm import ClientFactory, client_factory
from .data_store import load
from .models import Match, World
from . import rival as rival_module
from .rival import Rival, RivalUnavailable, Watch
from .speech import Speech, SpeechUnavailable
from .translate import Translator, TranslatorUnavailable

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class RivalEvent(BaseModel):
    kind: Literal["goal", "miss", "save"]
    team: str


class RivalSide(BaseModel):
    team: str


class RivalPick(BaseModel):
    id: str


class Heard(BaseModel):
    text: str = Field(max_length=500)
    lang: str | None = Field(default=None, max_length=16)  # the language speech recognition heard it in


class Translation(BaseModel):
    texts: list[str] = Field(max_length=20)
    to: str = Field("en", pattern=r"^[a-z]{2}(-[A-Za-z]{2,4})?$")


class HostLine(BaseModel):
    text: str = Field(min_length=1, max_length=300)


def create_app(world: World | None = None, match: Match | None = None, clients: ClientFactory | None = None,
               speech: Speech | None = None, rival: Rival | None = None) -> FastAPI:
    if world is None or match is None:
        world, match = load()
    clients = clients or client_factory()
    speech = speech or Speech()
    rival = rival or Rival(world, match, clients)
    translator = Translator()
    app = FastAPI(title="Nemesys", description="An AI rival fan to watch football with: voice only.")

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

    @app.get("/api/world")
    def get_world() -> dict:
        return world.model_dump(mode="json") | {
            "table": [row.model_dump() | {"points": row.points, "goal_difference": row.goal_difference}
                      for row in world.table]}

    @app.get("/api/match")
    def get_match() -> dict:
        return {"id": match.id, "home": match.home, "away": match.away, "venue": match.venue,
                "matchweek": match.matchweek, "duration": match.events[-1].elapsed, "lineup": match.events[0].frame}

    @app.post("/api/rival/watch")
    async def rival_watch(mode: str = "match", start: int = 0) -> dict:
        """The viewer has opened the lobby: a new watch, the News agent starts writing, and the host's welcome.
        ``start`` joins the match at that minute (for recording a particular scene)."""
        w = rival.new_watch(mode, start)
        return {"id": w.id, **rival.lobby_state(w, rival.welcome())}

    @app.post("/api/rival/replay")
    async def rival_replay(which: str = "last", speed: float = 1.0) -> dict:
        """Play a recorded session back, for free: no model is called."""
        try:
            w = rival.replay(which, max(0.25, min(speed, 8.0)))
        except RivalUnavailable as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"id": w.id, **rival.lobby_state(w)}

    @app.post("/api/rival/dry")
    async def rival_dry(team: str = "kil", rival_id: str = Query("dez", alias="rival"), mode: str = "match",
                        start: int = 0) -> dict:
        """A dry run: the real match and agents, with a text stand-in for the rival's voice."""
        if team not in (match.home, match.away):
            raise HTTPException(400, "Unknown team")
        w = await rival.dry_run(team, rival_id, mode, start)
        return {"id": w.id, **rival.lobby_state(w)}

    @app.post("/api/rival/{watch_id}/pretend")
    async def rival_pretend(watch_id: str, req: Heard) -> dict:
        """In a dry run, a line from the viewer as if spoken (to check the Referee's yellow card)."""
        try:
            rival.pretend(watch_for(watch_id), req.text)
        except RivalUnavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/rival/welcome")
    def rival_welcome() -> dict:
        """The host's opening line, so the page can have it ready to play before Start is pressed."""
        return {"text": rival.welcome()}

    @app.post("/api/rival/host")
    async def rival_host(req: HostLine) -> Response:
        """The lobby host's voice: Azure AI Speech, deliberately not the rival's."""
        try:
            audio = await speech.synthesise(req.text, "host")
        except SpeechUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/api/rival/session")
    async def rival_session(request: Request, watch: str, voice: str | None = None,
                            accent: str | None = None) -> Response:
        """The browser's WebRTC offer in, GPT-Live's answer out, joining the viewer's watch. The same call
        reconnects the voice to a match already under way."""
        offer = (await request.body()).decode()
        if not offer.startswith("v="):
            raise HTTPException(400, "Expected an SDP offer")
        try:
            w, answer, resumed = await rival.connect(offer, watch, voice, accent)
        except RivalUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return Response(answer, media_type="application/sdp",
                        headers={"X-Watch-Id": w.id, "X-Resumed": "1" if resumed else "0"})

    def watch_for(watch_id: str) -> Watch:
        try:
            return rival.get(watch_id)
        except KeyError:
            raise HTTPException(404, "No such watch") from None

    @app.post("/api/rival/{watch_id}/opened")
    async def rival_opened(watch_id: str, resumed: bool = False) -> dict:
        """The voice is connected: open the conversation, or pick it back up after a reconnect."""
        await rival.opened(watch_for(watch_id), resumed)
        return {"ok": True}

    @app.post("/api/rival/{watch_id}/pause")
    async def rival_pause(watch_id: str, paused: bool = True) -> dict:
        """Pause or restart the match: the clock stops and the rival stays quiet until the viewer is back."""
        w = watch_for(watch_id)
        await rival.pause(w, paused)
        return {"paused": w.paused}

    @app.post("/api/rival/{watch_id}/audio")
    async def rival_audio(watch_id: str, sample: dict) -> dict:
        """How well the rival's voice is reaching the browser, every few seconds, for finding the cause of any
        stutter: packets lost or jitter (the network), concealment with nothing lost (GPT-Live's audio arriving
        late), or long tasks (a busy page)."""
        sample = {k: v for k, v in sample.items() if isinstance(v, (int, float))}
        rough = sample.get("concealed_pct", 0) >= 2 or sample.get("lost", 0) > 0 or sample.get("stretched_pct", 0) >= 5
        if rough:
            log.warning("Rival audio rough in %s: %s", watch_id, sample)
        rival_module.SAVED.mkdir(parents=True, exist_ok=True)
        with open(rival_module.SAVED / "audio.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "watch": watch_id, "rough": rough,
                                **sample}) + "\n")
        return {}

    @app.post("/api/translate")
    async def translate(req: Translation) -> dict:
        """Azure AI Translator: lines said in another language, in English (or ``to``), with the language each
        was detected as, so the page only shows a translation when there's something to translate."""
        texts = [t[:1000] for t in req.texts]
        try:
            done = await translator.to(req.to, texts)
        except TranslatorUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"texts": [t for t, _ in done], "from": [lang for _, lang in done]}

    @app.post("/api/rival/{watch_id}/end")
    async def rival_end(watch_id: str) -> dict:
        await rival.end(watch_for(watch_id))
        return {"ok": True}

    @app.get("/api/rival/{watch_id}/state")
    def rival_state(watch_id: str) -> dict:
        return watch_for(watch_id).state()

    @app.websocket("/api/rival/{watch_id}/stream")
    async def rival_stream(ws: WebSocket, watch_id: str) -> None:
        """The match as it plays (events with the live snapshot) and what the rival is being told."""
        await ws.accept()
        w = rival.watches.get(watch_id)
        if w is None:
            await ws.close(code=4404)
            return
        queue: asyncio.Queue = asyncio.Queue()
        w.listeners.add(queue)
        try:
            await ws.send_json({"type": "state", **w.state()})
            if w.last_event:
                await ws.send_json(w.last_event)
            while watch_id in rival.watches:
                with suppress(TimeoutError):
                    await ws.send_json(await asyncio.wait_for(queue.get(), timeout=5))
        except WebSocketDisconnect:
            pass
        finally:
            w.listeners.discard(queue)
            with suppress(Exception):
                await ws.close()

    @app.post("/api/rival/{watch_id}/heard")
    async def rival_heard(watch_id: str, req: Heard) -> dict:
        """What the viewer said to the lobby host: their club, then which rival. Returns what the host says next."""
        return rival.heard(watch_for(watch_id), req.text, req.lang)

    @app.post("/api/rival/{watch_id}/side")
    async def rival_side(watch_id: str, req: RivalSide) -> dict:
        """Testing only: pick the club by tapping (the product is voice only)."""
        if req.team not in (match.home, match.away):
            raise HTTPException(400, "Unknown team")
        return rival.choose_side(watch_for(watch_id), req.team)

    @app.post("/api/rival/{watch_id}/rival")
    async def rival_pick(watch_id: str, req: RivalPick) -> dict:
        """Testing only: pick the rival by tapping."""
        return rival.choose_rival(watch_for(watch_id), req.id)

    @app.post("/api/rival/{watch_id}/event")
    async def rival_event(watch_id: str, req: RivalEvent) -> dict:
        if req.team not in (match.home, match.away):
            raise HTTPException(400, "Unknown team")
        w = watch_for(watch_id)
        text = await rival.event(w, req.kind, req.team)
        return {"sent": text, "score": w.score}

    @app.get("/rival.html", include_in_schema=False)
    def old_address(request: Request) -> RedirectResponse:
        """Nemesys used to live at /rival.html; links to it still work."""
        query = request.url.query
        return RedirectResponse("/" + (f"?{query}" if query else ""))

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app

