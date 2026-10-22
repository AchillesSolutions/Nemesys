import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Directory, loadStatic } from "../api";
import { Crest } from "../components/Crest";
import { Fixture, Score } from "../components/Fixture";
import type { MatchEvent, MatchInfo, Snapshot } from "../types";
import { type AgentReport, AgentsFlow } from "./Agents";
import { type Handoff, HandoffLayer } from "./Handoffs";
import { type MemoryItem, MemoryBox } from "./Memory";
import { addPiece, type Line } from "./lines";
import { LiveCall } from "./live";
import { hostSay, listen, prepare } from "./lobby";
import { LanguagePick } from "./LanguagePick";
import { Logo } from "./Logo";

const ECHO_MS = 900; // without headphones the recogniser hears the host's voice for a moment after it stops
const TRANSLATE_AFTER_MS = 1200; // a rival's line in another language is translated once it has settled

/** Mostly the host's own words, heard back through the speakers? */
function echoOf(heard: string, said: string): boolean {
  // Words, or (in Chinese, which has no spaces) characters.
  const words = (t: string) => t.toLowerCase().match(/[a-z\u00e0-\u00ff']+|[\u4e00-\u9fff]/g) ?? [];
  const h = words(heard), s = new Set(words(said.replace(/\[\/?(zh|es)\]/g, "")));
  // A short answer is always the viewer's: "Kilburn" is in the host's question, but it's also the answer to it.
  return h.length >= 4 && h.filter((w) => s.has(w)).length / h.length >= 0.7;
}
import { highlight, nameMatcher, type NameMatcher } from "./names";
import { Timeline, type TimelineMoment } from "./Timeline";

/**
 * The Rival on the matchday screen. A lobby host (not the rival) asks, by voice, who you support and which
 * rival you'd like: the one you watched with last time, or someone new. Then the rival joins, greets you and
 * the match plays, fast-forwarding quiet play so the whole thing fits in about two minutes. The real product
 * is voice only, with no screen (the match is on the viewer's TV): this is the demo view, with the conversation
 * as captions, the score and a timeline at the top, and the agents at work along the bottom. The lobby's crests
 * and rival cards can be tapped, for testing only.
 */

type Moment = TimelineMoment & { big: boolean };
interface RivalCard { id: string; name: string; home: string; club: string; language: string; watched: boolean; last: string | null }
interface Lobby { stage: "language" | "name" | "side" | "rival" | "done"; viewer?: string; language?: string; team: string | null; say: string | null; online: RivalCard[];
  offered: string[]; rival: RivalCard | null }
interface Play {
  event: MatchEvent | null; snapshot: Snapshot | null; arrivedAt: number; nextGap: number | null;
  pace: number; // match seconds per real second for the stretch after this event
}
type Status = "idle" | "lobby" | "matching" | "live" | "reconnecting" | "ended";

const SHOWN_LINES = 6; // the conversation window: roughly the last few exchanges
const MATCHING_MIN_MS = 3000; // the "connecting you to a rival" screen stays up at least this long
const COST_PER_S = 0.0008; // third-party listing for gpt-live-1; not yet on Azure's price list
const SOUNDS: Record<string, string> = { laugh: "laughs", laughs: "laughs", sigh: "sighs", groan: "groans",
  gasp: "gasps", scoff: "scoffs", chuckle: "chuckles", cheer: "cheers", boo: "boos" };
const NO_PLAY: Play = { event: null, snapshot: null, arrivedAt: 0, nextGap: null, pace: 40 };
const MAX_RECONNECTS = 4;
// ?mode=live (real time) or ?mode=short (testing); the default is the ten-minute match.
// ?from=80 joins the match at the 80th minute, for recording a particular scene.
const QUERY = new URLSearchParams(location.search);
const MODE = ["live", "short"].includes(QUERY.get("mode") ?? "") ? QUERY.get("mode")! : "match";
const FROM = Number(QUERY.get("from") ?? 0) || 0;
// Free ways to check the screen without paying for a voice: ?replay=last plays the last recorded session back
// exactly; ?dry=1 runs the real match and agents with a text stand-in for the rival (&team=kil&rival=dez).
const REPLAY = QUERY.get("replay");
const DRY = QUERY.get("dry") === "1";

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

/** Why the microphone can't be used, in plain words, or null if it can. Asks for permission the first time. */
async function microphoneProblem(): Promise<string | null> {
  if (!navigator.mediaDevices?.getUserMedia) {
    return "This browser can't use a microphone here. Try Edge or Chrome, on an https address.";
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop()); // just checking: the lobby and the rival open their own
    return null;
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      return "No microphone found. Nemesys is a conversation: connect a microphone or headset, then press Connect again.";
    }
    if (name === "NotReadableError") {
      return "Your microphone is busy in another app. Close it there, then press Connect again.";
    }
    return "Nemesys needs your microphone. Allow access in the browser, then press Connect again.";
  }
}

