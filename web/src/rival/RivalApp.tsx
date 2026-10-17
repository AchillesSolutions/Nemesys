import { useEffect, useRef, useState, type ReactNode } from "react";
import { LiveCall, type Speaker } from "./live";

/**
 * Test bench for the Rival. The rival asks who you're backing; once you've picked (by voice, or with the
 * buttons if it didn't catch it) a short countdown starts, then the build-up and the match, with the real
 * events sped up. The real product has no screen; this page exists to prove the conversation works.
 */

interface Club { id: string; name: string; short_name: string; colours: [string, string] }
interface Line { who: Speaker; text: string; at: number }
interface FeedItem { kind: string; text: string; minute: string }
interface MatchState {
  phase: string; minute: string; score: Record<string, number>; user_team: string | null;
  countdown: number; feed: FeedItem[];
}
type Status = "idle" | "connecting" | "live" | "ended";

const PAUSE_MS = 1800; // a fragment after this much quiet from the same speaker starts a new line
const COST_PER_S = 0.0008; // third-party listing for gpt-live-1; not yet on Azure's price list
const SOUNDS: Record<string, string> = { laugh: "laughs", laughs: "laughs", sigh: "sighs", groan: "groans",
  gasp: "gasps", scoff: "scoffs", chuckle: "chuckles", cheer: "cheers", boo: "boos" };
const PHASES: Record<string, string> = { setup: "Who are you backing?", countdown: "Build-up starts in",
  prematch: "Pre-match", first_half: "First half", half_time: "Half-time", second_half: "Second half",
  full_time: "Full-time" };
const FEED_LABELS: Record<string, string> = { setup: "Opening", side: "Sides", prematch: "Build-up", nudge: "Ammo",
  event: "Match", delegation: "Answered", manual: "Test" };

/** Transcript text with sound tags like [laugh] shown as stage directions; a half-arrived tag is hidden. */
function render(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const clean = text.replace(/\[[^\]]*$/, "");
  let last = 0;
  for (const m of clean.matchAll(/\[([^\]]+)\]/g)) {
    parts.push(clean.slice(last, m.index));
    const tag = m[1].trim().toLowerCase();
    parts.push(<em key={m.index} className="rival-sound">({SOUNDS[tag] ?? tag})</em>);
    last = m.index! + m[0].length;
  }
  parts.push(clean.slice(last));
  return parts;
}

