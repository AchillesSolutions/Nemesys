"""The Rival: what it's told, when, and how the match streams alongside it (GPT-Live itself is faked)."""

import asyncio
import json

import pytest

from kickoff import news
from kickoff import rival as rival_module
from kickoff.generator import generate_match
from kickoff.models import EventType
from kickoff.rival import (BIG, LIVE, MATCH, MIN_FRAME_S, ROSTER, SHORT, Pacing, Rival, Voice, Watch, ammo, describe,
                           feeling, frame_plan, match_seconds, pace_plan, previous_meeting, rival_instructions)
from kickoff.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


@pytest.fixture()
def dossier():
    return news.Dossier(stories=[
        news.Story(club="home", kind="transfer", headline="Whitcombe's £3m fee questioned", detail="One goal in six."),
        news.Story(club="away", kind="form", headline="Mwangi on fire", detail="Three in four."),
        news.Story(club="both", kind="history", headline="Last three meetings", detail="Harbour City 2-1 Kilburn."),
    ])


class FakeSideband:
    """Stands in for the GPT-Live sideband: records what the server sends."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        pass


INSTANT = {"quiet": 1e9, "build": 1e9, "holds": {}, "small_hold": 0, "half_time": 0, "greeting": 0, "after": 0,
           "max_s": 60}


def fast(monkeypatch):
    monkeypatch.setattr(rival_module, "NEWS_WAIT_S", 0.01)
    monkeypatch.setattr(rival_module, "PACING", {
        "short": Pacing(build_s=SHORT.build_s, tells_small=False, **INSTANT),
        "match": Pacing(build_s=MATCH.build_s, tells_small=True, **INSTANT)})
    monkeypatch.setattr(asyncio, "sleep", _quick(asyncio.sleep))
    monkeypatch.setattr(rival_module.Rival, "_fill", _no_wait)


async def _no_wait(self, watch, seconds, lull=0, gap=0):
    await asyncio.sleep(0)


def _quick(sleep):
    async def quick(seconds, *args, **kwargs):  # the sign-off's pause, among others
        return await sleep(min(seconds, 0.001), *args, **kwargs)
    return quick


def test_instructions_cover_both_clubs_the_boundaries_and_the_accent(world, match):
    text = rival_instructions(world, match, "american")
    assert "Kilburn Athletic" in text and "Harbour City" in text
    assert "own natural American voice" in text
    assert "never mention real clubs" in text
    assert "no violent metaphors" in text
    assert "a The" not in text and ".." not in text
    assert "American voice" not in rival_instructions(world, match, "none")


def test_the_rival_knows_who_it_is_after_the_lobby(world, match):
    rematch = {"when": "last season, at The Quay", "result": "Harbour City 2-1 Kilburn Athletic"}
    dez = next(p for p in ROSTER if p.id == "dez")
    text = rival_instructions(world, match, dez.accent, user_team="kil", rematch=rematch, persona=dez)
    assert "You are Dez from San Diego, a lifelong Harbour City fan" in text
    assert "followed Harbour City from San Diego for years" in text
    assert "own natural American voice" in text and "single breath" in text
    assert "matched you with a Kilburn Athletic fan" in text
    assert "it's YOU again" in text and "Harbour City 2-1 Kilburn Athletic" in text
    assert "asking who they are backing" not in text, "the lobby has already asked"


def test_ammo_goes_beyond_the_match(world, match, dossier):
    last_time = {"when": "last season", "result": "Harbour City 2-1 Kilburn Athletic", "user_said": ["We'll win it."]}
    points = ammo(world, match, world.club("hbr"), world.club("kil"), dossier, last_time)
    assert "8th" in points[0], "the table dig leads"
    assert any("Whitcombe's £3m fee" in p and "Wind them up" in p for p in points), "their club's news is a dig"
    assert any("Mwangi on fire" in p and "Boast" in p for p in points), "our club's good news is a boast"
    assert any("We'll win it." in p for p in points), "what they said last time comes back"
    assert any("history" in p for p in points)


def test_the_seeded_last_meeting_is_for_a_kilburn_fan(tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    assert previous_meeting("kil")["result"] == "Harbour City 2-1 Kilburn Athletic"
    assert previous_meeting("hbr") is None


def test_a_saved_conversation_is_quoted_before_the_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    (tmp_path / "watch-1.json").write_text(json.dumps({"user_team": "kil", "result": "Kilburn 3-2 Harbour City",
        "transcript": [["user", "Whitcombe is the best striker in this league"], ["rival", "Hardly."],
                       ["user", "Watch us come back from two down today"]]}))
    last = previous_meeting("kil")
    assert last["result"] == "Kilburn 3-2 Harbour City"
    assert "Watch us come back from two down today" in last["user_said"]


def test_moments_land_from_the_rivals_side(world, match):
    goals = [ev for ev in match.events if ev.type is EventType.GOAL]
    assert "Erupt" in feeling(goals[0], "hbr"), "Harbour City's first goal, for a Harbour City rival"
    assert "Gutted" in feeling(goals[0], "kil")
    red = next(ev for ev in match.events if ev.type is EventType.RED_CARD)
    assert "jeer" in feeling(red, "kil") and "Protest" in feeling(red, "hbr")
    half = next(ev for ev in match.events if ev.type is EventType.HALF_TIME)
    assert "gloat" in feeling(half, "hbr") and "excuses" in feeling(half, "kil")


def test_every_big_moment_is_described(world, match):
    described = [describe(world, ev) for ev in match.events if ev.type in BIG]
    goals = [d for d in described if "GOAL for" in d]
    assert len(goals) == 6
    assert "0-2" in goals[1], "two down at the break"
    assert "Kilburn 3-2 Harbour City" in goals[-1]
    assert any("RED CARD" in d for d in described)
    assert any("rules the" in d for d in described)


def test_the_short_match_fits_a_two_minute_demo(match):
    assert match_seconds(match.events, SHORT) + SHORT.greeting < 105, "with the lobby, about two minutes"
    assert 7.5 * 60 < match_seconds(match.events, MATCH) < 9.5 * 60, "about eight minutes, with room to talk"
    assert match_seconds(match.events, LIVE) > 100 * 60, "real time"


def test_fast_play_sends_the_browser_a_few_pictures_a_second(match):
    """Sending every pass at fast-forward pace (40-70 a second) made the pitch and the voice stutter."""
    plan = pace_plan(match.events, SHORT)
    frames = frame_plan(match.events, plan)
    assert len(frames) < len(match.events) / 4
    shown = {match.events[i].type for i in frames}
    assert {EventType.GOAL, EventType.SAVE, EventType.RED_CARD, EventType.HALF_TIME} <= shown, "moments always shown"
    clock, sent = 0.0, []
    for i in range(len(match.events)):
        if i:
            clock += (match.events[i].elapsed - match.events[i - 1].elapsed) / plan[i - 1]
        if i in frames:
            sent.append(clock)
    busiest = max(sum(1 for t in sent if s <= t < s + 1) for s in range(int(clock) + 1))
    assert busiest <= 12, f"{busiest} pictures in one second"
    gaps = [frames[i][0] for i in frames if frames[i][0] is not None]
    assert sum(gaps) == match.events[-1].elapsed - match.events[0].elapsed, "the clock still covers the whole match"


def test_play_slows_before_big_moments_and_runs_quickly_otherwise(match, monkeypatch):
    plan = pace_plan(match.events, SHORT)
    assert len(plan) == len(match.events)
    goal = next(i for i, ev in enumerate(match.events) if ev.type is EventType.GOAL)
    assert plan[goal - 1] == SHORT.build, "the move before a goal plays slowly"
    assert plan.count(SHORT.quiet) > len(plan) * 0.8, "most of the match plays quickly"
    assert all(pace > 0 for pace in plan), "the clock never stops or jumps"


@pytest.mark.parametrize("heard, team", [
    ("kilburn", "kil"), ("i'm with the otters", "kil"), ("harbour city mate", "hbr"), ("the gulls", "hbr"),
    ("neither", None), ("kilburn not harbour", None),
])
def test_side_comes_from_what_the_user_says(heard, team):
    assert Rival.side_from(heard) == team


def lobby(world, match, tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    rival = Rival(world, match, endpoint="https://example.invalid", key="test")
    watch = Watch("w1", {"kil": 0, "hbr": 0}, lobby="side", viewer="Max")  # Max: the seeded last meeting is his
    return rival, watch


def test_the_lobby_asks_who_it_is_and_keeps_memories_per_person(world, match, tmp_path, monkeypatch):
    rival, _ = lobby(world, match, tmp_path, monkeypatch)
    max_ = Watch("a", {"kil": 0, "hbr": 0})
    assert rival.heard(max_, "English please")["say"].endswith("Who am I talking to?")
    assert rival.heard(max_, "hi, it's Max")["say"].startswith("Max! Welcome back.")
    assert rival.heard(max_, "Kilburn")["say"].endswith("Rematch, or someone new?"), "his rematch with Dez"
    sam = Watch("b", {"kil": 0, "hbr": 0})
    rival.heard(sam, "English")
    assert rival.heard(sam, "Sam")["say"].startswith("Nice to meet you. "), "a new name isn't said: it may be mispronounced"
    step = rival.heard(sam, "Kilburn")
    assert "Rematch" not in step["say"] and step["offered"] == ["dez", "kev"], "someone new: the English speakers"
    assert not any(card["watched"] for card in step["online"])
    dez = rival.heard(sam, "Dez please")
    assert dez["rival"]["id"] == "dez" and sam.rematch is None, "nobody else's memories"


def test_the_lobby_speaks_mandarin_to_a_mandarin_speaker(world, match, tmp_path, monkeypatch):
    rival, _ = lobby(world, match, tmp_path, monkeypatch)
    assert "中文" in rival.welcome() and "español" in rival.welcome(), "the welcome offers all three"
    wei = Watch("c", {"kil": 0, "hbr": 0})
    step = rival.heard(wei, "中文", "zh-CN")
    assert wei.language == "Mandarin" and step["say"] == "[zh]好的，我们说中文。请问怎么称呼？[/zh]"
    assert rival.heard(wei, "我叫伟")["say"].startswith("[zh]很高兴认识你。[/zh] [zh]基尔本竞技对港城")
    step = rival.heard(wei, "基尔本")
    assert step["offered"] == ["chen"] and "来自上海的陈在线" in step["say"], "the Mandarin-speaking rival"
    assert rival.heard(wei, "不要，换个人")["say"].startswith("[zh]现在只有陈在线"), "nobody else speaks Mandarin"
    assert rival.heard(wei, "好的")["rival"]["id"] == "chen"


def test_the_lobby_speaks_spanish_to_a_spanish_speaker(world, match, tmp_path, monkeypatch):
    rival, _ = lobby(world, match, tmp_path, monkeypatch)
    ana = Watch("d", {"kil": 0, "hbr": 0})
    rival.heard(ana, "Español", "es-MX")
    assert ana.language == "Spanish"
    rival.heard(ana, "me llamo Ana")
    step = rival.heard(ana, "las gaviotas")
    assert step["offered"] == ["sofia"] and "Buenos Aires" in step["say"]
    assert rival.heard(ana, "sí, claro")["rival"]["id"] == "sofia"


def test_a_name_is_heard_however_it_is_said():
    from kickoff.rival import name_from
    assert name_from("Max") == "Max"
    assert name_from("um, my name is jean-paul") == "Jean-Paul"
    assert name_from("yeah it's sofia") == "Sofia"
    assert name_from("hello there my friend how are you") is None
    assert name_from("我叫伟") == "伟" and name_from("me llamo Ana") == "Ana"


def test_the_lobby_offers_a_rematch_first(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    assert rival.heard(watch, "erm, not sure")["say"].startswith("Sorry, I didn't catch that"), "nothing named: ask again"
    step = rival.heard(watch, "Kilburn, obviously")
    assert step["stage"] == "rival" and step["offered"] == ["dez"]
    assert "Dez from San Diego" in step["say"] and "Rematch, or someone new?" in step["say"]
    step = rival.heard(watch, "go on then, rematch")
    assert step["stage"] == "done" and step["rival"]["id"] == "dez" and step["rival"]["watched"]
    assert watch.persona.voice == "cedar" and watch.rematch["result"] == "Harbour City 2-1 Kilburn Athletic"


def test_someone_new_then_by_name(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    rival.heard(watch, "Kilburn")
    step = rival.heard(watch, "nah, someone new")
    assert step["say"] == "Also online: Kev from Boston. Who do you want to be matched with?"
    step = rival.heard(watch, "Kev then")
    assert step["rival"]["id"] == "kev" and not step["rival"]["watched"]
    assert watch.persona.voice == "cedar"
    assert watch.rematch is None, "never met: no 'last time' to quote"


def test_a_rival_who_speaks_another_language_only_speaks_it(world, match):
    chen = next(p for p in ROSTER if p.id == "chen")
    text = rival_instructions(world, match, chen.accent, user_team="kil", persona=chen)
    assert "you always speak Mandarin, whatever language the user uses" in text
    assert "Mandarin Chinese" in text
    english = rival_instructions(world, match, "american", user_team="kil", persona=ROSTER[0])
    assert "you speak English, whatever language the user uses" in english


def test_a_harbour_fan_meets_new_kilburn_rivals(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    step = rival.heard(watch, "the gulls")
    assert step["offered"] == ["rob", "priya"], "nobody to rematch: everyone online in their language is offered"
    assert step["say"].endswith("Who do you want to be matched with?")
    assert rival.heard(watch, "priya")["rival"]["home"] == "New York"


def run_match(world, match, monkeypatch, tmp_path, drop_voice_at: str | None = None, mode: str = "match",
              start: int = 0):
    persona = next(p for p in ROSTER if p.id == "dez")
    fast(monkeypatch)
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)

    async def run():
        rival = Rival(world, match, endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        watch = Watch("w1", {"kil": 0, "hbr": 0}, voice=Voice("v1", sideband), mode=mode, persona=persona,
                      start=start)
        rival.watches[watch.id] = watch
        stream: asyncio.Queue = asyncio.Queue()
        watch.listeners.add(stream)
        rival.choose_side(watch, "kil")
        await rival.opened(watch, resumed=False)
        while not watch.director.done():
            await asyncio.sleep(0)
            if drop_voice_at and watch.minute == drop_voice_at:
                watch.voice = None  # GPT-Live went away mid-match
        messages = []
        while not stream.empty():
            messages.append(stream.get_nowait())
        return watch, sideband.sent, messages

    return asyncio.run(run())


def test_the_match_streams_and_the_rival_hears_the_big_moments(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path)
    assert watch.phase == "full_time"
    assert watch.score == {"kil": 3, "hbr": 2}

    events = [m for m in messages if m["type"] == "event"]
    assert len(events) < len(match.events) / 2, "routine play is thinned for the browser"
    assert events[-1]["snapshot"]["status"] == "full_time"

    assert sent[0]["type"] == "session.thinking.append" and sent[0]["content"].startswith("News on"), "news first"
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert spoken[0].startswith("You've just joined. Greet them"), "the rival greets, with a dig"
    goals = [s for s in spoken if "GOAL for" in s]
    assert len(goals) == 6
    assert all(s.startswith(rival_module.BREAK_IN) for s in goals), "big moments break in"
    assert "Erupt" in goals[0] and "Gutted" in goals[2], "Harbour City's goal thrills the rival; Kilburn's doesn't"
    phases = [m["phase"] for m in messages if m["type"] == "state"]
    assert phases[0] == "lobby" and "prematch" in phases and "half_time" in phases and phases[-1] == "full_time"
    moments = [m for m in messages if m["type"] == "moment"]
    assert sum(m["big"] for m in moments) >= 12, "the match feed shows the big moments"
    assert {m["id"] for m in messages if m["type"] == "agent"} >= {"ammo", "match"}, "agents report their work"


def test_the_short_match_tells_only_the_big_moments(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path, mode="short")
    assert watch.phase == "full_time" and watch.score == {"kil": 3, "hbr": 2}
    events = [m for m in messages if m["type"] == "event"]
    assert sum(e["next_gap"] or 0 for e in events) >= match.events[-1].elapsed - 200, "the clock never jumps"
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert sum("GOAL for" in s for s in spoken) == 6
    assert not any("big save" in s for s in spoken), "small moments stay off the short match"


def test_the_match_carries_on_when_the_voice_drops(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path, drop_voice_at="56")
    assert watch.phase == "full_time", "a live game never stops"
    assert watch.score == {"kil": 3, "hbr": 2}
    assert [m for m in messages if m["type"] == "event"][-1]["snapshot"]["status"] == "full_time"
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert not any("90+4'" in s for s in spoken), "nothing is sent while there's no voice"
    assert any("90+4'" in f["text"] for f in watch.feed), "but the feed still shows what happened"


# -- the agents (the offline client stands in for the model) ------------------------------------------------

def offline_clients():
    from kickoff.agents.offline import OfflineChatClient
    return lambda role: OfflineChatClient(role)


def test_the_agents_answer_in_their_structured_form():
    from kickoff.rival_agents import RivalAgents

    agents = RivalAgents(offline_clients())

    async def run():
        story = await agents.read_match({"minute": "56", "score": "Kilburn 1-2 Harbour City"})
        dig = await agents.next_dig({"candidates": ["used one", "fresh one"], "used": ["used one"]})
        check = await agents.check("Your keeper's had a mare", "minute 56")
        verdict = await agents.review([{"who": "rival", "said": "Ha!"}], "minute 56")
        return story, dig, check, verdict

    story, dig, check, verdict = asyncio.run(run())
    assert "56" in story
    assert dig.dig == "fresh one", "never a used dig"
    assert check.ok and check.line == "Your keeper's had a mare"
    assert verdict.verdict == "fine"


def test_the_agents_work_alongside_the_match(world, match, monkeypatch, tmp_path):
    fast(monkeypatch)
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    monkeypatch.setattr(rival_module, "REVIEW_EVERY_S", 0)

    async def run():
        rival = Rival(world, match, offline_clients(), endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        watch = Watch("w1", {"kil": 0, "hbr": 0}, voice=Voice("v1", sideband), mode="short",
                      persona=next(p for p in ROSTER if p.id == "dez"))
        rival.watches[watch.id] = watch
        rival.choose_side(watch, "kil")
        await rival.opened(watch, resumed=False)
        await asyncio.wait_for(watch.director, timeout=30)
        await asyncio.sleep(0.05)  # let the last read land
        dig = rival._take_dig(watch)
        watch.transcript.append(("rival", "Ha! " * 40))
        await asyncio.sleep(0.05)  # one referee review
        await rival.end(watch)
        return watch, sideband.sent, dig

    watch, sent, dig = asyncio.run(run())
    reads = [m["content"] for m in sent if m["type"] == "session.thinking.append" and "Match agent's read" in m["content"]]
    assert reads, "the Match agent's reads reach the rival as quiet context"
    assert watch.storyline.startswith("Offline read")
    assert dig is not None, "the Ammo agent has a refereed dig ready for the next lull"
    assert {"match", "ammo", "referee"} <= set(watch.agents)
    assert watch.agents["referee"]["text"] in ("All good-natured", "Dig checked: fine")


def test_a_dig_goes_stale_when_the_score_changes():
    async def run():
        rival = Rival.__new__(Rival)
        watch = Watch("w", {"kil": 0, "hbr": 0})
        watch.dig_score = (0, 0)

        async def ready():
            return "Ask them if they still think it's their year."
        watch.next_dig = asyncio.create_task(ready())
        await asyncio.sleep(0)
        watch.score = {"kil": 1, "hbr": 0}
        return rival._take_dig(watch)

    assert asyncio.run(run()) is None, "a dig written at 0-0 can't be used at 1-0"


def test_joining_a_match_under_way(world, match, monkeypatch, tmp_path):
    from kickoff.rival import start_index
    start = start_index(match.events, 80)
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path, start=start)
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert spoken[0].startswith("You've just joined, and the match is already under way")
    assert "minute 80" in spoken[0] and "Kilburn 1-2 Harbour City" in spoken[0], "the rival knows where it's up to"
    assert sum("GOAL for" in s for s in spoken) == 3, "only the goals from the 80th minute on are new"
    moments = [m for m in messages if m["type"] == "moment"]
    assert sum(m["kind"] == "goal" for m in moments) == 6, "the timeline still shows every goal"
    assert watch.score == {"kil": 3, "hbr": 2}


def test_the_rival_signs_off_at_full_time(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path)
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert spoken[-1].startswith("Time to go. Sign off in character: sulk"), "Kilburn won, so the Harbour fan sulks"
    assert "same time next week" in spoken[-1]
    assert sent[-1]["type"] == "session.close", "and the session ends"


def test_a_pause_stops_the_clock(world, match):
    async def run():
        rival = Rival(world, match, endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        watch = Watch("w", {"kil": 0, "hbr": 0}, voice=Voice("v", sideband), last_voice=10**12)
        rival.watches[watch.id] = watch
        await rival.pause(watch, True)
        started = asyncio.get_running_loop().time()
        waiting = asyncio.create_task(rival._fill(watch, 0.2, lull=999))
        await asyncio.sleep(0.6)
        assert not waiting.done(), "paused: the match waits"
        await rival.pause(watch, False)
        await waiting
        return asyncio.get_running_loop().time() - started, sideband.sent

    took, sent = asyncio.run(run())
    assert took >= 0.6, "nothing moved on while paused (the pause is checked every half second at most)"
    kinds = [m["type"] for m in sent]
    assert kinds[0] == "session.input_audio.mute" and "session.input_audio.unmute" in kinds
    assert any("Stay completely quiet" in m.get("content", "") for m in sent)


@pytest.mark.parametrize("said", ["someone else", "some one else please", "nah, someone new", "not him", "next"])
def test_asking_for_someone_else(world, match, tmp_path, monkeypatch, said):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    rival.heard(watch, "Kilburn")
    assert rival.heard(watch, said)["say"].startswith("Also online:")


@pytest.mark.parametrize("said, rival_id", [
    ("Uh, let's, let's speak with Des again.", "dez"),  # how speech recognition spelt it on the day
    ("yeah go on, same again", "dez"),
    ("Kevin please", "kev"),
    ("Kev", "kev"),
])
def test_picking_a_rival_by_how_people_actually_say_it(world, match, tmp_path, monkeypatch, said, rival_id):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    rival.heard(watch, "Kilburn")
    assert rival.heard(watch, said)["rival"]["id"] == rival_id


def test_not_sure_is_not_a_yes(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    rival.heard(watch, "Kilburn")
    step = rival.heard(watch, "hmm not sure really")
    assert step["stage"] == "rival" and step["rival"] is None
    assert step["say"] == "Sorry, I didn't catch that. Rematch with Dez, or someone new?", "the host asks again"


def test_the_host_asks_again_rather_than_going_quiet(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    assert rival.heard(watch, "erm well I think")["say"].endswith("Kilburn Athletic, or Harbour City?")
    assert rival.heard(watch, "um what")["say"] is None, "but not twice in a row"
    assert rival.heard(watch, "uh")["say"] is None, "and not for a stray word"


# -- free ways to check the screen: dry runs and replays --------------------------------------------------

def test_a_dry_run_plays_the_match_with_a_text_stand_in_and_is_never_saved(world, match, monkeypatch, tmp_path):
    fast(monkeypatch)
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)

    async def run():
        rival = Rival(world, match, endpoint="https://example.invalid", key="test")
        watch = await rival.dry_run("kil", "dez", "match")
        queue: asyncio.Queue = asyncio.Queue()
        watch.listeners.add(queue)
        await asyncio.wait_for(watch.director, timeout=30)
        await asyncio.sleep(0.2)  # the stand-in finishes its last words
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        return watch, messages

    watch, messages = asyncio.run(run())
    said = [m for m in messages if m["type"] == "transcript"]
    assert said and all(m["who"] == "rival" and "start_ms" in m for m in said)
    assert any("GOAL for" in m["delta"] for m in said) or any("GOAL" in t for _, t in watch.transcript)
    assert not list(tmp_path.glob("watch-*.json")), "a rehearsal never becomes 'last time'"


def test_a_replay_plays_a_recorded_session_back(world, match, tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    recording = [[0, {"type": "state", "phase": "prematch"}],
                 [10, {"type": "transcript", "who": "rival", "delta": "Oh, it's you again!", "start_ms": 10, "end_ms": 900}],
                 [20, {"type": "transcript", "who": "user", "delta": "Here we go", "start_ms": 5, "end_ms": 400}]]
    (tmp_path / "watch-20261018-000000-abc.json").write_text(json.dumps(
        {"user_team": "kil", "rival": "dez", "mode": "match", "recording": recording, "transcript": []}))

    async def run():
        rival = Rival(world, match, endpoint="https://example.invalid", key="test")
        watch = rival.replay("last", speed=8)
        queue: asyncio.Queue = asyncio.Queue()
        watch.listeners.add(queue)
        await asyncio.sleep(0.2)
        return watch, [queue.get_nowait() for _ in range(queue.qsize())]

    watch, messages = asyncio.run(run())
    assert watch.persona.id == "dez" and watch.rehearsal
    assert [m["type"] for m in messages] == ["state", "transcript", "transcript"]
    assert messages[2]["start_ms"] < messages[1]["start_ms"], "the viewer's line is kept as said first"


def test_the_referee_books_the_viewer_for_abuse_but_not_for_banter(world, match, monkeypatch):
    monkeypatch.setattr(rival_module, "USER_SETTLE_S", 0.01)

    async def run():
        rival = Rival(world, match, offline_clients(), endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        watch = Watch("w", {"kil": 0, "hbr": 0}, voice=Voice("v", sideband), rehearsal=True)
        rival.watches[watch.id] = watch
        stream: asyncio.Queue = asyncio.Queue()
        watch.listeners.add(stream)
        for said in ("your keeper's useless, mate", "oh shut up you idiot", "and you, idiot"):
            rival.pretend(watch, said)
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.5)  # the rival's reaction waits until the viewer has finished speaking
        return watch, sideband.sent, [stream.get_nowait() for _ in range(stream.qsize())]

    watch, sent, messages = asyncio.run(run())
    cards = [m for m in messages if m["type"] == "card"]
    assert [c["count"] for c in cards] == [1, 2], "banter: no card; abuse: a card each time, only ever a warning"
    assert cards[0]["reason"] == "Personal abuse"
    told = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert any("yellow card" in t and "booked" in t for t in told), "the rival reacts to the booking"
    assert any("another yellow card" in t for t in told)
    assert watch.agents["referee"]["text"].startswith("Yellow card for the viewer")


def test_the_referee_hears_mandarin_too(world, match, monkeypatch):
    """Chinese has no spaces between words: a short line in Mandarin must still reach the Referee."""
    monkeypatch.setattr(rival_module, "USER_SETTLE_S", 0.01)
    heard = []

    async def run():
        rival = Rival(world, match, offline_clients(), endpoint="https://example.invalid", key="test")
        watch = Watch("w", {"kil": 0, "hbr": 0}, voice=Voice("v", FakeSideband()), rehearsal=True)
        rival.watches[watch.id] = watch

        async def booking(said):
            heard.append(said)
            return type("Booking", (), {"card": False, "reason": ""})()
        monkeypatch.setattr(rival.agents, "booking", booking)
        for said in ("操你的", "哈"):
            rival.pretend(watch, said)
            await asyncio.sleep(0.3)

    asyncio.run(run())
    assert heard == ["操你的"], "a real line is checked; a single sound isn't"



def test_nothing_new_reaches_the_rival_mid_sentence_except_a_big_moment(world, match):
    """Anything handed to GPT-Live while it's talking makes it drop its sentence: it waits for a gap."""
    async def run():
        sideband = FakeSideband()
        watch = Watch("w", {"kil": 0, "hbr": 0}, voice=Voice("v", sideband))
        watch.heard("rival", "You lot keep the ball all you like, but")  # mid-sentence
        await watch.thinking("A storyline", kind="storyline")
        await watch.commentary("A save, 17'", ttl=0.3)  # a small moment that goes stale quickly
        await watch.commentary("GOAL!", urgent=True)
        sent_mid_sentence = [m["content"] for m in sideband.sent]
        await asyncio.sleep(1.6)  # the rival finishes; a gap opens
        return sent_mid_sentence, [m["content"] for m in sideband.sent]

    mid, after = asyncio.run(run())
    assert mid == ["GOAL!"], "only the big moment breaks in"
    assert after == ["GOAL!", "A storyline"], "the rest waits for the gap; the stale save is dropped"


