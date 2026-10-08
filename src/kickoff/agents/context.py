"""Turns events and snapshots into plain-English facts for the agents.

Agents never see raw ids: every player and club is resolved to a name here,
so prompts are short and the models cannot invent squad details.
"""

from __future__ import annotations

from ..match_reader import ReaderUpdate
from ..models import EventType, MatchEvent, World

# Which teachable concepts each event touches. Ordered: the first is the main one.
EVENT_CONCEPTS: dict[EventType, list[str]] = {
    EventType.KICK_OFF: ["kick_off"],
    EventType.OFFSIDE: ["offside"],
    EventType.FOUL: ["foul"],
    EventType.FREE_KICK: ["free_kick"],
    EventType.CORNER: ["corner"],
    EventType.YELLOW_CARD: ["yellow_card"],
    EventType.RED_CARD: ["red_card"],
    EventType.SUBSTITUTION: ["substitution"],
    EventType.VAR_CHECK: ["var"],
    EventType.VAR_DECISION: ["var"],
    EventType.PENALTY: ["penalty"],
    EventType.GOAL_DISALLOWED: ["var", "offside"],
    EventType.HALF_TIME: ["half_time"],
    EventType.GOAL_KICK: ["goal_kick"],
    EventType.THROW_IN: ["throw_in"],
}



def concepts_for(ev: MatchEvent) -> list[str]:
    concepts = list(EVENT_CONCEPTS.get(ev.type, []))
    if ev.type == EventType.FREE_KICK and ev.detail.get("wall"):
        concepts.insert(0, "direct_free_kick")
    if ev.type == EventType.RED_CARD and ev.detail.get("reason") == "second yellow card":
        concepts.insert(0, "second_yellow")
        concepts.append("ten_men")
    if ev.type == EventType.GOAL and ev.detail.get("pattern") == "counter_attack":
        concepts.append("counter_attack")
    if ev.minute.count("+") and ev.type in (EventType.GOAL, EventType.HALF_TIME, EventType.FULL_TIME):
        concepts.append("added_time")
    return concepts