export default function RivalApp() {
  const [clubs, setClubs] = useState<Club[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [state, setState] = useState<MatchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const call = useRef<LiveCall | null>(null);
  const open = useRef<Partial<Record<Speaker, number>>>({}); // each speaker's line still being spoken
  const started = useRef(0);

  useEffect(() => {
    Promise.all([fetch("/api/world").then((r) => r.json()), fetch("/api/match").then((r) => r.json())])
      .then(([world, match]) => {
        const byId = (id: string) => world.clubs.find((c: Club) => c.id === id);
        setClubs([byId(match.home), byId(match.away)]);
      })
      .catch(() => setError("Couldn't reach the server"));
    return () => call.current?.close();
  }, []);

  // While live: count billed seconds locally between the service's usage updates, and follow the match.
  useEffect(() => {
    if (status !== "live") return;
    const timer = setInterval(async () => {
      setSeconds((s) => Math.max(s, (Date.now() - started.current) / 1000));
      if (!call.current) return;
      const resp = await fetch(`/api/rival/${call.current.sessionId}/state`).catch(() => null);
      if (resp?.ok) setState(await resp.json());
    }, 1000);
    return () => clearInterval(timer);
  }, [status]);

  // Speakers overlap, so each keeps its own open line: a fragment joins that speaker's line unless they've paused.
  function addFragment(who: Speaker, text: string) {
    setLines((prev) => {
      const now = Date.now();
      const i = open.current[who];
      if (i !== undefined && prev[i] && now - prev[i].at < PAUSE_MS) {
        const next = prev.slice();
        next[i] = { who, text: prev[i].text + text, at: now };
        return next;
      }
      open.current[who] = prev.length;
      return [...prev, { who, text: text.trimStart(), at: now }];
    });
  }

  async function start() {
    setError(null);
    setLines([]);
    setState(null);
    setSeconds(0);
    open.current = {};
    setStatus("connecting");
    try {
      call.current = await LiveCall.start(
        {
          onTranscript: addFragment,
          onUsage: (s) => setSeconds((prev) => Math.max(prev, s)),
          onClosed: () => setStatus("ended"),
          onError: setError,
        },
        async (c) => {
          started.current = Date.now();
          setStatus("live");
          await fetch(`/api/rival/${c.sessionId}/kickoff`, { method: "POST" });
        },
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("idle");
    }
  }

  function stop() {
    call.current?.close();
    setStatus("ended");
  }

  async function post(path: string, body: object) {
    if (!call.current) return;
    const resp = await fetch(`/api/rival/${call.current.sessionId}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) setError("The server didn't accept that");
  }

  function toggleMute() {
    call.current?.setMuted(!muted);
    setMuted(!muted);
  }

  const [home, away] = clubs;
  const live = status === "live";
  const score = state?.score ?? {};
  const club = (id: string | null | undefined) => clubs.find((c) => c.id === id);
  const phase = state?.phase ?? "setup";

  return (
    <main className="rival">
      <header className="rival-head">
        <p className="rival-kicker">Kick-Off · Rival test bench</p>
        {home && away && (
          <h1 className="rival-fixture">
            <span style={{ color: home.colours[0] }}>{home.short_name}</span>
            <span className="rival-score">{score[home.id] ?? 0}–{score[away.id] ?? 0}</span>
            <span style={{ color: away.colours[0] }}>{away.short_name}</span>
          </h1>
        )}
        {live && (
          <p className="rival-phase">
            {PHASES[phase] ?? phase}
            {phase === "countdown" && ` ${state?.countdown}`}
            {(phase === "first_half" || phase === "second_half") && state?.minute && ` · ${state.minute}'`}
            {state?.user_team && ` · You: ${club(state.user_team)?.short_name}, rival: ${
              club(clubs.find((c) => c.id !== state.user_team)?.id)?.short_name}`}
          </p>
        )}
      </header>

      <section className="rival-controls">
        {status === "live" || status === "connecting" ? (
          <>
            <button className="rival-primary" onClick={stop}>End</button>
            <button onClick={toggleMute} disabled={!live}>{muted ? "Unmute mic" : "Mute mic"}</button>
          </>
        ) : (
          <button className="rival-primary" onClick={start}>{status === "ended" ? "Start again" : "Start"}</button>
        )}
        <span className="rival-meter">
          {status === "connecting" ? "Connecting…" : `${Math.round(seconds)}s · ~$${(seconds * COST_PER_S).toFixed(2)}`}
        </span>
      </section>
      <p className="rival-hint">Use headphones. Tell the rival who you're backing and the match starts five seconds later.</p>

      {live && phase === "setup" && home && away && (
        <section className="rival-side">
          <span>Didn't catch it? I'm backing</span>
          {[home, away].map((c) => (
            <button key={c.id} onClick={() => post("side", { team: c.id })}>{c.short_name}</button>
          ))}
        </section>
      )}

      {error && <p className="rival-error" role="alert">{error}</p>}

      <section className="rival-transcript" aria-live="polite">
        {lines.map((line, i) => (
          <p key={i} className={`rival-line rival-line-${line.who}`}>
            <span className="rival-who">{line.who === "user" ? "You" : "Rival"}</span>
            {render(line.text)}
          </p>
        ))}
        {!lines.length && <p className="rival-empty">The conversation shows up here as it happens.</p>}
      </section>

      {state && state.feed.length > 0 && (
        <section className="rival-sent">
          <h2>What the server told the rival</h2>
          {state.feed.slice().reverse().map((f, i) => (
            <p key={i}><span className="rival-tag">{FEED_LABELS[f.kind] ?? f.kind}</span>{f.text}</p>
          ))}
        </section>
      )}

      {home && away && (
        <details className="rival-manual">
          <summary>Test moments (made up, outside the match script)</summary>
          {[home, away].map((c) => (
            <div key={c.id} className="rival-event-row">
              <span className="rival-club" style={{ borderColor: c.colours[0] }}>{c.short_name}</span>
              <button disabled={!live} onClick={() => post("event", { kind: "goal", team: c.id })}>Goal</button>
              <button disabled={!live} onClick={() => post("event", { kind: "miss", team: c.id })}>Big miss</button>
              <button disabled={!live} onClick={() => post("event", { kind: "save", team: c.id })}>Saved</button>
            </div>
          ))}
        </details>
      )}
    </main>
  );
}
