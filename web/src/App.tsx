import { useEffect, useMemo, useRef, useState } from "react";
import { Directory, loadStatic } from "./api";
import { BridgeBanner, Chapters, DemoControls, ProfileChips, ViewerSettings } from "./components/Controls";
import { VoiceStatus } from "./components/VoiceStatus";
import { listener, type ListenState } from "./listen";
import { Captions, TalkCard } from "./components/Captions";
import { Feed } from "./components/Feed";
import { Learned, LeagueTable, Stats, UnderTheHood } from "./components/Panels";
import { Pitch, Ticker } from "./components/Pitch";
import { Scoreboard } from "./components/Scoreboard";
import { Setup } from "./components/Setup";
import { LEVELS, t } from "./i18n";
import type { HelpLevel, Language, MatchInfo, Mode, Profile, World } from "./types";
import { sound } from "./sound";
import { voice } from "./voice";
import { useMatch } from "./useMatch";

/**
 * A viewer can be preset from the URL, skipping setup: ?team=kil&language=zh&level=beginner&speed=2
 * Used to open two side-by-side viewers for the demo.
 */
function presetFromUrl(): { profile: Profile | null; speed: number; mode: Mode; demo: boolean } {
  const q = new URLSearchParams(location.search);
  // Everyone watches the highlights flow (key moments, quiet play compressed, bridges between).
  // ?demo=1 is for judges and recording: a little faster, with playback controls and the agent panel.
  const demo = q.get("demo") === "1";
  const speed = Number(q.get("speed")) || (demo ? 1.5 : 1);
  const mode: Mode = q.get("mode") === "full" ? "full" : "key_moments";
  if (!q.has("language") && !q.has("team") && !q.has("level")) return { profile: null, speed, mode, demo };
  const language = (["en", "zh", "es"].includes(q.get("language") ?? "") ? q.get("language") : "en") as Language;
  const level = (Object.keys(LEVELS).includes(q.get("level") ?? "") ? q.get("level") : "beginner") as HelpLevel;
  return { profile: { team: q.get("team") || null, language, level, hands_free: q.get("hands_free") === "1" }, speed, mode, demo };
}

const PRESET = presetFromUrl();

export default function App() {
  const [data, setData] = useState<{ world: World; match: MatchInfo } | null>(null);
  const [failed, setFailed] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(PRESET.profile);

  useEffect(() => {
    loadStatic().then(setData).catch(() => setFailed(true));
    void voice.init();
  }, []);
  const dir = useMemo(() => (data ? new Directory(data.world) : null), [data]);

  if (failed) return <p className="boot">Can't reach the Kick-Off server. Is it running?</p>;
  if (!data || !dir) return <p className="boot">Kick-Off…</p>;
  if (!profile) {
    // The Kick off click is the user gesture browsers require before playing audio.
    return <Setup dir={dir} match={data.match} demo={PRESET.demo} onStart={setProfile} />;
  }
  return <Live dir={dir} match={data.match} profile={profile} />;
}

