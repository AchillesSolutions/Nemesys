import { useState, type CSSProperties } from "react";
import { voice } from "../voice";
import { AskBar } from "./AskBar";
import type { Directory } from "../api";
import { LANGUAGE_NAMES, LEVELS, t } from "../i18n";
import type { HelpLevel, Language, MatchInfo, Profile } from "../types";

/** Before kick-off: team, language and how much help. */
export function Setup({ dir, match, onStart }: { dir: Directory; match: MatchInfo; onStart: (p: Profile) => void }) {
  const [team, setTeam] = useState<string | null>(null);
  const [language, setLanguage] = useState<Language>("en");
  const [level, setLevel] = useState<HelpLevel>("beginner");
  const lang = language;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  const [reply, setReply] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // "I'm new and I support Kilburn": the Host turns it into settings, and says so.
  const tellMe = async (text: string) => {
    setBusy(true);
    voice.enable(); // the tap or click that got us here allows audio
    try {
      const res = await fetch("/api/setup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, profile: { team, language, level } }),
      });
      const body = await res.json() as { answer: string; profile: Profile };
      setTeam(body.profile.team);
      setLanguage(body.profile.language);
      setLevel(body.profile.level);
      setReply(body.answer);
      voice.say([{ text: body.answer, tone: "hopeful", card_title: "", sources: ["host"], proposal_ids: [],
                   concepts: [], headline: false }], body.profile.language);
    } catch {
      setReply(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="setup">
      <header className="setup-head">
        <h1>Kick-Off</h1>
        <p>{t(lang, "tagline")}</p>
        <p className="fixture">
          {home.name} v {away.name} · {match.venue} · {dir.world.league}, matchweek {match.matchweek}
        </p>
      </header>

      <section className="setup-voice">
        <p className="setup-voice-label">{t(lang, "setupByVoice")}</p>
        <AskBar lang={lang} busy={busy} onAsk={tellMe} placeholder={t(lang, "setupHint")} />
        {reply && <p className="setup-reply">{reply}</p>}
      </section>

      <fieldset>
        <legend>{t(lang, "whoSupport")}</legend>
        <div className="choices">
          {[home, away].map((c) => (
            <button key={c.id} className={`choice team ${team === c.id ? "on" : ""}`} onClick={() => setTeam(c.id)}
                    style={{ "--club": c.colours[0], "--club2": c.colours[1] } as CSSProperties}>
              <span className="crest big" style={{ background: c.colours[0], borderColor: c.colours[1] }} />
              <strong>{c.short_name}</strong>
              <small>{c.nickname}</small>
            </button>
          ))}
          <button className={`choice team ${team === null ? "on" : ""}`} onClick={() => setTeam(null)}>
            <span className="crest big neutral" />
            <strong>{t(lang, "neutral")}</strong>
          </button>
        </div>
      </fieldset>

      <fieldset>
        <legend>{t(lang, "language")}</legend>
        <div className="choices">
          {(Object.keys(LANGUAGE_NAMES) as Language[]).map((l) => (
            <button key={l} className={`choice ${language === l ? "on" : ""}`} onClick={() => setLanguage(l)}>
              <strong>{LANGUAGE_NAMES[l]}</strong>
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>{t(lang, "howMuchHelp")}</legend>
        <div className="choices levels">
          {(Object.keys(LEVELS) as HelpLevel[]).map((l) => (
            <button key={l} className={`choice ${level === l ? "on" : ""}`} onClick={() => setLevel(l)}>
              <strong>{LEVELS[l][lang].name}</strong>
              <small>{LEVELS[l][lang].hint}</small>
            </button>
          ))}
        </div>
      </fieldset>

      <button className="btn primary start" onClick={() => onStart({ team, language, level })}>
        {t(lang, "start")} →
      </button>
    </main>
  );
}
