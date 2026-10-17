import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Directory, loadStatic } from "../api";
import { Crest } from "../components/Crest";
import { Fixture, Score } from "../components/Fixture";
import { LeagueTable } from "../components/Panels";
import { Pitch } from "../components/Pitch";
import type { MatchEvent, MatchInfo, Snapshot } from "../types";
import { type AgentReport, AgentsStrip } from "./Agents";
import { LiveCall, type Speaker } from "./live";
import { hostSay, listen } from "./lobby";
import { highlight, nameMatcher, type NameMatcher } from "./names";

/**
 * The Rival on the matchday screen. A lobby host (not the rival) asks, by voice, who you support and which
 * rival you'd like: the one you watched with last time, or someone new. Then the rival joins, greets you and
 * the match plays, fast-forwarding quiet play so the whole thing fits in about two minutes. The real product
 * is voice only, with no screen: this is the demo view, with the agents at work under the pitch. The lobby's
 * crests and rival cards can be tapped, for testing only.
 */

interface Line { who: Speaker; text: string; at: number }
interface Moment { minute: string; text: string; big: boolean }
interface RivalCard { id: string; name: string; home: string; club: string; language: string; watched: boolean; last: string | null }
interface Lobby { stage: "side" | "rival" | "done"; team: string | null; say: string | null; online: RivalCard[];
  offered: string[]; rival: RivalCard | null }
interface Play {
  event: MatchEvent | null; snapshot: Snapshot | null; recent: MatchEvent[]; arrivedAt: number; nextGap: number | null;
  pace: number; // match seconds per real second for the stretch after this event
}
type Status = "idle" | "lobby" | "matching" | "live" | "reconnecting" | "ended";

const PAUSE_MS = 1800; // a fragment after this much quiet from the same speaker starts a new line
const SHOWN_LINES = 6; // the conversation window: roughly the last few exchanges
const TRAIL = 6;
const MATCHING_MIN_MS = 3000; // the "connecting you to a rival" screen stays up at least this long
const COST_PER_S = 0.0008; // third-party listing for gpt-live-1; not yet on Azure's price list
const SOUNDS: Record<string, string> = { laugh: "laughs", laughs: "laughs", sigh: "sighs", groan: "groans",
  gasp: "gasps", scoff: "scoffs", chuckle: "chuckles", cheer: "cheers", boo: "boos" };
const NO_PLAY: Play = { event: null, snapshot: null, recent: [], arrivedAt: 0, nextGap: null, pace: 40 };
const MAX_RECONNECTS = 4;
// The pitch and the table only redraw when the match moves, not on every word of the conversation.
const MatchPitch = memo(Pitch);
const Table = memo(LeagueTable);
const MODE = new URLSearchParams(location.search).get("mode") === "full" ? "full" : "short";

/** Transcript text: sound tags like [laugh] as stage directions (a half-arrived tag is hidden), names coloured. */
function render(text: string, names: NameMatcher | null): ReactNode[] {
  const parts: ReactNode[] = [];
  const clean = text.replace(/\[[^\]]*$/, "");
  let last = 0;
  for (const m of clean.matchAll(/\[([^\]]+)\]/g)) {
    parts.push(...highlight(clean.slice(last, m.index), names, last));
    const tag = m[1].trim().toLowerCase();
    parts.push(<em key={`s${m.index}`} className="rival-sound">({SOUNDS[tag] ?? tag})</em>);
    last = m.index! + m[0].length;
  }
  parts.push(...highlight(clean.slice(last), names, last));
  return parts;
}