class Describer:
    def __init__(self, world: World, home: str, away: str):
        self.world, self.home, self.away = world, home, away

    def club(self, club_id: str | None) -> str:
        return self.world.club(club_id).short_name if club_id else ""

    def player(self, player_id: str | None) -> str:
        if not player_id:
            return ""
        return self.world.club(player_id.split("-")[0]).player(player_id).name

    def event(self, ev: MatchEvent) -> str:
        """One factual sentence describing the event."""
        team, p, o = self.club(ev.team), self.player(ev.player), self.player(ev.other_player)
        d = ev.detail
        match ev.type:
            case EventType.KICK_OFF:
                return f"{team} kick off."
            case EventType.PASS:
                return f"{p} ({team}) passes to {o}: {ev.outcome}."
            case EventType.SHOT:
                how = " with a header" if d.get("body_part") == "head" else ""
                src = f" from a {d['from']}" if d.get("from") else ""
                return f"{p} ({team}) shoots{how}{src}: {ev.outcome.replace('_', ' ')}."
            case EventType.SAVE:
                return f"{team} goalkeeper {p} makes a save."
            case EventType.GOAL:
                score = self._score(d["score"])
                extra = {"counter_attack": f" on the counter-attack, {d.get('seconds_from_recovery')} seconds after "
                                           "winning the ball",
                         "penalty": " from the penalty spot",
                         "sustained_pressure": f" after a {d.get('passes')}-pass move",
                         "corner": " from a corner"}.get(d.get("pattern"), "")
                assist = f", set up by {self.player(d['assist'])}" if d.get("assist") else ""
                return f"GOAL! {p} scores for {team}{extra}{assist}. {score}."
            case EventType.GOAL_DISALLOWED:
                return f"{p}'s goal for {team} is ruled out for {d.get('reason')}. {self._score(d['score'])}."
            case EventType.TACKLE:
                return f"{p} ({team}) wins the ball from {o} with a tackle."
            case EventType.INTERCEPTION:
                return f"{p} ({team}) {'clears the ball' if d.get('clearance') else 'intercepts the ball'}."
            case EventType.FOUL:
                return f"{p} ({team}) fouls {o}."
            case EventType.FREE_KICK:
                if d.get("wall"):
                    return (f"Free kick to {team}, {d.get('distance_to_goal_m')} metres from goal. {p} stands over it; "
                            f"the defending team builds a wall {d.get('wall_distance_yards')} yards away.")
                return f"Free kick to {team} for {d.get('reason')}."
            case EventType.CORNER:
                return f"Corner to {team}, taken by {p}."
            case EventType.THROW_IN:
                return f"Throw-in to {team}."
            case EventType.GOAL_KICK:
                return f"Goal kick to {team}."
            case EventType.OFFSIDE:
                return f"{p} ({team}) is flagged offside, receiving a pass from {self.player(d.get('passer'))}."
            case EventType.YELLOW_CARD:
                nth = "A second yellow card of the match" if d.get("booking_number") == 2 else "Yellow card"
                return f"{nth} for {p} ({team}): {d.get('reason')}."
            case EventType.RED_CARD:
                return f"Red card for {p} ({team}) after a {d.get('reason')}. {team} are down to ten players."
            case EventType.SUBSTITUTION:
                return f"{team} substitution: {o} comes on for {p}. {d.get('note', '')}."
            case EventType.VAR_CHECK:
                return f"VAR is checking a {d.get('reason')}: {d.get('incident')}."
            case EventType.VAR_DECISION:
                return f"VAR decision: {d.get('decision')}. {d.get('explanation', '')}"
            case EventType.PENALTY:
                return f"Penalty to {team}! {p} will take it."
            case EventType.HALF_TIME:
                return f"Half-time. {self._score(d['score'])}."
            case EventType.FULL_TIME:
                return f"Full-time. {self._score(d['score'])}."
        return f"{ev.type.value} {team} {p}".strip()

    def _score(self, score: dict[str, int]) -> str:
        return f"{self.club(self.home)} {score[self.home]}-{score[self.away]} {self.club(self.away)}"

    def situation(self, update: ReaderUpdate) -> str:
        """A compact summary of the live state for prompts."""
        s = update.snapshot
        h, a = s.home, s.away
        lines = [
            f"Minute {s.minute}, period {s.period}. Score: {h.name} {h.score}-{a.score} {a.name}.",
            f"Possession overall {h.name} {h.possession_pct}% / {a.name} {a.possession_pct}%; "
            f"last 10 minutes {h.possession_last_10_pct}% / {a.possession_last_10_pct}%.",
            f"Shots (on target): {h.name} {h.shots} ({h.shots_on_target}), {a.name} {a.shots} ({a.shots_on_target}).",
            f"Players on the pitch: {h.name} {len(h.players_on_pitch)}, {a.name} {len(a.players_on_pitch)}.",
            f"Momentum over 10 minutes (-1 {a.name} on top, +1 {h.name} on top): {s.momentum}.",
        ]
        booked = [f"{self.player(p)} ({t.name})" for t in (h, a) for p in t.booked if p not in t.sent_off]
        if booked:
            lines.append("On a yellow card: " + ", ".join(booked) + ".")
        return "\n".join(lines)

    def table(self, top: int = 8) -> str:
        rows = [f"{i}. {r.club}: {r.points} pts, GD {r.goal_difference:+d}, form {''.join(r.form)}"
                for i, r in enumerate(self.world.table, 1)]
        return "\n".join(rows[:top])

    def club_profile(self, club_id: str) -> str:
        c = self.world.club(club_id)
        return f"{c.name} ({c.nickname}), {c.stadium}. Style: {c.style.summary}"