def test_bets_are_remembered_and_called_in_at_full_time(world, match, monkeypatch, tmp_path):
    fast(monkeypatch)
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)

    async def run():
        rival = Rival(world, match, offline_clients(), endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        watch = Watch("w1", {"kil": 0, "hbr": 0}, voice=Voice("v1", sideband), mode="match",
                      persona=next(p for p in ROSTER if p.id == "dez"))
        rival.watches[watch.id] = watch
        rival.choose_side(watch, "kil")
        watch.heard("user", "Deal: if Harbour win, I'll admit Harbour are the better team")
        watch.heard("rival", "You're on! Shake on it, mate, no backing out now.")
        watch.heard("user", "Whitcombe is going to score twice today, you watch")
        watch.last_rival = watch.last_user = 0.0  # they finished talking a while ago: a gap for the reminder
        await rival._remember_pacts(watch)
        await rival.opened(watch, resumed=False)
        await asyncio.wait_for(watch.director, timeout=30)
        return watch, [m.get("content", "") for m in sideband.sent]

    watch, sent = asyncio.run(run())
    assert watch.pacts == ["Deal: if Harbour win, I'll admit Harbour are the better team"]
    assert any(s.startswith("Remember this, and call it in") for s in sent), "the rival is told to remember it"
    assert any(s.startswith("FULL-TIME") and "Call it in now" in s or "Call it in now" in s for s in sent)
    sign_off = next(s for s in sent if s.startswith("Time to go."))
    assert "settle what you agreed" in sign_off and "better team" in sign_off
    saved = json.loads(next(tmp_path.glob("watch-*.json")).read_text(encoding="utf-8"))
    assert saved["pacts"] == watch.pacts, "kept for next time"
    assert previous_meeting("kil", "dez")["pacts"] == watch.pacts


def test_each_rival_keeps_to_their_own_language(world, match):
    dez = next(p for p in ROSTER if p.id == "dez")
    text = rival_instructions(world, match, dez.accent, user_team="kil", persona=dez)
    assert "you speak English, whatever language the user uses" in text
    assert "switch straight away" not in text


def test_a_handoff_shows_only_once_the_rival_really_has_it(world, match):
    """The demo view draws a dot from one agent to the next; it must not fire while the send is still held back."""
    async def run():
        sideband = FakeSideband()
        watch = Watch("w", {"kil": 0, "hbr": 0}, voice=Voice("v", sideband))
        stream: asyncio.Queue = asyncio.Queue()
        watch.listeners.add(stream)
        watch.heard("rival", "You lot keep the ball all you like, but")  # mid-sentence
        await watch.thinking("A storyline", kind="storyline", then=[watch.handoff("match")])
        before = [stream.get_nowait() for _ in range(stream.qsize())]
        await asyncio.sleep(1.6)
        after = [stream.get_nowait() for _ in range(stream.qsize())]
        return before, after

    before, after = asyncio.run(run())
    assert not [m for m in before if m["type"] == "handoff"]
    assert {"type": "handoff", "from": "match", "to": "rival"} in after


def test_a_receipt_is_only_ever_the_viewers_own_words():
    from kickoff.rival import receipt_of, receipts
    watch = Watch("w", {"kil": 0, "hbr": 0})
    watch.heard("user", "Mwangi won't score all season, mark my words.")
    watch.heard("rival", "Our keeper is the best in the league.")
    watch.rematch = {"when": "last season", "user_said": ["Whitcombe is going to bag a hat-trick, just watch."]}

    mine = receipt_of("Mwangi won't score all season", "this_match", watch)
    assert mine and mine["quote"] == "Mwangi won't score all season" and mine["when"] == "Earlier this match"
    assert receipt_of("Our keeper is the best in the league", "this_match", watch) is None, "the rival's words"
    assert receipt_of("Mwangi won't score all season", "news", watch) is None
    point = 'Throw this back at them: last season, they said "Whitcombe is going to bag a hat-trick, just watch."'
    assert receipts(point, watch) == [{"type": "receipt", "quote": "Whitcombe is going to bag a hat-trick, just watch.",
                                       "when": "last season"}]
    assert receipts("Wind them up about the table.", watch) == []


def test_a_bet_receipt_reads_as_the_viewer_would_put_it():
    from kickoff.rival import bet_receipt
    watch = Watch("w", {"kil": 0, "hbr": 0}, persona=next(p for p in ROSTER if p.id == "dez"))
    watch.pacts = ["If Kilburn lose, the user will wear the rival's Harbour City shirt next week."]
    assert bet_receipt(watch)["quote"] == "If Kilburn lose, you will wear Dez's Harbour City shirt next week."


def test_an_unanswered_question_holds_back_a_new_topic():
    watch = Watch("w", {"kil": 0, "hbr": 0})
    watch.heard("user", "So who's likely to finish first, do you think")
    assert watch.question_open()
    watch.heard("rival", "Ooh, that's a good one.")
    assert watch.question_open(), "a holding noise isn't an answer"
    watch.heard("rival", " Kestrel Bay, easily: unbeaten, top of the pile, and your lot won't get near them this year.")
    assert not watch.question_open()
    watch.heard("user", "Great save there.")
    assert not watch.question_open()


def test_memory_is_noted_once_and_recalled_with_a_receipt():
    watch = Watch("w", {"kil": 0, "hbr": 0}, persona=next(p for p in ROSTER if p.id == "dez"))
    stream: asyncio.Queue = asyncio.Queue()
    watch.listeners.add(stream)
    from kickoff.rival import in_your_words, with_memory
    bet = watch.remember("bet", in_your_words("If Kilburn lose, the user will wear the rival's shirt.", watch))
    assert watch.remember("bet", bet["text"]) is bet, "the same memory isn't noted twice"
    sent = with_memory([{"type": "receipt", "quote": bet["text"], "when": "The bet", "kind": "bet"}], watch)
    assert {"type": "memory", "op": "recall", "id": bet["id"]} in sent
    assert {"type": "handoff", "from": "memory", "to": "rival", "via": "ammo"} in sent
    published = [stream.get_nowait() for _ in range(stream.qsize())]
    assert {"type": "handoff", "from": "ammo", "to": "memory"} in published
    assert sum(m["type"] == "memory" and m["op"] == "add" for m in published) == 1