export default function RivalApp() {
  const [data, setData] = useState<{ dir: Directory; match: MatchInfo & { lineup?: Record<string, number[]> } } | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [moments, setMoments] = useState<Moment[]>([]);
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [phase, setPhase] = useState("lobby");
  const [lobby, setLobby] = useState<Lobby | null>(null);
  const [reports, setReports] = useState<Record<string, AgentReport>>({});
  const [host, setHost] = useState<{ working: boolean; text: string } | undefined>(undefined);
  const [heard, setHeard] = useState("");
  const [play, setPlay] = useState<Play>(NO_PLAY);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const call = useRef<LiveCall | null>(null);
  const stream = useRef<WebSocket | null>(null);
  const watchId = useRef<string | null>(null);
  const stopListening = useRef<(() => void) | null>(null);
  const hostTalking = useRef(false);
  const joining = useRef(false);
  const stopping = useRef(false);
  const reconnects = useRef(0);
  const open = useRef<Partial<Record<Speaker, number>>>({}); // each speaker's line still being spoken
  const lastRivalWord = useRef(0);
  const started = useRef(0);

  useEffect(() => {
    loadStatic()
      .then(({ world, match }) => setData({ dir: new Directory(world), match }))
      .catch(() => setError("Couldn't reach the server"));
    return () => { call.current?.close(); stream.current?.close(); stopListening.current?.(); };
  }, []);

  // A clock for the agent pulses, and billing while the rival's voice is open.
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now() / 1000);
      if (started.current) setSeconds((s) => Math.max(s, (Date.now() - started.current) / 1000));
    }, 500);
    return () => clearInterval(timer);
  }, []);

  // Speakers overlap, so each keeps its own open line: a fragment joins that speaker's line unless they've paused.
  function addFragment(who: Speaker, text: string) {
    if (who === "rival") lastRivalWord.current = Date.now();
    setLines((prev) => {
      const at = Date.now();
      const i = open.current[who];
      if (i !== undefined && prev[i] && at - prev[i].at < PAUSE_MS) {
        const next = prev.slice();
        next[i] = { who, text: prev[i].text + text, at };
        return next;
      }
      open.current[who] = prev.length;
      return [...prev, { who, text: text.trimStart(), at }];
    });
  }

  /** The match stream: events for the pitch, moments for the feed, what the agents are doing. */
  function follow(watch: string) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/rival/${watch}/stream`);
    stream.current = ws;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "state") {
        setPhase(msg.phase);
        if (msg.agents) setReports(msg.agents);
      } else if (msg.type === "agent") {
        setReports((r) => ({ ...r, [msg.id]: { text: msg.text, working: msg.working, at: msg.at } }));
      } else if (msg.type === "moment") {
        setMoments((m) => [{ minute: msg.minute, text: msg.text, big: msg.big }, ...m].slice(0, 8));
      } else if (msg.type === "event") {
        setPlay((p) => ({
          event: msg.event, snapshot: msg.snapshot, arrivedAt: performance.now(), nextGap: msg.next_gap,
          pace: msg.pace ?? p.pace, recent: msg.event.elapsed - (p.event?.elapsed ?? 0) > 120 ? [msg.event]
            : [...p.recent, msg.event].slice(-TRAIL),
        }));
      }
    };
  }

  // -- the lobby -----------------------------------------------------------------------------------

  async function start() {
    if (!data) return;
    setError(null);
    setNotice(null);
    setLines([]);
    setMoments([]);
    setPlay(NO_PLAY);
    setReports({});
    setLobby(null);
    setHeard("");
    setSeconds(0);
    setPhase("lobby");
    open.current = {};
    joining.current = false;
    stopping.current = false;
    reconnects.current = 0;
    started.current = 0;
    stream.current?.close();
    let first: Lobby & { id: string };
    try {
      first = await fetch(`/api/rival/watch?mode=${MODE}`, { method: "POST" }).then((r) => r.json());
    } catch {
      setError("Couldn't reach the server");
      return;
    }
    watchId.current = first.id;
    follow(first.id);
    setStatus("lobby");
    void update(first);
    stopListening.current = await listen((text) => {
      if (hostTalking.current || joining.current) return; // the host's own voice, or too late
      setHeard(text);
      void post("heard", { text });
    }, (text) => { if (!hostTalking.current) setHeard(text); });
    if (!stopListening.current) setNotice("Speech recognition isn't available here; tap to choose (testing only).");
  }

  /** Testing only (the product is voice only): tap a crest or a rival card. */
  async function post(path: string, body: object) {
    const next = await fetch(`/api/rival/${watchId.current}/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (next) void update(next);
  }

  /** The lobby moved on: show it, have the host say what's next, and bring the rival in once chosen. */
  async function update(next: Lobby) {
    setLobby(next);
    if (next.stage === "done" && next.rival && !joining.current) {
      joining.current = true;
      stopListening.current?.();
      stopListening.current = null;
      void join(next.rival, next.say);
      return;
    }
    if (next.say) await speak(next.say, next.stage === "side" ? "Asking who you support" : "Offering you a rival");
    setHost({ working: true, text: "Listening" });
  }

  async function speak(line: string, what: string) {
    hostTalking.current = true;
    setHost({ working: true, text: what });
    setHeard("");
    await hostSay(line);
    hostTalking.current = false;
  }

  /** The rival is chosen: show the connection, and bring them in. */
  async function join(rival: RivalCard, say: string | null) {
    setStatus("matching");
    const shown = Date.now();
    if (say) void speak(say, `Connecting you to ${rival.name}`);
    try {
      await connect(watchId.current!, async () => {
        await new Promise((r) => setTimeout(r, Math.max(0, MATCHING_MIN_MS - (Date.now() - shown))));
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("ended");
    }
  }

  // -- the rival's voice ---------------------------------------------------------------------------

  /** Connect the rival's voice to the watch: the first time, or again after a drop. */
  async function connect(watch: string, beforeJoin?: () => Promise<void>) {
    call.current = await LiveCall.start(
      { watch },
      {
        onTranscript: addFragment,
        onUsage: (s) => setSeconds((prev) => Math.max(prev, s)),
        onClosed: (reason) => void dropped(reason),
        onError: (message, code) => (code === "content_filter"
          ? setNotice("Azure's content filter cut a line. The match carries on.")
          : setError(message)),
      },
      async (c) => {
        await beforeJoin?.();
        if (!started.current) started.current = Date.now();
        setHost({ working: false, text: "Matched" });
        setStatus("live");
        setNotice(c.resumed ? "Reconnected. The rival's picking up where it left off." : null);
        await fetch(`/api/rival/${c.watchId}/opened?resumed=${c.resumed}`, { method: "POST" });
      },
    );
  }

  /** The voice went away. Unless the viewer ended it, reconnect: a live game never stops. */
  async function dropped(reason: string) {
    if (stopping.current || reason === "close_requested") {
      setStatus("ended");
      stream.current?.close();
      return;
    }
    if (reconnects.current >= MAX_RECONNECTS || !watchId.current) {
      setError("Lost the rival's voice and couldn't get it back. Press Start again.");
      setStatus("ended");
      return;
    }
    reconnects.current += 1;
    setStatus("reconnecting");
    setNotice(reason === "content" ? "Azure's content filter ended the voice session. Reconnecting; the match carries on."
      : "The voice connection dropped. Reconnecting; the match carries on.");
    try {
      await connect(watchId.current);
    } catch {
      setTimeout(() => void dropped(reason), 1500);
    }
  }

  function stop() {
    stopping.current = true;
    stopListening.current?.();
    call.current?.close();
    stream.current?.close();
    if (watchId.current) void fetch(`/api/rival/${watchId.current}/end`, { method: "POST" });
    setHost(undefined);
    setStatus("ended");
  }

  function toggleMute() {
    call.current?.setMuted(!muted);
    setMuted(!muted);
  }

  const names = useMemo(() => (data ? nameMatcher(data.dir, [data.match.home, data.match.away]) : null), [data]);
  const shown = useMemo(() => lines.slice(-SHOWN_LINES), [lines]);
  if (!data) return <p className="boot">{error ?? "Loading…"}</p>;
  const { dir, match } = data;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  const live = status === "live" || status === "reconnecting";
  const inLobby = status === "lobby" || status === "matching";
  const userClub = dir.club(lobby?.team);
  const rival = lobby?.rival ?? null;
  const rivalSpeaking = Date.now() - lastRivalWord.current < 1500;

  return (
    <div className="live rival-live">
      <div className="screen">
        <Fixture dir={dir} match={match} home={play.snapshot?.home} away={play.snapshot?.away} lang="en">
          {play.event ? (
            <Score snapshot={play.snapshot} lang="en"
                   clock={{ event: play.event, arrivedAt: play.arrivedAt, nextGap: play.nextGap, speed: play.pace, paused: false }} />
          ) : (
            <>
              <div className="score kickoff-time">20:00</div>
              <p className="match-clock">
                {phase === "prematch" ? "Teams are out · kick-off any second" : `Kick-off · ${match.venue}`}
              </p>
            </>
          )}
        </Fixture>

        <main className="stage">
          <div className="stage-pitch">
            <MatchPitch dir={dir} match={match} snapshot={play.snapshot} recent={play.recent}
                   nextGap={play.nextGap} speed={play.pace} lang="en" lineup={match.lineup ?? null} />
            {inLobby && lobby && (
              <div className="lobby-overlay">
                {lobby.stage === "side" ? (
                  <div className="lobby-box glass">
                    <p className="lobby-kicker">Kick-Off lobby</p>
                    <h2>Who are you supporting?</h2>
                    <div className="lobby-crests">
                      {[home, away].map((c) => (
                        <button key={c.id} className="lobby-crest" onClick={() => void post("side", { team: c.id })}>
                          <Crest club={c} className="crest-big" />
                          <span>{c.name}</span>
                        </button>
                      ))}
                    </div>
                    <p className="lobby-heard">{heard ? `\u201c${heard}\u201d` : "Say your club"}</p>
                  </div>
                ) : lobby.stage === "rival" ? (
                  <div className="lobby-box glass">
                    <p className="lobby-kicker">Kick-Off lobby · {dir.club(lobby.online[0]?.club)?.short_name} fans online</p>
                    <h2>Who do you fancy?</h2>
                    <div className="lobby-rivals">
                      {lobby.online.filter((r) => lobby.offered.includes(r.id)).map((r) => (
                        <button key={r.id} className="lobby-rival" onClick={() => void post("rival", { id: r.id })}>
                          <span className="lobby-rival-name">{r.name}</span>
                          <span className="lobby-rival-home">{r.home}{r.language !== "English" && ` · speaks ${r.language}`}</span>
                          <span className={`lobby-rival-tag ${r.watched ? "seen" : ""}`}>{r.watched ? "Watched together" : "New"}</span>
                        </button>
                      ))}
                    </div>
                    <p className="lobby-heard">{heard ? `\u201c${heard}\u201d` : lobby.offered.length === 1 ? "\u201cRematch\u201d or \u201csomeone new\u201d" : "Say a name"}</p>
                  </div>
                ) : (
                  <div className="lobby-box glass matching">
                    <p className="lobby-kicker">Kick-Off lobby</p>
                    <h2>Connecting you to {rival?.name ?? "your rival"}<span className="dots" aria-hidden><i /><i /><i /></span></h2>
                    {rival && dir.club(rival.club) && (
                      <div className="rival-card">
                        <Crest club={dir.club(rival.club)!} className="crest-big" />
                        <div>
                          <p className="rival-card-name">{rival.name}</p>
                          <p className="rival-card-club">{dir.club(rival.club)!.name} fan · {rival.home}</p>
                          {rival.watched && rival.last && <p className="rival-card-last">Last time: {rival.last}</p>}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="stage-agents">
            <AgentsStrip reports={reports} now={now} active={{
              host: host?.working ?? false,
              rival: live && rivalSpeaking,
            }} />
          </div>

          <aside className="rail left">
            <section className="panel glass rival-session">
              <h2>The Rival <span className="sub">{started.current ? `${Math.round(seconds)}s · ~$${(seconds * COST_PER_S).toFixed(2)}` : ""}</span></h2>
              <div className="rival-buttons">
                {status !== "idle" && status !== "ended" ? (
                  <>
                    <button className="btn rival-primary" onClick={stop}>End</button>
                    <button className="btn" onClick={toggleMute} disabled={!live}>{muted ? "Unmute mic" : "Mute mic"}</button>
                  </>
                ) : (
                  <button className="btn rival-primary" onClick={() => void start()}>{status === "ended" ? "Start again" : "Start"}</button>
                )}
              </div>
              {userClub && <p className="rival-sides">Supporting: <b style={{ color: dir.colour(userClub.id) }}>{userClub.short_name}</b></p>}
              {notice && <p className="rival-notice">{notice}</p>}
              {error && <p className="rival-error" role="alert">{error}</p>}
              {rival && <p className="rival-sides">Watching with: <b>{rival.name}</b> <span className="empty">from {rival.home}</span></p>}
            </section>
            <Table dir={dir} match={match} snapshot={play.snapshot} lang="en" />
          </aside>

          <aside className="rail right">
            <section className="panel glass match-feed">
              <h2>Match feed</h2>
              {moments.length ? moments.slice(0, 5).map((m, i) => (
                <p key={`${m.minute}-${i}-${moments.length}`} className={`feed-moment ${m.big ? "big" : ""}`}>
                  <span className="feed-minute">{m.minute}’</span>{highlight(m.text.replace(/^[\d+]+': /, ""), names, i)}
                </p>
              )) : <p className="empty">The big moments show up here as they happen.</p>}
            </section>
            <section className="panel glass rival-chat" aria-live="polite">
              <h2>Conversation</h2>
              <div className="rival-chat-lines">
                {shown.map((line, i) => (
                  <p key={lines.length - shown.length + i}
                     className={`rival-line rival-line-${line.who}`}
                     style={{ opacity: 0.35 + 0.65 * ((i + 1) / shown.length) }}>
                    <span className="rival-who">{line.who === "user" ? "You" : rival?.name ?? "Rival"}</span>
                    {render(line.text, names)}
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