function Live({ dir, match, profile }: { dir: Directory; match: MatchInfo; profile: Profile }) {
  const { state, send } = useMatch(profile, PRESET.mode, PRESET.speed);
  const lang = state.profile.language;
  const [soundOn, setSoundOn] = useState(sound.enabled);
  const [voiceOn, setVoiceOn] = useState(voice.enabled);
  const [listen, setListen] = useState<{ state: ListenState; caption?: string }>({ state: listener.state });

  // Achilles listens for its name all match long; Space is push-to-talk.
  useEffect(() => {
    listener.mode = "wake";
    listener.on({
      state: (state, caption) => setListen({ state, caption }),
      command: (text, language) => send({ action: "ask", text, language }),
    });
    void listener.start(profile.language);
    const isField = (e: KeyboardEvent) => (e.target as HTMLElement).closest?.("select, input, textarea");
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !isField(e)) { e.preventDefault(); listener.push(true); }
    };
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && !isField(e)) listener.push(false); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [profile.language, send]);

  // Once the Host has answered, follow-ups need no name for a few seconds.
  useEffect(() => { if (state.asking === 0) listener.done(state.lastReply === "answer"); }, [state.asking, state.lastReply]);
  useEffect(() => listener.setHint(state.profile.language), [state.profile.language]);
  useEffect(() => { listener.handsFree = state.profile.hands_free; }, [state.profile.hands_free]);

  // A short hint on how to talk to Achilles, at kick-off and when hands-free changes (the talk card stays).
  const [hintUntil, setHintUntil] = useState(() => performance.now() + 15000);
  useEffect(() => setHintUntil(performance.now() + 15000), [state.profile.hands_free]);
  const [, tick] = useState(0);
  useEffect(() => { const id = setTimeout(() => tick((n) => n + 1), Math.max(0, hintUntil - performance.now()) + 50); return () => clearTimeout(id); }, [hintUntil]);
  const hint = performance.now() < hintUntil ? t(lang, state.profile.hands_free ? "hintAlone" : "hintWake") : null;
  const spokenSeq = useRef(0); // the newest feed entry already spoken

  // Speak each new piece of commentary once, as it arrives, but only if it is in the viewer's
  // current language (lines written just before a language switch are shown, not read out).
  useEffect(() => {
    const fresh = state.feed.filter((c) => (c.seq ?? 0) > spokenSeq.current).sort((x, y) => (x.seq ?? 0) - (y.seq ?? 0));
    for (const c of fresh) {
      spokenSeq.current = Math.max(spokenSeq.current, c.seq ?? 0);
      if (!c.language || c.language === state.profile.language) voice.say(c.lines, state.profile.language);
    }
  }, [state.feed, state.profile.language]);

  // Tell the server exactly when Achilles is speaking, so the next big moment waits for the end of a line.
  useEffect(() => voice.onLine((line) => send({ action: "voice", speaking: line !== null })) as () => void, [send]);

  // Achilles' answers to the viewer: spoken (and so subtitled) in the language they were written in.
  useEffect(() => {
    if (!state.reply) return;
    voice.say([{ text: state.reply.text, tone: "neutral", card_title: "", sources: ["host"], proposal_ids: [],
                 concepts: [], headline: true }], state.reply.language);
  }, [state.reply]);

  // A new language means the queued lines are in the wrong one.
  useEffect(() => voice.stop(), [state.profile.language]);

  const toggleVoice = () => {
    if (voiceOn) voice.disable(); else voice.enable();
    setVoiceOn(!voiceOn);
  };

  useEffect(() => {
    if (state.event) sound.onEvent(state.event, state.speed);
  }, [state.event, state.speed]);

  const toggleSound = () => {
    if (soundOn) sound.disable(); else void sound.enable();
    setSoundOn(!soundOn);
  };

  const toggles = (
    <span className="toggles">
      <button className="btn" onClick={toggleVoice} aria-pressed={voiceOn}
              title={voice.engine === "azure" ? "Azure AI Speech" : "Browser voice"}>
        {voiceOn ? "🗣" : "🤐"} {t(lang, "voice")}
      </button>
      <button className="btn" onClick={toggleSound} aria-pressed={soundOn}>
        {soundOn ? "🔊" : "🔇"} {t(lang, "sound")}
      </button>
    </span>
  );

  return (
    <div className="live" lang={lang === "zh" ? "zh-CN" : lang}>
      <header className="topbar">
        <span className="brand">Kick-Off</span>
        <Scoreboard dir={dir} match={match} snapshot={state.snapshot} lang={lang}
                    clock={{ event: state.event, arrivedAt: state.arrivedAt, nextGap: state.nextGap,
                             speed: state.speed * state.pace, paused: state.paused || state.bridge !== null }} />
        {!state.connected && <span className="status">{t(lang, "connecting")}</span>}
        {state.finished && <span className="status">{t(lang, "finished")}</span>}
      </header>
      {PRESET.demo
        ? <ViewerSettings dir={dir} match={match} profile={state.profile} send={send} extra={toggles} />
        : <ProfileChips dir={dir} profile={state.profile} extra={toggles} />}
      {PRESET.demo && (
        <DemoControls lang={lang} mode={state.mode} speed={state.speed} paused={state.paused} send={send} />
      )}
      <main className="grid">
        <div className="col-main">
          <div className="pitch-stack">
            <Pitch dir={dir} match={match} snapshot={state.snapshot} recent={state.recent}
                   nextGap={state.nextGap} speed={state.speed * state.pace} lang={lang} />
            <BridgeBanner bridge={state.bridge} lang={lang} />
            <VoiceStatus state={listen.state} caption={listen.caption} lang={lang} hint={hint}
                         handsFree={state.profile.hands_free} />
          </div>
          <Captions voiceOn={voiceOn} feed={state.feed} lang={lang} />
          {PRESET.demo && <Ticker dir={dir} event={state.event} lang={lang} />}
          {PRESET.demo && (
            <Chapters chapters={state.chapters} current={state.mode === "key_moments" ? state.chapter : null}
                      elapsed={state.snapshot?.elapsed ?? 0} lang={lang} send={send} />
          )}
        </div>
        <aside className="col-side">
          <LeagueTable dir={dir} match={match} snapshot={state.snapshot} lang={lang} />
          <Learned learned={state.learned} lang={lang} />
          <Stats dir={dir} snapshot={state.snapshot} lang={lang} />
          <TalkCard lang={lang} handsFree={state.profile.hands_free} />
          {PRESET.demo && <Feed feed={state.feed} lang={lang} />}
          {PRESET.demo && <UnderTheHood feed={state.feed} lang={lang} />}
        </aside>
      </main>
      {state.error && <div className="toast" role="alert">{state.error}</div>}
    </div>
  );
}
