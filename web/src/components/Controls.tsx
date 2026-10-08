import { useEffect, useState, type ReactNode } from "react";
import type { Directory } from "../api";
import { LANGUAGE_NAMES, LEVELS, t } from "../i18n";
import type { Chapter, Control, HelpLevel, Language, MatchInfo, Mode, Profile } from "../types";

const SPEEDS = [1, 1.5, 2, 4, 10];

/**
 * Playback controls for judges and recording the demo: pause, skip, speed, mode. A viewer of a live
 * match never sees these; they appear only when the page is opened with ?demo=1.
 */
export function DemoControls({ lang, mode, speed, paused, send }: {
  lang: Language; mode: Mode; speed: number; paused: boolean; send: (c: Control) => void;
}) {
  return (
    <div className="controls demo-controls" aria-label="Demo controls">
      <span className="demo-tag">DEMO</span>
      <button className="btn" onClick={() => send({ action: paused ? "resume" : "pause" })}>
        {paused ? `▶ ${t(lang, "play")}` : `❚❚ ${t(lang, "pause")}`}
      </button>
      <button className="btn" onClick={() => send({ action: "next_chapter" })}>⏭ {t(lang, "nextMoment")}</button>
      <select aria-label="Speed" value={speed} onChange={(e) => send({ action: "speed", value: Number(e.target.value) })}>
        {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      <div className="segmented" role="group" aria-label="Mode">
        {(["key_moments", "full"] as Mode[]).map((m) => (
          <button key={m} className={mode === m ? "on" : ""} onClick={() => send({ action: "mode", mode: m })}>
            {t(lang, m === "full" ? "fullMatch" : "keyMoments")}
          </button>
        ))}
      </div>
    </div>
  );
}

/** What the viewer has chosen, shown (not edited) on the TV screen: changes are made by voice. */
export function ProfileChips({ dir, profile, extra }: { dir: Directory; profile: Profile; extra?: ReactNode }) {
  const lang = profile.language;
  return (
    <div className="controls profile-chips">
      <span className="chip big">{t(lang, "supporting")}: {dir.club(profile.team)?.short_name ?? t(lang, "neutral")}</span>
      <span className="chip big">{LANGUAGE_NAMES[lang]}</span>
      <span className="chip big">{LEVELS[profile.level][lang].name}</span>
      {extra}
    </div>
  );
}

/** Demo fallback: the viewer's settings as pickers, for testing without a microphone. */
export function ViewerSettings({ dir, match, profile, send, extra }: {
  dir: Directory; match: MatchInfo; profile: Profile; send: (c: Control) => void; extra?: ReactNode;
}) {
  const lang = profile.language;
  return (
    <div className="controls viewer-settings">
      <label>
        <span>{t(lang, "teamSupported")}:</span>
        <select value={profile.team ?? ""} onChange={(e) => send({ action: "profile", team: e.target.value || null })}>
          <option value="">{t(lang, "neutral")}</option>
          {[match.home, match.away].map((id) => <option key={id} value={id}>{dir.club(id)!.short_name}</option>)}
        </select>
      </label>
      <label>
        <span>{t(lang, "language")}:</span>
        <select value={lang} onChange={(e) => send({ action: "profile", language: e.target.value as Language })}>
          {(Object.keys(LANGUAGE_NAMES) as Language[]).map((l) => <option key={l} value={l}>{LANGUAGE_NAMES[l]}</option>)}
        </select>
      </label>
      <label>
        <span>{t(lang, "guide")}:</span>
        <select value={profile.level} onChange={(e) => send({ action: "profile", level: e.target.value as HelpLevel })}>
          {(Object.keys(LEVELS) as HelpLevel[]).map((l) => <option key={l} value={l}>{LEVELS[l][lang].name}</option>)}
        </select>
      </label>
      {extra}
    </div>
  );
}

/**
 * Key moments the viewer has already seen, to rewatch. Moments still to come are never shown, and
 * the chapter playing now has no title until its moment has happened, so nothing is spoiled.
 */
export function Chapters({ chapters, current, elapsed, lang, send }: {
  chapters: Chapter[]; current: Chapter | null; elapsed: number; lang: Language; send: (c: Control) => void;
}) {
  const seen = chapters.filter((c) => c.moment <= elapsed || c.id === current?.id);
  if (seen.length === 0) return null;
  return (
    <nav className="chapters" aria-label={t(lang, "keyMoments")}>
      {seen.map((c) => {
        const playing = c.id === current?.id;
        const revealed = c.moment <= elapsed;
        return (
          <button key={c.id} className={playing ? "on" : ""} disabled={playing && !revealed}
                  onClick={() => send({ action: "seek_chapter", chapter: c.id })}>
            <span className="ch-minute">{revealed ? `${c.minute}'` : "●"}</span>
            <span className="ch-title">{revealed ? c.titles[lang] : t(lang, "nowPlaying")}</span>
          </button>
        );
      })}
    </nav>
  );
}

/** A short banner when Key moments skips ahead, so the jump in the clock makes sense. */
export function JumpBanner({ chapter, lang }: { chapter: Chapter | null; lang: Language }) {
  const [shown, setShown] = useState<number | null>(null);
  useEffect(() => {
    if (!chapter) return;
    setShown(chapter.id);
    const id = setTimeout(() => setShown(null), 2600);
    return () => clearTimeout(id);
  }, [chapter]);
  if (!chapter || shown !== chapter.id) return null;
  return <div className="jump-banner" role="status">⏩ {t(lang, "jumpingTo")} {chapter.minute}'</div>;
}
