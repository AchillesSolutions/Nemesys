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
  /** Set when Key moments jumps ahead, so the UI can say so. */
  jumpedTo: Chapter | null;
  feed: Commentary[]; // newest first
  learned: string[];
  profile: Profile;
  mode: Mode;
  speed: number;
  paused: boolean;
  finished: boolean;
  error: string | null;
}

const TRAIL = 6;
const FEED_MAX = 40;

function socketUrl(profile: Profile, mode: Mode, speed: number): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ language: profile.language, level: profile.level, mode, speed: String(speed) });
  if (profile.team) q.set("team", profile.team);
  return `${proto}://${location.host}/ws?${q}`;
}

/** One live match for one viewer: connects on start, streams state, sends controls. */
export function useMatch(initial: Profile | null, initialMode: Mode = "key_moments", initialSpeed = 1.5) {
  const [state, setState] = useState<MatchState>(() => ({
    connected: false, chapters: [], chapter: null, event: null, recent: [], snapshot: null, feed: [],
    arrivedAt: 0, nextGap: null, jumpedTo: null,
    learned: [], profile: initial ?? { team: null, language: "en", level: "beginner" },
    mode: initialMode, speed: initialSpeed, paused: false, finished: false, error: null,
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
      // A new chapter after the first one means play has jumped ahead.
      return { ...s, chapter: msg.chapter, jumpedTo: s.event ? msg.chapter : null };
    case "event": {
      const jumped = s.event != null && msg.event.elapsed - s.event.elapsed > 120;
      return {
        ...s, event: msg.event, snapshot: msg.snapshot, arrivedAt: performance.now(), nextGap: msg.next_gap,
        // Trails make no sense across a jump: start them afresh.
        recent: jumped ? [msg.event] : [...s.recent, msg.event].slice(-TRAIL),
      };
    }
    case "commentary": {
      const { type: _type, ...commentary } = msg;
      return { ...s, feed: [commentary, ...s.feed].slice(0, FEED_MAX), learned: commentary.learned };
    }
    case "profile":
      return { ...s, profile: msg.profile, learned: msg.learned };
    case "finished":
      return { ...s, finished: true };
    case "error":
      return { ...s, error: msg.message };
  }
}
