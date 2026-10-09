"""Behaviour of the commentary agents, run against the offline model."""

import asyncio

import pytest
from agent_framework import BaseChatClient, FunctionInvocationLayer

from kickoff.agents import CommentarySession, HelpLevel, Language, ViewerProfile
from kickoff.agents.offline import OfflineChatClient
from kickoff.agents.viewer import Knowledge, depth_for
from kickoff.generator import generate_match
from kickoff.match_reader import MatchReader
from kickoff.models import EventType
from kickoff.world import AWAY_ID, HOME_ID, build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


class CountingFactory:
    def __init__(self, broken: set[str] = frozenset()):
        self.clients: dict[str, BaseChatClient] = {}
        self.broken = broken

    def __call__(self, role: str) -> BaseChatClient:
        self.clients[role] = BrokenClient() if role in self.broken else OfflineChatClient(role)
        return self.clients[role]

    @property
    def calls(self) -> int:
        return sum(getattr(c, "calls", 0) for c in self.clients.values())


class BrokenClient(FunctionInvocationLayer, BaseChatClient):
    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def fail():
            raise RuntimeError("model unavailable")
        return fail()


def run_match(world, match, profile, factory=None, until_minute=None):
    """Play the match through a session using match time as the clock."""
    factory = factory or CountingFactory()
    reader = MatchReader(world, match.home, match.away)
    now = {"t": 0.0}
    session = CommentarySession(world, match, profile, factory, clock=lambda: now["t"])
    out = []

    async def go():
        for ev in match.events:
            if until_minute is not None and ev.elapsed > until_minute * 60:
                break
            update = reader.apply(ev)
            now["t"] = ev.elapsed
            async for c in session.respond(update):
                out.append((ev, c))

    asyncio.run(go())
    return session, out, factory


def lines(out):
    return [(ev, line) for ev, c in out for line in c.lines]


def test_explanations_shorten_as_the_viewer_learns():
    profile, k = ViewerProfile(), Knowledge()
    assert depth_for(profile, k, "offside", now=0) == "full"
    k.record("offside", 0)
    assert depth_for(profile, k, "offside", now=60) is None, "no reminder straight after explaining"
    assert depth_for(profile, k, "offside", now=600) == "reminder"
    k.record("offside", 600)
    assert depth_for(profile, k, "offside", now=1200) == "nod"
    k.record("offside", 1200)
    assert depth_for(profile, k, "offside", now=3000) is None


def test_levels_assume_different_knowledge():
    k = Knowledge()
    assert depth_for(ViewerProfile(level=HelpLevel.GETTING_THERE), k, "corner", 0) is None
    assert depth_for(ViewerProfile(level=HelpLevel.GETTING_THERE), k, "offside", 0) == "full"
    assert depth_for(ViewerProfile(level=HelpLevel.ANALYST), k, "offside", 0) is None


def test_beginner_hears_offside_explained_then_nodded(world, match):
    session, out, _ = run_match(world, match, ViewerProfile())
    offside = [line.text for _, line in lines(out) if "offside" in line.concepts]
    assert len(offside) >= 2
    assert len(offside[0]) > len(offside[-1]), "later offside lines should be shorter"
    assert session.learned_labels()[:2] == ["Eleven a side", "Offside"]


def test_analyst_gets_no_rule_lessons(world, match):
    _, out, _ = run_match(world, match, ViewerProfile(level=HelpLevel.ANALYST))
    assert out
    assert all("teacher" not in line.sources for _, line in lines(out))


def test_big_moments_get_an_instant_headline_in_the_viewers_language(world, match):
    _, out, _ = run_match(world, match, ViewerProfile(language=Language.ZH, team=HOME_ID))
    goals = [(ev, line) for ev, line in lines(out) if line.headline and ev.type == EventType.GOAL]
    assert len(goals) == 6
    first_ev, first = goals[0]
    assert first.text.startswith("Harbour City进球了")
    assert first.tone == "disappointed", "a Kilburn fan should not sound happy about a Harbour City goal"
    assert goals[1][1].tone == "excited"


def test_neutral_viewer_hears_goals_excitedly(world, match):
    _, out, _ = run_match(world, match, ViewerProfile(), until_minute=36)
    goal = next(line for ev, line in lines(out) if line.headline)
    assert goal.tone == "excited"


def test_talk_budget_holds_back_minor_moments(world, match):
    reader = MatchReader(world, match.home, match.away)
    session = CommentarySession(world, match, ViewerProfile(), CountingFactory(), clock=lambda: 0.0)
    updates = {ev.type: reader.apply(ev) for ev in match.events}
    session.busy_until = 10.0  # still speaking
    assert not session.should_wake(updates[EventType.CORNER])
    assert session.should_wake(updates[EventType.RED_CARD])
    session.busy_until = 0.0
    assert session.should_wake(updates[EventType.CORNER])


