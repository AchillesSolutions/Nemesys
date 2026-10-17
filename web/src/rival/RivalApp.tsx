import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Directory, loadStatic } from "../api";
import { Fixture, Score } from "../components/Fixture";
import { LeagueTable } from "../components/Panels";
import { Pitch } from "../components/Pitch";
import type { MatchEvent, MatchInfo, Snapshot } from "../types";
import { LiveCall, type Speaker } from "./live";

/**
 * The Rival on the matchday screen. The rival asks who you're backing; once you've picked (by voice, or
 * with the buttons if it didn't catch it) a short countdown starts, then the build-up and the match, with
 * the real events sped up. The real product has no screen: this is the demo view of what's going on.
 */

interface Line { who: Speaker; text: string; at: number }
interface FeedItem { kind: string; text: string; minute: string }
interface RivalState { phase: string; minute: string; user_team: string | null; countdown: number; speed: number; feed: FeedItem[] }
interface Play { event: MatchEvent | null; snapshot: Snapshot | null; recent: MatchEvent[]; arrivedAt: number; nextGap: number | null }
type Status = "idle" | "connecting" | "live" | "ended";

const PAUSE_MS = 1800; // a fragment after this much quiet from the same speaker starts a new line
const SHOWN_LINES = 6; // the conversation window: roughly the last few exchanges
const TRAIL = 6;
const COST_PER_S = 0.0008; // third-party listing for gpt-live-1; not yet on Azure's price list
const SOUNDS: Record<string, string> = { laugh: "laughs", laughs: "laughs", sigh: "sighs", groan: "groans",
  gasp: "gasps", scoff: "scoffs", chuckle: "chuckles", cheer: "cheers", boo: "boos" };
const FEED_LABELS: Record<string, string> = { setup: "Opening", side: "Sides", prematch: "Build-up", nudge: "Ammo",
  event: "Match", delegation: "Answered", manual: "Test" };
