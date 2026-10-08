import { useEffect, useMemo, useRef, useState } from "react";
import { Directory, loadStatic } from "./api";
import { Chapters, DemoControls, JumpBanner, ProfileChips, ViewerSettings } from "./components/Controls";
import { VoiceStatus } from "./components/VoiceStatus";
import { listener, type ListenState } from "./listen";
import { Feed } from "./components/Feed";
import { Learned, LeagueTable, Stats, UnderTheHood } from "./components/Panels";
import { Pitch, Ticker } from "./components/Pitch";
import { Scoreboard } from "./components/Scoreboard";
import { Setup } from "./components/Setup";
import { LEVELS, t } from "./i18n";
import type { Commentary, HelpLevel, Language, MatchInfo, Mode, Profile, World } from "./types";
import { sound } from "./sound";
import { voice } from "./voice";
import { useMatch } from "./useMatch";

/**
 * A viewer can be preset from the URL, skipping setup: ?team=kil&language=zh&level=beginner&speed=2
 * Used to open two side-by-side viewers for the demo.
 */
function presetFromUrl(): { profile: Profile | null; speed: number; mode: Mode; demo: boolean } {
  const q = new URLSearchParams(location.search);
  // ?demo=1 is for judges and recording: Key moments at 1.5x with playback controls.
  // Without it, Kick-Off behaves like a live broadcast: the full match, in real time, no controls.
  const demo = q.get("demo") === "1";
  const speed = Number(q.get("speed")) || (demo ? 1.5 : 1);
  const mode: Mode = (q.get("mode") ?? (demo ? "key_moments" : "full")) === "full" ? "full" : "key_moments";
  if (!q.has("language") && !q.has("team") && !q.has("level")) return { profile: null, speed, mode, demo };
  const language = (["en", "zh", "es"].includes(q.get("language") ?? "") ? q.get("language") : "en") as Language;
  const level = (Object.keys(LEVELS).includes(q.get("level") ?? "") ? q.get("level") : "beginner") as HelpLevel;
  return { profile: { team: q.get("team") || null, language, level }, speed, mode, demo };
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

  // Once the Host has answered, go back to listening for the name.
  useEffect(() => { if (state.asking === 0) listener.done(); }, [state.asking]);
  useEffect(() => listener.setHint(state.profile.language), [state.profile.language]);
  const spoken = useRef<Commentary | null>(null);

  // Speak each new piece of commentary once, as it arrives.
  useEffect(() => {
    const latest = state.feed[0];
    if (!latest || latest === spoken.current) return;
    spoken.current = latest;
    voice.say(latest.lines, state.profile.language);
  }, [state.feed, state.profile.language]);

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
                             speed: state.speed, paused: state.paused }} />
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
                   nextGap={state.nextGap} speed={state.speed} lang={lang} />
            {state.mode === "key_moments" && <JumpBanner chapter={state.jumpedTo} lang={lang} />}
            <VoiceStatus state={listen.state} caption={listen.caption} lang={lang} />
          </div>
          <Ticker dir={dir} event={state.event} lang={lang} />
          <Chapters chapters={state.chapters} current={state.mode === "key_moments" ? state.chapter : null}
                    elapsed={state.snapshot?.elapsed ?? 0} lang={lang} send={send} />
          <div className="row">
            <Stats dir={dir} snapshot={state.snapshot} lang={lang} />
            <LeagueTable dir={dir} match={match} snapshot={state.snapshot} lang={lang} />
          </div>
        </div>
        <aside className="col-side">
          <Feed feed={state.feed} lang={lang} />
          <Learned learned={state.learned} lang={lang} />
          {PRESET.demo && <UnderTheHood feed={state.feed} lang={lang} />}
        </aside>
      </main>
      {state.error && <div className="toast" role="alert">{state.error}</div>}
    </div>
  );
}