def test_a_failing_specialist_does_not_silence_the_others(world, match):
    factory = CountingFactory(broken={"teacher"})
    _, out, _ = run_match(world, match, ViewerProfile(), factory=factory)
    sources = {s for _, line in lines(out) for s in line.sources}
    assert "teacher" not in sources
    assert {"storyteller", "league_guide"} <= sources


def test_a_failing_producer_falls_back_to_priority(world, match):
    factory = CountingFactory(broken={"producer"})
    _, out, _ = run_match(world, match, ViewerProfile(), factory=factory)
    assert any(c.reasoning.startswith("Fallback") for _, c in out)


def test_model_calls_stay_within_budget(world, match):
    """A full match must fit free-tier rate limits: most events cost no model calls."""
    _, _, factory = run_match(world, match, ViewerProfile())
    assert factory.calls < 120, factory.calls


def test_supporters_hear_we_in_headlines(world, match):
    _, out, _ = run_match(world, match, ViewerProfile(team=HOME_ID))
    goals = [line.text for ev, line in lines(out) if line.headline and ev.type == EventType.GOAL]
    assert goals[0].startswith("Harbour City have scored"), "the other side is named"
    assert goals[1].startswith("Goal! We've scored!"), "our goal is ours"
    _, neutral, _ = run_match(world, match, ViewerProfile(), until_minute=60)
    assert all("We've" not in line.text for _, line in lines(neutral)), "neutral viewers hear team names"


def test_reasoning_models_get_an_effort_instead_of_a_temperature(monkeypatch):
    from kickoff.agents.llm import is_reasoning, options_for

    class C:
        kickoff_model = "gpt-5.5"

    assert is_reasoning("gpt-5.5") and is_reasoning("o3") and not is_reasoning("gpt-4.1-mini")
    monkeypatch.setenv("KICKOFF_REASONING_EFFORT", "low")
    assert options_for(C(), {"temperature": 0.3, "response_format": dict}) == {"response_format": dict,
                                                                               "reasoning_effort": "low"}
    C.kickoff_model = "gpt-4.1-mini"
    assert options_for(C(), {"temperature": 0.3}) == {"temperature": 0.3}


def test_held_back_lines_are_dropped_once_the_score_changes(world, match):
    from kickoff.agents.messages import Proposal
    from kickoff.agents.producer import still_true

    reader = MatchReader(world, match.home, match.away)
    for ev in match.events:
        update = reader.apply(ev)
        if ev.type == EventType.GOAL and update.snapshot.home.score == 3:
            break
    session = CommentarySession(world, match, ViewerProfile(team=HOME_ID), CountingFactory())
    stale = Proposal(id=1, agent="league_guide", text="We go up to seventh with 13 points.", priority=6,
                     kind="stakes", made_at=update.event.elapsed - 200, score=(2, 2))
    fresh = Proposal(id=2, agent="league_guide", text="We go up to fifth.", priority=4, kind="stakes",
                     made_at=update.event.elapsed, score=(3, 2))
    session.backlog = [stale, fresh]
    assert still_true(session._moment(update), recent=set()) == [fresh]


def test_play_by_play_calls_fill_the_gaps(world, match):
    _, out, _ = run_match(world, match, ViewerProfile(), until_minute=60)
    calls = [(ev, line) for ev, line in lines(out) if line.call]
    kinds = {ev.type for ev, _ in calls}
    assert {EventType.OFFSIDE, EventType.SHOT, EventType.VAR_CHECK, EventType.HALF_TIME} <= kinds
    var = next(line.text for ev, line in calls if ev.type == EventType.VAR_CHECK)
    assert var == "VAR check! They're looking at a possible penalty."
    saved = next((line.text for ev, line in calls if ev.type == EventType.SHOT and ev.outcome == "saved"), None)
    assert saved is None or "saved by Halloran" in saved or "saved by Brennan" in saved


def test_var_check_is_called_before_the_explanation(world, match):
    _, out, _ = run_match(world, match, ViewerProfile())
    var = [c for ev, c in out if ev.type == EventType.VAR_CHECK][0]
    assert var.lines[0].call, "the call comes first, the Teacher's explanation follows"


def test_calls_are_in_the_viewers_language(world, match):
    _, out, _ = run_match(world, match, ViewerProfile(language=Language.ZH), until_minute=60)
    var = next(line.text for ev, line in lines(out) if line.call and ev.type == EventType.VAR_CHECK)
    assert var == "VAR介入！正在检查是否是点球。"
