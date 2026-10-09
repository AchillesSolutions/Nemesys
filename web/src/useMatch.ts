import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Chapter, Commentary, Control, MatchEvent, Mode, Profile, ServerMessage, Snapshot,
} from "./types";

export interface MatchState {
  connected: boolean;
  chapters: Chapter[];
  chapter: Chapter | null;
  event: MatchEvent | null;
  recent: MatchEvent[]; // last few events, newest last, for the pitch trail
  snapshot: Snapshot | null;
  /** When the latest event arrived (performance.now()) and match seconds until the next one, for smooth motion. */
  arrivedAt: number;
  nextGap: number | null;
  /** How fast the current stretch plays (quiet play inside a highlight is compressed). */
  pace: number;
  /** Set while play skips ahead between highlights, so the UI can say so. */
  bridge: { from: string; to: string } | null;
  feed: Commentary[]; // newest first
  learned: string[];
  profile: Profile;
  mode: Mode;
  speed: number;
  paused: boolean;
  finished: boolean;
  error: string | null;
  asking: number; // questions waiting for the Host's answer
}

const TRAIL = 6;
let feedSeq = 0; // every feed entry gets its own identity, even when several share an event

/** Add to the feed keeping match order, newest moment first, whenever each line happens to arrive. */
function addToFeed(feed: Commentary[], entry: Commentary): Commentary[] {
  const at = (c: Commentary) => c.elapsed ?? 0;
  const next = [entry, ...feed].sort((a, b) => at(b) - at(a) || (b.seq ?? 0) - (a.seq ?? 0));
  return next.slice(0, FEED_MAX);
}
const FEED_MAX = 40;

function socketUrl(profile: Profile, mode: Mode, speed: number): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ language: profile.language, level: profile.level, mode, speed: String(speed) });
  if (profile.team) q.set("team", profile.team);
  if (profile.hands_free) q.set("hands_free", "true");
  return `${proto}://${location.host}/ws?${q}`;
}

/** One live match for one viewer: connects on start, streams state, sends controls. */
export function useMatch(initial: Profile | null, initialMode: Mode = "key_moments", initialSpeed = 1.5) {
  const [state, setState] = useState<MatchState>(() => ({
    connected: false, chapters: [], chapter: null, event: null, recent: [], snapshot: null, feed: [],
    arrivedAt: 0, nextGap: null, pace: 1, bridge: null,
    learned: [], profile: initial ?? { team: null, language: "en", level: "beginner", hands_free: false },
    mode: initialMode, speed: initialSpeed, paused: false, finished: false, error: null, asking: 0,
  }));
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!initial) return;
    const socket = new WebSocket(socketUrl(initial, initialMode, initialSpeed));
    ws.current = socket;
    socket.onopen = () => setState((s) => ({ ...s, connected: true, error: null }));
    socket.onclose = () => setState((s) => ({ ...s, connected: false }));
    socket.onmessage = (e) => {
      const msg = JSON.parse(e.data) as ServerMessage;
      setState((s) => reduce(s, msg));
    };
    return () => socket.close();
  }, [initial, initialMode, initialSpeed]);

  const send = useCallback((control: Control) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(control));
    // Optimistic local updates so the controls feel instant.
    setState((s) => {
      switch (control.action) {
        case "pause": return { ...s, paused: true };
        case "resume": return { ...s, paused: false };
        case "speed": return { ...s, speed: control.value };
        case "mode": return { ...s, mode: control.mode };
        case "seek_chapter":
        case "next_chapter": return { ...s, mode: "key_moments", finished: false };
        case "seek": return { ...s, finished: false };
        case "ask": return { ...s, asking: s.asking + 1 };
        default: return s;
      }
    });
  }, []);

  return { state, send };
}

function reduce(s: MatchState, msg: ServerMessage): MatchState {
  switch (msg.type) {
    case "ready":
      return { ...s, chapters: msg.chapters, profile: msg.profile, mode: msg.mode, speed: msg.speed };
    case "chapter":
      return { ...s, chapter: msg.chapter };
    case "bridge":
      return { ...s, bridge: { from: msg.from_minute, to: msg.to_minute } };
    case "event": {
      const jumped = s.event != null && msg.event.elapsed - s.event.elapsed > 120;
      return {
        ...s, event: msg.event, snapshot: msg.snapshot, arrivedAt: performance.now(), nextGap: msg.next_gap,
        pace: msg.pace, bridge: null,
        // Trails make no sense across a jump: start them afresh.
        recent: jumped ? [msg.event] : [...s.recent, msg.event].slice(-TRAIL),
      };
    }
    case "commentary": {
      const { type: _type, ...commentary } = msg;
      return { ...s, feed: addToFeed(s.feed, { ...commentary, seq: ++feedSeq }), learned: commentary.learned };
    }
    case "profile":
      return { ...s, profile: msg.profile, learned: msg.learned };
    case "host": {
      const entry: Commentary = {
        seq: ++feedSeq, event_id: -Date.now(), elapsed: s.event?.elapsed ?? 0, minute: s.event?.minute ?? "0", reasoning: "", proposals: [], learned: s.learned,
        lines: [{ text: msg.answer, tone: "neutral", card_title: `“${msg.question}”`, sources: ["host"],
                  proposal_ids: [], concepts: [], headline: false }],
      };
      return { ...s, feed: addToFeed(s.feed, entry), asking: Math.max(0, s.asking - 1) };
    }
    case "finished":
      return { ...s, finished: true };
    case "error":
      return { ...s, error: msg.message, asking: Math.max(0, s.asking - 1) };
  }
}