/** The lobby's questions on screen, in the viewer's language, with English beneath for anyone watching. */
const ASKED: Record<string, Record<string, string>> = {
  English: { name: "Who am I talking to?", side: "Who are you supporting?", rival: "Who do you want to be matched with?",
             one: "Who do you want to be matched with?" },
  Mandarin: { name: "请问怎么称呼？", side: "你支持哪个队？", rival: "你想和谁一起看？", one: "要一起看吗？" },
  Spanish: { name: "¿Cómo te llamas?", side: "¿Con quién vas?", rival: "¿Con quién quieres ver el partido?",
             one: "¿Quieres ver el partido juntos?" },
};
const SAY: Record<string, Record<string, string>> = {
  English: { name: "Say your name", side: "Say your club" },
  Mandarin: { name: "说出你的名字", side: "说出你的球队" },
  Spanish: { name: "Di tu nombre", side: "Di tu equipo" },
};

function Asked({ lang = "English", what }: { lang?: string; what: string }) {
  return (
    <>
      <h2>{ASKED[lang]?.[what] ?? ASKED.English[what]}</h2>
      {lang !== "English" && <p className="lobby-english">{ASKED.English[what]}</p>}
    </>
  );
}

function said(lang = "English", what: string): string {
  return SAY[lang]?.[what] ?? SAY.English[what];
}

