import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Directory } from "../api";
import { LANGUAGE_NAMES, LEVELS, t } from "../i18n";
import { listener, type ListenState } from "../listen";
import { sound } from "../sound";
import type { HelpLevel, Language, Line, MatchInfo, Profile } from "../types";
import { voice } from "../voice";
import { VoiceStatus } from "./VoiceStatus";

type Stage = "splash" | "greeting" | "listening" | "thinking" | "starting";

const NO_ANSWER_MS = 15000;

const say = (text: string, tone: Line["tone"] = "hopeful"): Line =>
  ({ text, tone, card_title: "", sources: ["host"], proposal_ids: [], concepts: [], headline: false });

/**
 * Before kick-off, Achilles introduces itself and asks who the viewer supports and how much they know.
 * The answer, in any of the three languages, becomes their settings; then the match starts.
 * With ?demo=1 the choices can also be clicked, as a fallback for testing without a microphone.
 */
export function Setup({ dir, match, demo, onStart }: {
  dir: Directory; match: MatchInfo; demo: boolean; onStart: (p: Profile) => void;
}) {
  const [stage, setStage] = useState<Stage>("splash");
  const [profile, setProfile] = useState<Profile>({ team: null, language: "en", level: "beginner", hands_free: false });
  const [listen, setListen] = useState<{ state: ListenState; caption?: string }>({ state: "off" });
  const [reply, setReply] = useState<string | null>(null);
  const profileRef = useRef(profile);
  profileRef.current = profile;
  const prompted = useRef(0);
  const lang = profile.language;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;

  const begin = async () => {
    if (stage !== "splash") return;
    setStage("greeting");
    await sound.enable();
    voice.enable();
    listener.on({ state: (state, caption) => setListen({ state, caption }) });
    void listener.start("en");
    listener.mode = "open"; // during setup, anything said is an answer
    await voice.sayAndWait([say(t("en", "greeting"))], "en");
    await voice.sayAndWait([say(t("zh", "greetingOther"))], "zh");
    await voice.sayAndWait([say(t("es", "greetingOther"))], "es");
    setStage("listening");
  };

  // Press any key (or tap) to start: the one interaction browsers require before audio.
  useEffect(() => {
    if (stage !== "splash") return;
    const go = (e: KeyboardEvent | PointerEvent) => {
      if ((e.target as HTMLElement).closest?.("button, select")) return;
      void begin();
    };
    window.addEventListener("keydown", go);
    window.addEventListener("pointerdown", go);
    return () => { window.removeEventListener("keydown", go); window.removeEventListener("pointerdown", go); };
  });

  // The viewer's answer.
  useEffect(() => {
    listener.on({
      command: async (text, spoken) => {
        if (stage !== "listening") { listener.done(); return; } // heard during the greeting: not an answer yet
        setStage("thinking");
        try {
          const res = await fetch("/api/setup", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, spoken, profile: profileRef.current }),
          });
          const body = await res.json() as { answer: string; profile: Profile };
          setProfile(body.profile);
          setReply(body.answer);
          listener.done();
          await voice.sayAndWait([say(body.answer)], body.profile.language);
          start(body.profile);
        } catch {
          listener.done();
          setStage("listening");
        }
      },
    });
  });

  // Nobody answered: ask once more, then start with what we have.
  useEffect(() => {
    if (stage !== "listening") return;
    const id = setTimeout(async () => {
      if (prompted.current++ === 0) {
        await voice.sayAndWait([say(t(lang, "setupPrompt"))], lang);
      } else {
        start(profileRef.current);
      }
    }, NO_ANSWER_MS);
    return () => clearTimeout(id);
  }, [stage, lang, listen.caption]);

  const start = (p: Profile) => {
    setStage("starting");
    listener.mode = "wake";
    void voice.sayAndWait([say(t(p.language, "letsGo"), "excited")], p.language);
    onStart(p);
  };

  if (stage === "splash") {
    return (
      <main className="splash" onClick={() => void begin()}>
        <h1>Kick-Off</h1>
        <p className="tagline">{t("en", "tagline")}</p>
        <p className="fixture">{home.name} v {away.name} · {match.venue}</p>
        <p className="press">{t("en", "pressToStart")}</p>
      </main>
    );
  }

  const set = (changes: Partial<Profile>) => setProfile({ ...profile, ...changes });
  return (
    <main className={`setup ${demo ? "" : "voice-only"}`}>
      <header className="setup-head">
        <h1>Kick-Off</h1>
        <p className="fixture">{home.name} v {away.name} · {match.venue} · {dir.world.league}, matchweek {match.matchweek}</p>
      </header>

      <section className="setup-voice">
        <p className="achilles">{reply ?? t(lang, "greeting")}</p>
        <p className="hands-free-explain">{t(lang, "handsFreeExplain")}</p>
        <VoiceStatus state={listen.state} caption={listen.caption} lang={lang} inline
                     handsFree={stage === "listening"} />
      </section>

      <fieldset>
        <legend>{t(lang, "whoSupport")}</legend>
        <div className="choices">
          {[home, away].map((c) => (
            <button key={c.id} className={`choice team ${profile.team === c.id ? "on" : ""}`} onClick={() => set({ team: c.id })}
                    style={{ "--club": c.colours[0], "--club2": c.colours[1] } as CSSProperties} tabIndex={demo ? 0 : -1}>
              <span className="crest big" style={{ background: c.colours[0], borderColor: c.colours[1] }} />
              <strong>{c.short_name}</strong>
              <small>{c.nickname}</small>
            </button>
          ))}
          <button className={`choice team ${profile.team === null ? "on" : ""}`} onClick={() => set({ team: null })}
                  tabIndex={demo ? 0 : -1}>
            <span className="crest big neutral" />
            <strong>{t(lang, "neutral")}</strong>
          </button>
        </div>
      </fieldset>

      <fieldset>
        <legend>{t(lang, "language")}</legend>
        <div className="choices">
          {(Object.keys(LANGUAGE_NAMES) as Language[]).map((l) => (
            <button key={l} className={`choice ${profile.language === l ? "on" : ""}`} onClick={() => set({ language: l })}
                    tabIndex={demo ? 0 : -1}>
              <strong>{LANGUAGE_NAMES[l]}</strong>
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>{t(lang, "howMuchHelp")}</legend>
        <div className="choices levels">
          {(Object.keys(LEVELS) as HelpLevel[]).map((l) => (
            <button key={l} className={`choice ${profile.level === l ? "on" : ""}`} onClick={() => set({ level: l })}
                    tabIndex={demo ? 0 : -1}>
              <strong>{LEVELS[l][lang].name}</strong>
              <small>{LEVELS[l][lang].hint}</small>
            </button>
          ))}
        </div>
      </fieldset>

      {demo && (
        <label className="hands-free-toggle">
          <input type="checkbox" checked={profile.hands_free} onChange={(e) => set({ hands_free: e.target.checked })} />
          <span>{t(lang, "handsFree")}</span>
        </label>
      )}
      {profile.hands_free && !demo && <p className="chip big hands-free">🎙 {t(lang, "handsFree")}</p>}
      {demo && <button className="btn primary start" onClick={() => start(profile)}>{t(lang, "start")} →</button>}
    </main>
  );
}
