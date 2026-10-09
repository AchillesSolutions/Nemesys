import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Directory } from "../api";
import { LANGUAGE_NAMES, LEVELS, t } from "../i18n";
import { listener, type ListenState } from "../listen";
import { sound } from "../sound";
import type { HelpLevel, Language, Line, MatchInfo, Profile } from "../types";
import { quickAnswer } from "../quickAnswer";
import { Crest } from "./Crest";
import { Fixture } from "./Fixture";
import { voice } from "../voice";
import { VoiceStatus } from "./VoiceStatus";

/**
 * The match lobby. After one key press (browsers need it before sound), Achilles welcomes the viewer
 * and asks one question at a time: which language (asked in all three, back to back), which team, how
 * familiar they are with football, and whether they are watching alone. The "Make it yours" options
 * light up as they answer, and anything already answered in an earlier reply is not asked again.
 * Kick-off waits until every choice is made: then a five-second countdown starts. If nobody answers,
 * each question is asked once more and then its default is used (a neutral beginner in English).
 * With ?demo=1 the options can also be clicked.
 */

type Stage = "splash" | "asking" | "thinking" | "tip" | "ready" | "started";
type Step = "language" | "team" | "level" | "alone";

const STEPS: Step[] = ["language", "team", "level", "alone"];
const STEP_OF: Record<string, Step> = { language: "language", team: "team", level: "level", hands_free: "alone" };
const SOON_S = 5;
const NO_ANSWER_MS = 12000; // counted from when the question has finished being asked
const DEFAULT: Profile = { team: null, language: "en", level: "beginner", hands_free: false };
const LANGUAGES: Language[] = ["en", "zh", "es"];

const spoken = (text: string, language?: Language, tone: Line["tone"] = "hopeful"): Line =>
  ({ text, tone, card_title: "", sources: ["host"], proposal_ids: [], concepts: [], headline: false, language });

function question(step: Step, lang: Language): string {
  return t(lang, ({ language: "askLanguage", team: "askTeam", level: "askLevel", alone: "askAlone" } as const)[step]);
}

