import { useEffect, useMemo, useState } from "react";
import { Directory, loadStatic } from "./api";
import { Chapters, DemoControls, JumpBanner, ViewerSettings } from "./components/Controls";
import { Feed } from "./components/Feed";
import { Learned, LeagueTable, Stats, UnderTheHood } from "./components/Panels";
import { Pitch, Ticker } from "./components/Pitch";
import { Scoreboard } from "./components/Scoreboard";
import { Setup } from "./components/Setup";
import { LEVELS, t } from "./i18n";
import type { HelpLevel, Language, MatchInfo, Mode, Profile, World } from "./types";
import { sound } from "./sound";
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
  }, []);
  const dir = useMemo(() => (data ? new Directory(data.world) : null), [data]);

  if (failed) return <p className="boot">Can't reach the Kick-Off server. Is it running?</p>;
  if (!data || !dir) return <p className="boot">Kick-Off…</p>;
  if (!profile) {
    // The Kick off click is the user gesture browsers require before playing audio.
    const start = (p: Profile) => { void sound.enable(); setProfile(p); };
    return <Setup dir={dir} match={data.match} onStart={start} />;
  }
  return <Live dir={dir} match={data.match} profile={profile} />;
}

function Live({ dir, match, profile }: { dir: Directory; match: MatchInfo; profile: Profile }) {
  const { state, send } = useMatch(profile, PRESET.mode, PRESET.speed);
  const lang = state.profile.language;
  const [soundOn, setSoundOn] = useState(sound.enabled);

  useEffect(() => {
    if (state.event) sound.onEvent(state.event, state.speed);
  }, [state.event, state.speed]);

  const toggleSound = () => {
    if (soundOn) sound.disable(); else void sound.enable();
    setSoundOn(!soundOn);
  };

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
      <ViewerSettings dir={dir} match={match} profile={state.profile} send={send} extra={
        <button className="btn sound-toggle" onClick={toggleSound} aria-pressed={soundOn}>
          {soundOn ? "🔊" : "🔇"} {t(lang, "sound")}
        </button>} />
      {PRESET.demo && (
        <DemoControls lang={lang} mode={state.mode} speed={state.speed} paused={state.paused} send={send} />
      )}
      <main className="grid">
        <div className="col-main">
          <div className="pitch-stack">
            <Pitch dir={dir} match={match} snapshot={state.snapshot} recent={state.recent}
                   nextGap={state.nextGap} speed={state.speed} lang={lang} />
            {state.mode === "key_moments" && <JumpBanner chapter={state.jumpedTo} lang={lang} />}
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
          <UnderTheHood feed={state.feed} lang={lang} />
        </aside>
      </main>
      {state.error && <div className="toast" role="alert">{state.error}</div>}
    </div>
  );
}