const NO_PLAY: Play = { event: null, snapshot: null, recent: [], arrivedAt: 0, nextGap: null };

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
  const [data, setData] = useState<{ dir: Directory; match: MatchInfo & { lineup?: Record<string, number[]> } } | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [rival, setRival] = useState<RivalState | null>(null);
  const [play, setPlay] = useState<Play>(NO_PLAY);
  const [error, setError] = useState<string | null>(null);
  const call = useRef<LiveCall | null>(null);
  const stream = useRef<WebSocket | null>(null);
  const open = useRef<Partial<Record<Speaker, number>>>({}); // each speaker's line still being spoken
  const started = useRef(0);

  useEffect(() => {
    loadStatic()
      .then(({ world, match }) => setData({ dir: new Directory(world), match }))
      .catch(() => setError("Couldn't reach the server"));
    return () => { call.current?.close(); stream.current?.close(); };
  }, []);

  // Billing runs while the session is open: count it locally between the service's usage updates.
  useEffect(() => {
    if (status !== "live") return;
    const timer = setInterval(() => setSeconds((s) => Math.max(s, (Date.now() - started.current) / 1000)), 1000);
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

  function follow(sessionId: string) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/rival/${sessionId}/stream`);
    stream.current = ws;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "state") {
        const { type: _type, ...state } = msg;
        setRival(state);
      } else if (msg.type === "event") {
        setPlay((p) => ({
          event: msg.event, snapshot: msg.snapshot, arrivedAt: performance.now(), nextGap: msg.next_gap,
          recent: [...p.recent, msg.event].slice(-TRAIL),
        }));
      }
    };
  }

  async function start() {
    setError(null);
    setLines([]);
    setRival(null);
    setPlay(NO_PLAY);
    setSeconds(0);
    open.current = {};
    setStatus("connecting");
    try {
      call.current = await LiveCall.start(
        {
          onTranscript: addFragment,
          onUsage: (s) => setSeconds((prev) => Math.max(prev, s)),
          onClosed: () => { setStatus("ended"); stream.current?.close(); },
          onError: setError,
        },
        async (c) => {
          started.current = Date.now();
          setStatus("live");
          follow(c.sessionId);
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
    stream.current?.close();
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

  const shown = useMemo(() => lines.slice(-SHOWN_LINES), [lines]);
  if (!data) return <p className="boot">{error ?? "Loading…"}</p>;
  const { dir, match } = data;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  const live = status === "live";
  const phase = rival?.phase ?? "setup";
  const speed = rival?.speed ?? 12;
  const userClub = dir.club(rival?.user_team), rivalClub = userClub ? (userClub.id === home.id ? away : home) : null;

  return (
    <div className="live rival-live">
      <div className="screen">
        <Fixture dir={dir} match={match} home={play.snapshot?.home} away={play.snapshot?.away} lang="en">
          {play.event ? (
            <Score snapshot={play.snapshot} lang="en"
                   clock={{ event: play.event, arrivedAt: play.arrivedAt, nextGap: play.nextGap, speed, paused: false }} />
          ) : (
            <>
              <div className="score kickoff-time">{phase === "countdown" ? `0:0${rival?.countdown ?? 0}` : "20:00"}</div>
              <p className="match-clock">
                {!live ? `Kick-off · ${match.venue}`
                  : phase === "setup" ? "Who are you backing?"
                  : phase === "countdown" ? "Build-up starts in a moment"
                  : "Teams are out · kick-off soon"}
              </p>
            </>
          )}
        </Fixture>

        <main className="stage">
          <div className="stage-pitch">
            <Pitch dir={dir} match={match} snapshot={play.snapshot} recent={play.recent}
                   nextGap={play.nextGap} speed={speed} lang="en" lineup={match.lineup ?? null} />
          </div>

          <aside className="rail left">
            <section className="panel glass rival-session">
              <h2>The Rival <span className="sub">{live ? `${Math.round(seconds)}s · ~$${(seconds * COST_PER_S).toFixed(2)}` : ""}</span></h2>
              <div className="rival-buttons">
                {status === "live" || status === "connecting" ? (
                  <>
                    <button className="btn rival-primary" onClick={stop}>End</button>
                    <button className="btn" onClick={toggleMute} disabled={!live}>{muted ? "Unmute mic" : "Mute mic"}</button>
                  </>
                ) : (
                  <button className="btn rival-primary" onClick={start}>{status === "ended" ? "Start again" : "Start"}</button>
                )}
              </div>
              {status === "connecting" && <p className="empty">Connecting…</p>}
              {userClub && rivalClub ? (
                <p className="rival-sides">You're backing <b style={{ color: dir.colour(userClub.id) }}>{userClub.short_name}</b>.
                  {" "}The rival's <b style={{ color: dir.colour(rivalClub.id) }}>{rivalClub.short_name}</b> through and through.</p>
              ) : live ? (
                <div className="rival-side-pick">
                  <span className="empty">Didn't catch it? I'm backing</span>
                  {[home, away].map((c) => (
                    <button key={c.id} className="btn" onClick={() => post("side", { team: c.id })}>{c.short_name}</button>
                  ))}
                </div>
              ) : (
                <p className="empty">Headphones on. Tell the rival who you're backing and the match starts five seconds later.</p>
              )}
              {error && <p className="rival-error" role="alert">{error}</p>}
            </section>
            <LeagueTable dir={dir} match={match} snapshot={play.snapshot} lang="en" />
          </aside>

          <aside className="rail right">
            <section className="panel glass rival-feed">
              <h2>Feeding the rival</h2>
              {rival?.feed.length ? (
                rival.feed.slice(-4).reverse().map((f, i) => (
                  <p key={rival.feed.length - i} className="rival-feed-item">
                    <span className="rival-tag">{FEED_LABELS[f.kind] ?? f.kind}{f.minute && ` · ${f.minute}'`}</span>
                    {f.text}
                  </p>
                ))
              ) : <p className="empty">Match moments and talking points appear here as they're sent.</p>}
            </section>
            <section className="panel glass rival-chat" aria-live="polite">
              <h2>Conversation</h2>
              <div className="rival-chat-lines">
                {shown.map((line, i) => (
                  <p key={lines.length - shown.length + i}
                     className={`rival-line rival-line-${line.who}`}
                     style={{ opacity: 0.35 + 0.65 * ((i + 1) / shown.length) }}>
                    <span className="rival-who" style={{ color: line.who === "rival" && rivalClub ? dir.colour(rivalClub.id)
                      : line.who === "user" && userClub ? dir.colour(userClub.id) : undefined }}>
                      {line.who === "user" ? "You" : "Rival"}
                    </span>
                    {render(line.text)}
                  </p>
                ))}
                {!shown.length && <p className="empty">The conversation shows up here as it happens.</p>}
              </div>
            </section>
          </aside>
        </main>
      </div>
    </div>
  );
}