export function Setup({ dir, match, demo, onStart }: {
  dir: Directory; match: MatchInfo; demo: boolean; onStart: (p: Profile) => void;
}) {
  const [stage, setStage] = useState<Stage>("splash");
  const [step, setStep] = useState<Step>("language");
  const [profile, setProfile] = useState<Profile>(DEFAULT);
  const [said, setSaid] = useState<string[]>([]);
  const [listen, setListen] = useState<{ state: ListenState; caption?: string }>({ state: "off" });
  const [kickoffAt, setKickoffAt] = useState<number | null>(null);
  const [askedAt, setAskedAt] = useState<number | null>(null); // when the current question finished
  const [now, setNow] = useState(() => performance.now());
  const stageRef = useRef(stage);
  const stepRef = useRef(step);
  const profileRef = useRef(profile);
  const answered = useRef(new Set<Step>());
  const prompted = useRef(0);
  stageRef.current = stage;
  profileRef.current = profile;
  const lang = profile.language;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;

  const go = (s: Stage) => { stageRef.current = s; setStage(s); };
  const say = async (lines: Line[], language: Language) => {
    setSaid(lines.map((l) => l.text));
    await voice.sayAndWait(lines, language); // queued together, so they play back to back
  };

  /** Turn what the viewer said into settings (the Host's tools do the understanding). */
  const understand = async (text: string, language: Language, asked: string) => {
    const res = await fetch("/api/setup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: `Achilles asked: "${asked}" The viewer answered: "${text}"`, spoken: language,
                             profile: profileRef.current }),
    });
    const body = await res.json() as { profile: Profile; changes: Partial<Profile> };
    profileRef.current = body.profile;
    setProfile(body.profile);
    return body;
  };

  /** A simple answer, understood in the browser: no round trip to the model. */
  const applyLocally = (changes: Partial<Profile>) => {
    const p = { ...profileRef.current, ...changes };
    profileRef.current = p;
    setProfile(p);
    return { profile: p, changes };
  };

  /** While a question is being asked, download the likely replies and the next question. */
  const preloadAfter = (step: Step, lang: Language) => {
    const langs = step === "language" ? LANGUAGES : [lang];
    const lines = langs.flatMap((l) => {
      switch (step) {
        case "language": return [spoken(t(l, "confirmLanguage"), l), spoken(question("team", l), l)];
        case "team": return [home, away].map((c) => spoken(t(l, "confirmTeam").replace("{team}", c.short_name), l))
          .concat([spoken(t(l, "confirmNeutral"), l), spoken(question("level", l), l)]);
        case "level": return [spoken(t(l, "confirmLevel"), l), spoken(question("alone", l), l)];
        case "alone": return [spoken(t(l, "tipAlone"), l), spoken(t(l, "tipOthers"), l), spoken(t(l, "kickoffSoon"), l)];
      }
    });
    void voice.preload(lines, lang);
  };

  const start = (p: Profile) => {
    if (stageRef.current === "started") return;
    go("started");
    listener.mode = "wake";
    void voice.sayAndWait([spoken(t(p.language, "letsGo"), p.language, "excited")], p.language);
    onStart(p);
  };

  const tip = async (p: Profile) => {
    go("tip");
    await say([spoken(t(p.language, p.hands_free ? "tipAlone" : "tipOthers"))], p.language);
    go("ready");
    setKickoffAt(performance.now() + SOON_S * 1000);
    await say([spoken(t(p.language, "kickoffSoon"))], p.language);
  };

  /** Ask the next question nobody has answered yet, or finish. */
  const askNext = async (p: Profile) => {
    const next = STEPS.find((s) => !answered.current.has(s));
    if (!next) return tip(p);
    stepRef.current = next;
    setStep(next);
    prompted.current = 0;
    go("asking");
    setAskedAt(null);
    preloadAfter(next, p.language);
    if (next === "language") {
      // The one question asked in all three languages, back to back.
      await say(LANGUAGES.map((l) => spoken(question("language", l), l)), "en");
    } else {
      await say([spoken(question(next, p.language))], p.language);
    }
    setAskedAt(performance.now());
  };

  const begin = async () => {
    if (stageRef.current !== "splash") return;
    go("asking"); // answers are welcome even while Achilles is still talking
    await sound.enable();
    voice.enable();
    listener.on({ state: (state, caption) => setListen({ state, caption }) });
    listener.mode = "open"; // in the lobby, everything said is an answer
    void listener.start("en");
    preloadAfter("language", "en");
    await say([spoken(t("en", "welcome"), "en"), ...LANGUAGES.map((l) => spoken(question("language", l), l))], "en");
    setAskedAt(performance.now());
  };

  // While waiting for the key press, download the welcome so it starts the instant the key is pressed.
  useEffect(() => {
    void voice.preload([spoken(t("en", "welcome"), "en"), ...LANGUAGES.map((l) => spoken(question("language", l), l))], "en");
  }, []);

  // Press any key (or tap) to start.
  useEffect(() => {
    if (stage !== "splash") return;
    const handler = (e: KeyboardEvent | PointerEvent) => {
      if ((e.target as HTMLElement).closest?.("button, select")) return;
      void begin();
    };
    window.addEventListener("keydown", handler);
    window.addEventListener("pointerdown", handler);
    return () => { window.removeEventListener("keydown", handler); window.removeEventListener("pointerdown", handler); };
  });

  // The viewer's answers, one step at a time.
  useEffect(() => {
    listener.on({
      command: async (text, language) => {
        const at = stageRef.current;
        if (at !== "asking" && at !== "ready") { listener.done(false); return; }
        voice.stop(); // the viewer answered: stop asking
        go("thinking");
        const current = stepRef.current;
        try {
          const quick = at === "asking" ? quickAnswer(current, text) : null;
          const { profile: p, changes } = quick
            ? applyLocally(quick)
            : await understand(text, language, question(current, profileRef.current.language));
          listener.done(false);
          if (at === "ready") { go("ready"); return; } // a change of mind just before kick-off
          answered.current.add(current);
          Object.keys(changes).forEach((k) => STEP_OF[k] && answered.current.add(STEP_OF[k]));
          const confirm = current === "language" ? t(p.language, "confirmLanguage")
            : current === "team" ? (p.team ? t(p.language, "confirmTeam").replace("{team}", dir.club(p.team)!.short_name)
                                            : t(p.language, "confirmNeutral"))
            : current === "level" ? t(p.language, "confirmLevel") : null;
          if (confirm) await say([spoken(confirm)], p.language);
          await askNext(p);
        } catch {
          listener.done(false);
          go(at);
        }
      },
    });
  });

  // Nobody answers (counted from when the question has been fully asked): ask once more, then use
  // the default for that question and move on.
  useEffect(() => {
    if (stage !== "asking" || askedAt == null) return;
    const id = setTimeout(async () => {
      const p = profileRef.current;
      if (prompted.current++ === 0) {
        const s = stepRef.current;
        setAskedAt(null);
        await say(s === "language" ? LANGUAGES.map((l) => spoken(question("language", l), l))
                                   : [spoken(question(s, p.language))], p.language);
        setAskedAt(performance.now());
      } else {
        answered.current.add(stepRef.current);
        await askNext(p);
      }
    }, NO_ANSWER_MS);
    return () => clearTimeout(id);
  }, [stage, step, askedAt, listen.caption]);

  // The countdown.
  useEffect(() => {
    if (kickoffAt == null || stage === "started") return;
    const id = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(id);
  }, [kickoffAt, stage]);
  const remaining = kickoffAt == null ? null : Math.max(0, Math.ceil((kickoffAt - now) / 1000));
  useEffect(() => { if (remaining === 0) start(profileRef.current); }, [remaining]);

  if (stage === "splash") {
    return (
      <main className="splash" onClick={() => void begin()}>
        <h1 className="wordmark">Kick-Off<span>.</span></h1>
        <p className="splash-fixture">
          <Crest club={home} className="crest-small" />{home.name}<span className="v">v</span>{away.name}
          <Crest club={away} className="crest-small" />
        </p>
        <p className="press">{t("en", "pressToStart")}</p>
        <p className="press-other">{t("zh", "pressToStart")} · {t("es", "pressToStart")}</p>
        <p className="why">{t("en", "whyPress")}</p>
      </main>
    );
  }

  const pick = (changes: Partial<Profile>) => {
    if (!demo) return;
    const p = { ...profile, ...changes };
    profileRef.current = p;
    setProfile(p);
  };

  const levels: HelpLevel[] = ["beginner", "getting_there", "big_picture"];

  return (
    <main className={`lobby ${demo ? "" : "voice-only"}`}>
      <Fixture dir={dir} match={match} lang={lang}>
        <div className="score kickoff-time">
          {remaining == null || remaining === 0 ? "20:00" : `19:59:${String(60 - remaining).padStart(2, "0")}`}
        </div>
        <p className="match-clock">{t(lang, "kickoffLabel")} · {match.venue}</p>
      </Fixture>

      <div className="lobby-stage">
        <div className="lobby-card">
          <section className="make-it-yours" aria-label={t(lang, "makeItYours")}>
            <h2>{t(lang, "makeItYours")}</h2>
            <Row label={t(lang, "language")} asking={stage !== "ready" && step === "language"}>
              {LANGUAGES.map((l) => (
                <Option key={l} on={profile.language === l} onPick={() => pick({ language: l })}>{LANGUAGE_NAMES[l]}</Option>
              ))}
            </Row>
            <Row label={t(lang, "team")} asking={stage !== "ready" && step === "team"}>
              {[home, away].map((c) => (
                <Option key={c.id} on={profile.team === c.id} onPick={() => pick({ team: c.id })}>
                  <Crest club={c} className="crest-small" />{c.short_name}
                </Option>
              ))}
              <Option on={profile.team === null} onPick={() => pick({ team: null })}>{t(lang, "neutral")}</Option>
            </Row>
            <Row label={t(lang, "guide")} asking={stage !== "ready" && step === "level"}>
              {levels.map((l) => (
                <Option key={l} on={profile.level === l || (l === "big_picture" && profile.level === "analyst")}
                        onPick={() => pick({ level: l })}>{LEVELS[l][lang].name}</Option>
              ))}
            </Row>
            <Row label={t(lang, "watching")} asking={stage !== "ready" && step === "alone"}>
              <Option on={profile.hands_free} onPick={() => pick({ hands_free: true })}>{t(lang, "onMyOwn")}</Option>
              <Option on={!profile.hands_free} onPick={() => pick({ hands_free: false })}>{t(lang, "withOthers")}</Option>
            </Row>
          </section>

          <section className="achilles-says">
            {said.map((line, i) => <p key={i} className="achilles">{line}</p>)}
            <VoiceStatus state={listen.state} caption={listen.caption} lang={lang} inline handsFree />
          </section>

          {demo && <button className="btn primary start" onClick={() => start(profile)}>{t(lang, "kickoffNow")} →</button>}
        </div>
      </div>
    </main>
  );
}

function Row({ label, asking, children }: { label: string; asking: boolean; children: ReactNode }) {
  return (
    <div className={`miy-row ${asking ? "asking" : ""}`}>
      <span className="miy-label">{label}</span>
      <div className="miy-options">{children}</div>
    </div>
  );
}

function Option({ on, onPick, children }: { on: boolean; onPick: () => void; children: ReactNode }) {
  return (
    <button className={`miy-option ${on ? "on" : ""}`} onClick={onPick} aria-pressed={on}>
      {children}
    </button>
  );
}