export default function RivalApp() {
  const [data, setData] = useState<{ dir: Directory; match: MatchInfo & { lineup?: Record<string, number[]> } } | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [moments, setMoments] = useState<Moment[]>([]);
  const [paused, setPaused] = useState(false);
  const [booking, setBooking] = useState<{ reason: string; count: number; key: number } | null>(null);
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [recalling, setRecalling] = useState<number | null>(null);
  const [receipt, setReceipt] = useState<{ quote: string; when: string; bet: boolean; key: number } | null>(null);
  const [goal, setGoal] = useState<{ colour: string; key: number } | null>(null);
  const pausedAt = useRef(0);
  const [english, setEnglish] = useState<Record<number, { src: string; text: string }>>({}); // by line id
  const translating = useRef(new Set<number>());
  const hostEndedAt = useRef(0);
  const hostLine = useRef("");
  const welcome = useRef("");
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
  const lastRivalWord = useRef(0);
  const started = useRef(0);

  useEffect(() => {
    loadStatic()
      .then(({ world, match }) => setData({ dir: new Directory(world), match }))
      .catch(() => setError("Couldn't reach the server"));
    // The host's welcome is fetched and decoded while the page loads, so it plays the instant Start is pressed.
    fetch("/api/rival/welcome").then((r) => r.json()).then((w) => { welcome.current = w.text; void prepare(w.text); })
      .catch(() => {});
    return () => { call.current?.close(); stream.current?.close(); stopListening.current?.(); };
  }, []);

  // A replay or dry run can start by itself (&auto=1): no voice, so no click is needed for sound.
  useEffect(() => {
    if (data && (REPLAY || DRY) && QUERY.get("auto") === "1" && status === "idle") void start();
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  // A clock for the agent pulses, and billing while the rival's voice is open.
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now() / 1000);
      if (started.current) setSeconds((s) => Math.max(s, (Date.now() - started.current) / 1000));
    }, 500);
    return () => clearInterval(timer);
  }, []);

  /** The match stream: events for the score and clock, moments for the timeline, what the agents are doing. */
  function follow(watch: string) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/rival/${watch}/stream`);
    stream.current = ws;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "transcript") {
        // Through the server, with when each piece was said, so lines are in the order they were spoken.
        if (msg.who === "rival") lastRivalWord.current = Date.now();
        setLines((prev) => addPiece(prev, msg.who, msg.delta, msg.start_ms, msg.end_ms));
      } else if (msg.type === "card") {
        // The Referee has booked the viewer: a yellow card in the middle of the screen, then it goes.
        const key = Date.now();
        setBooking({ reason: msg.reason, count: msg.count, key });
        setTimeout(() => setBooking((b) => (b?.key === key ? null : b)), 4500);
      } else if (msg.type === "state") {
        setPhase(msg.phase);
        if (msg.agents) setReports(msg.agents);
        if (msg.memory) setMemory(msg.memory);
      } else if (msg.type === "agent") {
        setReports((r) => ({ ...r, [msg.id]: { text: msg.text, working: msg.working, at: msg.at, status: msg.status } }));
      } else if (msg.type === "handoff") {
        handOff(msg.from, msg.to, msg.via);
      } else if (msg.type === "memory") {
        // What the rival remembers: a new memory, or one being recalled (it lights up while the dot flies out).
        if (msg.op === "add") setMemory((m) => (m.some((x) => x.id === msg.id) ? m : [...m, { id: msg.id, kind: msg.kind, text: msg.text }]));
        else if (msg.op === "recall") {
          setRecalling(msg.id);
          setTimeout(() => setRecalling((r) => (r === msg.id ? null : r)), 3200);
        }
      } else if (msg.type === "receipt") {
        // Demo only: the viewer's own words, on screen as the rival throws them back.
        const key = Date.now() + Math.random();
        setReceipt({ quote: msg.quote, when: msg.when, bet: msg.kind === "bet", key });
        setTimeout(() => setReceipt((r) => (r?.key === key ? null : r)), 7000);
      } else if (msg.type === "moment") {
        setMoments((m) => [...m, { minute: msg.minute, text: msg.text, big: msg.big, kind: msg.kind,
          team: msg.team, elapsed: msg.elapsed }]);
        if (!msg.past && msg.kind === "goal" && msg.big) scored(msg.team);
      } else if (msg.type === "event") {
        setPlay((p) => ({
          event: msg.event, snapshot: msg.snapshot, arrivedAt: performance.now(), nextGap: msg.next_gap,
          pace: msg.pace ?? p.pace,
        }));
      }
    };
  }

  /** One agent passing its work to the next: a dot on the demo view. */
  function handOff(from: string, to: string, via?: string) {
    setHandoffs((h) => [...h.slice(-7), { from, to, via, key: Date.now() + Math.random() }]);
  }

  /** A goal, for the demo: the screen flashes the scorers' colour. */
  function scored(team: string) {
    const key = Date.now();
    setGoal({ colour: data?.dir.colour(team) ?? "#ffffff", key });
    setTimeout(() => setGoal((g) => (g?.key === key ? null : g)), 1600);
  }

  // -- the lobby -----------------------------------------------------------------------------------

  /** A replay or a dry run: no lobby and no voice, straight to the match. */
  async function startWithoutVoice() {
    const url = REPLAY ? `/api/rival/replay?which=${encodeURIComponent(REPLAY)}`
      : `/api/rival/dry?team=${QUERY.get("team") ?? "kil"}&rival=${QUERY.get("rival") ?? "dez"}&mode=${MODE}&start=${FROM}`;
    const resp = await fetch(url, { method: "POST" });
    if (!resp.ok) {
      setError((await resp.json().catch(() => ({ detail: resp.statusText }))).detail);
      setStatus("ended");
      return;
    }
    const first: Lobby & { id: string } = await resp.json();
    watchId.current = first.id;
    setLobby(first);
    follow(first.id);
    started.current = Date.now();
    setStatus("live");
  }

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
    setEnglish({});
    setHandoffs([]);
    setMemory([]);
    joining.current = false;
    stopping.current = false;
    reconnects.current = 0;
    started.current = 0;
    stream.current?.close();
    if (REPLAY || DRY) return startWithoutVoice();
    // Nemesys is a conversation: without a microphone there's nothing to do, so say so before the host starts.
    const missing = await microphoneProblem();
    if (missing) {
      setError(missing);
      return;
    }
    setStatus("lobby");
    // The welcome starts the moment Start is pressed, while the watch is set up behind it.
    const greeting = welcome.current ? speak(welcome.current, "Asking your language") : null;
    let first: Lobby & { id: string };
    try {
      first = await fetch(`/api/rival/watch?mode=${MODE}&start=${FROM}`, { method: "POST" }).then((r) => r.json());
    } catch {
      setError("Couldn't reach the server");
      return;
    }
    watchId.current = first.id;
    follow(first.id);
    void update(greeting ? { ...first, say: null } : first); // the welcome is already being said
    stopListening.current = await listen((text, lang) => {
      if (hostTalking.current || joining.current) return; // the host's own voice, or too late
      if (Date.now() - hostEndedAt.current < ECHO_MS || echoOf(text, hostLine.current)) return;
      setHeard(text);
      void post("heard", { text, lang });
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
    if (next.say) await speak(next.say, next.stage === "language" ? "Asking your language"
      : next.stage === "name" ? "Asking your name"
      : next.stage === "side" ? "Asking who you support" : "Offering you a rival");
    setHost({ working: true, text: "Listening" });
  }

  async function speak(line: string, what: string) {
    hostTalking.current = true;
    hostLine.current = line;
    setHost({ working: true, text: what });
    setHeard("");
    await hostSay(line);
    hostTalking.current = false;
    hostEndedAt.current = Date.now();
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
        onTranscript: () => {}, // the conversation comes through the server's stream, in the order it was said
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
        if (!c.resumed) handOff("host", "rival");
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
      setError("Lost the rival's voice and couldn't get it back. Press Connect again.");
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

  /** Pause to take a call or make a note: the clock stops, and neither of you hears the other. */
  async function togglePause() {
    const next = !paused;
    setPaused(next);
    call.current?.setPaused(next);
    if (next) {
      pausedAt.current = performance.now();
    } else {
      const away = performance.now() - pausedAt.current;
      setPlay((p) => ({ ...p, arrivedAt: p.arrivedAt + away })); // the clock carries on from where it stopped
    }
    if (watchId.current) await fetch(`/api/rival/${watchId.current}/pause?paused=${next}`, { method: "POST" });
  }

  // Anything said in another language, by either of you, is shown in English too once the line has settled,
  // so whoever is watching (a judge, a friend) can follow a match enjoyed in Mandarin or Spanish.
  useEffect(() => {
    lines.forEach((line) => {
      const i = line.id;
      const settled = Date.now() - line.at > TRANSLATE_AFTER_MS;
      if (!settled || english[i]?.src === line.text || translating.current.has(i)) return;
      const src = line.text;
      const plain = src.replace(/\[[^\]]*\]/g, "").trim();
      if (!plain) return;
      translating.current.add(i);
      fetch("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texts: [plain], to: "en" }) })
        .then((r) => (r.ok ? r.json() : null))
        .then((r) => { if (r) setEnglish((e) => ({ ...e, [i]: { src, text: r.from[0] === "en" ? "" : r.texts[0] } })); })
        .catch(() => {})
        .finally(() => translating.current.delete(i));
    });
  }, [lines, now, english]);

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

  const lastMoment = moments[moments.length - 1];

  return (
    <div className="rival-live">
      <svg className="rival-pitch-bg" viewBox="0 0 105 68" preserveAspectRatio="xMidYMid slice" aria-hidden>
        <rect x="1" y="1" width="103" height="66" />
        <line x1="52.5" y1="1" x2="52.5" y2="67" />
        <circle cx="52.5" cy="34" r="9.15" />
        <rect x="1" y="13.85" width="16.5" height="40.3" />
        <rect x="87.5" y="13.85" width="16.5" height="40.3" />
        <rect x="1" y="24.85" width="5.5" height="18.3" />
        <rect x="98.5" y="24.85" width="5.5" height="18.3" />
      </svg>

      <header className="rival-top">
        <Fixture dir={dir} match={match} home={play.snapshot?.home} away={play.snapshot?.away} lang="en">
          {play.event ? (
            <Score snapshot={play.snapshot} lang="en"
                   clock={{ event: play.event, arrivedAt: play.arrivedAt, nextGap: play.nextGap, speed: play.pace, paused }} />
          ) : (
            <>
              <div className="score kickoff-time">20:00</div>
              <p className="match-clock">
                {phase === "prematch" ? "Teams are out · kick-off any second" : `Kick-off · ${match.venue}`}
              </p>
            </>
          )}
        </Fixture>
        <Timeline dir={dir} moments={moments} elapsed={play.event?.elapsed ?? 0} duration={match.duration} />
      </header>

      <main className="rival-main">
        <section className="rival-chat" aria-live="polite" aria-label="The conversation">
          <div className="rival-chat-lines">
            {shown.map((line, i) => (
              <p key={line.id}
                 className={`rival-line rival-line-${line.who}`}
                 style={{ opacity: 0.3 + 0.7 * ((i + 1) / shown.length) }}>
                <span className="rival-who">{line.who === "user" ? "You" : rival?.name ?? "Rival"}</span>
                {english[line.id]?.text ? (
                  <>
                    {render(english[line.id].text, names)}
                    <span className="rival-original">{render(line.text, null)}</span>
                  </>
                ) : render(line.text, names)}
              </p>
            ))}
            {!shown.length && status !== "idle" && !inLobby && <p className="empty">The conversation shows up here as it happens.</p>}
          </div>

        </section>

        <aside className="rival-side">
          <section className="demo-note" aria-label="About this screen">
            <p className="demo-note-title"><span className="demo-dot" aria-hidden />Demo view<span className="short"> · Nemesys is audio only</span></p>
            <p>Nemesys is audio only: you just talk. This screen shows, for the demo, what's happening behind the voice.</p>
          </section>
          {rival && dir.club(rival.club) ? (
            <section className="rival-profile">
              <p className="lobby-kicker">Watching with</p>
              <div className="rival-profile-head">
                <Crest club={dir.club(rival.club)!} className="crest-big" />
                <div>
                  <p className="rival-card-name">{rival.name}</p>
                  <p className="rival-card-club" style={{ color: dir.colour(rival.club) }}>{dir.club(rival.club)!.name} fan</p>
                </div>
              </div>
              <p className="rival-profile-line">{rival.home}{rival.language !== "English" ? ` · speaks ${rival.language}` : ""}
                {" · "}{rival.watched && rival.last ? `last time ${rival.last}` : "first time watching together"}</p>
            </section>
          ) : (
            <section className="rival-profile empty-profile">
              <p className="lobby-kicker">Watching with</p>
              <p className="empty">Your rival appears here once the lobby matches you.</p>
            </section>
          )}
          {(userClub || (status !== "idle" && status !== "ended") || started.current > 0) && <section className="rival-controls">
            {userClub &&<p className="rival-sides">Supporting <b style={{ color: dir.colour(userClub.id) }}>{userClub.short_name}</b></p>}
            {status !== "idle" && status !== "ended" && (
              <div className="rival-buttons">
                <button className="btn" onClick={stop}>End</button>
                <button className="btn" onClick={() => void togglePause()} disabled={!live}>{paused ? "Resume" : "Pause"}</button>
              </div>
            )}
            {started.current > 0 && <p className="rival-meter">{Math.round(seconds)}s · ~${(seconds * COST_PER_S).toFixed(2)}</p>}
            {live && notice && <p className="rival-notice">{notice}</p>}
          </section>}
          <MemoryBox items={memory} recalling={recalling} />
        </aside>
      </main>

      {/* Until the rival connects, the screen is a black curtain: the welcome, then the lobby. It lifts to
          reveal the match once the rival is with you, and comes back down when the match is over. */}
      <div className={`curtain ${inLobby || status === "idle" || status === "ended" ? "down" : "up"}`}>
        <Logo />
        {status === "ended" && play.snapshot && (
          // The match is over: the result, who you watched it with, and that's it.
          <div className="curtain-body game-over">
            <p className="game-over-result">
              <span>{home.name}</span>
              <b>{play.snapshot.home.score}<i>-</i>{play.snapshot.away.score}</b>
              <span>{away.name}</span>
            </p>
            {rival && <p className="curtain-fixture">Your rival: <b style={{ color: dir.colour(rival.club) }}>{rival.name}</b>, from {rival.home}</p>}
            <h1>Game over</h1>
            {error && <p className="rival-error" role="alert">{error}</p>}
          </div>
        )}
        {(status === "idle" || (status === "ended" && !play.snapshot)) && (
          <div className="curtain-body">
            <h1>Enjoy football with a rival</h1>
            {(REPLAY || DRY) && <p className="curtain-mode">{REPLAY ? "Replay of the last recorded session (free)" : "Dry run: text stand-in for the rival's voice"}</p>}
            <div className="opening-choices">
              <button className="opening-choice primary" onClick={() => void start()}>
                <b>Connect</b>
              </button>
            </div>
            <p className="opening-note">You'll need speakers and a microphone, or similar</p>
            {error && <p className="rival-error" role="alert">{error}</p>}
          </div>
        )}
        {inLobby && lobby && (
          <div className="curtain-body">
            {lobby.stage === "language" ? (
              <>
                <p className="lobby-kicker">The lobby</p>
                <h2>Your language</h2>
                <LanguagePick heard={heard} onPick={(_, label) => void post("heard", { text: label })} />
              </>
            ) : lobby.stage === "name" ? (
              <>
                <p className="lobby-kicker">The lobby</p>
                <Asked lang={lobby.language} what="name" />
                <p className="lobby-heard">{heard ? `\u201c${heard}\u201d` : said(lobby.language, "name")}</p>
                {notice && <button className="btn" onClick={() => void post("heard", { text: "skip" })}>Carry on without a name</button>}
              </>
            ) : lobby.stage === "side" ? (
              <>
                <p className="lobby-kicker">The lobby</p>
                <Asked lang={lobby.language} what="side" />
                <div className="lobby-crests">
                  {[home, away].map((c) => (
                    <button key={c.id} className="lobby-crest" onClick={() => void post("side", { team: c.id })}>
                      <Crest club={c} className="crest-big" />
                      <span>{c.name}</span>
                    </button>
                  ))}
                </div>
                <p className="lobby-heard">{heard ? `\u201c${heard}\u201d` : said(lobby.language, "side")}</p>
              </>
            ) : lobby.stage === "rival" ? (
              <>
                <p className="lobby-kicker">The lobby · {dir.club(lobby.online[0]?.club)?.short_name} fans online</p>
                <Asked lang={lobby.language} what={lobby.offered.length === 1 ? "one" : "rival"} />
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
              </>
            ) : (
              <>
                <p className="lobby-kicker">The lobby</p>
                <h2>Connecting you to {rival?.name ?? "your rival"}<span className="dots" aria-hidden><i /><i /><i /></span></h2>
                {rival && dir.club(rival.club) && (
                  <p className="curtain-fixture">{dir.club(rival.club)!.name} fan · {rival.home}{rival.language !== "English" ? ` · speaks ${rival.language}` : ""}</p>
                )}
              </>
            )}
            {notice && <p className="rival-notice">{notice}</p>}
          </div>
        )}
      </div>

      {booking && (
        <div className="booking" key={booking.key} role="alert">
          <div className="booking-card" aria-hidden />
          <div className="booking-text">
            <p className="booking-title">{booking.count > 1 ? "Another yellow card" : "Yellow card"}</p>
            <p className="booking-reason">{booking.reason}</p>
            <p className="booking-ask">{booking.count > 1 ? "That's another warning. Keep it friendly." : "Keep it friendly."}</p>
          </div>
        </div>
      )}

      {receipt && (
        <aside className="receipt" key={receipt.key} aria-live="polite">
          <p className="receipt-label">Receipts</p>
          {receipt.bet
            ? <p className="receipt-quote">{receipt.quote}</p>
            : <blockquote className="receipt-quote">{"\u201c"}{receipt.quote.replace(/^["\u201c]|["\u201d]$/g, "")}{"\u201d"}</blockquote>}
          <p className="receipt-when">{receipt.bet ? "The bet: time to pay up" : `You, ${receipt.when.charAt(0).toLowerCase()}${receipt.when.slice(1)}`}</p>
        </aside>
      )}

      <HandoffLayer handoffs={handoffs} />

      {goal && <div className="goal-flash" key={goal.key} aria-hidden style={{ "--flash": goal.colour } as CSSProperties} />}

      <footer className="rival-agents">
        <AgentsFlow reports={reports} now={now} beforeDone={live && phase !== "lobby"} active={{
          host: host && { ...host, status: host.working && host.text !== "Listening" ? "Talking" : host.text },
          match: lastMoment && reports.match === undefined ? { working: false, text: `${lastMoment.minute}\u2019 ${lastMoment.text}` } : undefined,
          rival: live ? { working: rivalSpeaking, text: rivalSpeaking ? "Talking" : "Listening",
                          status: rivalSpeaking ? "Talking" : "Listening" } : undefined,
        }} />
      </footer>
    </div>
  );
}
