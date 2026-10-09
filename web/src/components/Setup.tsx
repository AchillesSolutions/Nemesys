import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Directory } from "../api";
import { LANGUAGE_NAMES, LEVELS, t } from "../i18n";
import { listener, type ListenState } from "../listen";
import { sound } from "../sound";
import type { Language, Line, MatchInfo, Profile } from "../types";
import { voice } from "../voice";
import { VoiceStatus } from "./VoiceStatus";

/**
 * The match lobby. After one key press (browsers need it before sound), Achilles welcomes the viewer
 * and, in a short spoken back-and-forth, asks who they support, which language they want, and whether
 * they are watching alone. The "Make it yours" options light up as they answer. A countdown runs to
 * kick-off, shortening to ten seconds once everything is answered. Silence means a neutral beginner in
 * English. With ?demo=1 the options can also be clicked, for testing without a microphone.
 */

type Stage = "splash" | "ask-team" | "ask-alone" | "thinking" | "tip" | "ready" | "started";

const COUNTDOWN_S = 90;
const SOON_S = 10;
const NO_ANSWER_MS = 15000;
const DEFAULT: Profile = { team: null, language: "en", level: "beginner", hands_free: false };

const spoken = (text: string, tone: Line["tone"] = "hopeful"): Line =>
  ({ text, tone, card_title: "", sources: ["host"], proposal_ids: [], concepts: [], headline: false });

export function Setup({ dir, match, demo, onStart }: {
  dir: Directory; match: MatchInfo; demo: boolean; onStart: (p: Profile) => void;
}) {
  const [stage, setStage] = useState<Stage>("splash");
  const [profile, setProfile] = useState<Profile>(DEFAULT);
  const [said, setSaid] = useState("");
  const [listen, setListen] = useState<{ state: ListenState; caption?: string }>({ state: "off" });
  const [kickoffAt, setKickoffAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => performance.now());
  const stageRef = useRef(stage);
  const profileRef = useRef(profile);
  const prompted = useRef(0);
  stageRef.current = stage;
  profileRef.current = profile;
  const lang = profile.language;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;

  const go = (s: Stage) => { stageRef.current = s; setStage(s); };
  const stillAsking = () => stageRef.current === "ask-team"; // read fresh: it changes while Achilles talks
  const say = async (text: string, language: Language) => {
    setSaid(text);
    await voice.sayAndWait([spoken(text)], language);
  };

  /** Turn what the viewer said into settings (the Host's tools do the understanding). */
  const understand = async (text: string, language: Language, question?: string): Promise<Profile> => {
    const res = await fetch("/api/setup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: question ? `Achilles asked: "${question}" The viewer answered: "${text}"` : text,
        spoken: language, profile: profileRef.current,
      }),
    });
    const body = await res.json() as { profile: Profile };
    profileRef.current = body.profile;
    setProfile(body.profile);
    return body.profile;
  };

  const start = (p: Profile) => {
    if (stageRef.current === "started") return;
    go("started");
    listener.mode = "wake";
    void voice.sayAndWait([spoken(t(p.language, "letsGo"), "excited")], p.language);
    onStart(p);
  };

  const tip = async (p: Profile) => {
    go("tip");
    await say(t(p.language, p.hands_free ? "tipAlone" : "tipOthers"), p.language);
    go("ready");
    setKickoffAt((at) => Math.min(at ?? Infinity, performance.now() + SOON_S * 1000));
    await say(t(p.language, "kickoffSoon"), p.language);
  };

  const askAlone = async (p: Profile) => {
    go("ask-alone");
    await say(t(p.language, "askAlone"), p.language);
  };

  // Step one: the welcome, then the team and language question.
  const begin = async () => {
    if (stageRef.current !== "splash") return;
    go("ask-team"); // answers are welcome even while Achilles is still talking
    await sound.enable();
    voice.enable();
    listener.on({ state: (state, caption) => setListen({ state, caption }) });
    listener.mode = "open"; // in the lobby, everything said is an answer
    void listener.start("en");
    setKickoffAt(performance.now() + COUNTDOWN_S * 1000);
    await say(t("en", "welcome"), "en");
    if (stillAsking()) await voice.sayAndWait([spoken(t("zh", "greetingOther"))], "zh");
    if (stillAsking()) await voice.sayAndWait([spoken(t("es", "greetingOther"))], "es");
  };

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

  // The viewer's answers, step by step.
  useEffect(() => {
    listener.on({
      command: async (text, language) => {
        const step = stageRef.current;
        if (step !== "ask-team" && step !== "ask-alone" && step !== "ready") { listener.done(false); return; }
        voice.stop(); // the viewer answered: stop asking
        go("thinking");
        try {
          if (step === "ask-team") {
            const p = await understand(text, language);
            listener.done(false);
            await say(p.team ? t(p.language, "confirmTeam").replace("{team}", dir.club(p.team)!.short_name)
                             : t(p.language, "confirmNeutral"), p.language);
            if (p.hands_free) await tip(p); else await askAlone(p);
          } else if (step === "ask-alone") {
            const p = await understand(text, language, t(profileRef.current.language, "askAlone"));
            listener.done(false);
            await tip(p);
          } else {
            await understand(text, language); // a change of mind before kick-off
            listener.done(false);
            go("ready");
          }
        } catch {
          listener.done(false);
          go(step);
        }
      },
    });
  });

  // Nobody answers: ask once more, then carry on with the defaults.
  useEffect(() => {
    if (stage !== "ask-team" && stage !== "ask-alone") return;
    const id = setTimeout(async () => {
      const p = profileRef.current;
      if (prompted.current++ === 0) {
        await say(t(p.language, stage === "ask-team" ? "setupPrompt" : "askAlone"), p.language);
      } else if (stage === "ask-team") {
        await askAlone(p);
      } else {
        await tip(p);
      }
    }, NO_ANSWER_MS);
    return () => clearTimeout(id);
  }, [stage, listen.caption]);

  // The countdown.
  useEffect(() => {
    if (kickoffAt == null || stage === "started") return;
    const id = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(id);
  }, [kickoffAt, stage]);
  const remaining = kickoffAt == null ? COUNTDOWN_S : Math.max(0, Math.ceil((kickoffAt - now) / 1000));
  useEffect(() => { if (kickoffAt != null && remaining === 0) start(profileRef.current); }, [remaining, kickoffAt]);

  if (stage === "splash") {
    return (
      <main className="splash" onClick={() => void begin()}>
        <h1>Kick-Off</h1>
        <p className="fixture">{home.name} v {away.name}</p>
        <p className="venue">{match.venue}</p>
        <p className="press">{t("en", "pressToStart")}</p>
      </main>
    );
  }

  const pick = (changes: Partial<Profile>) => {
    if (!demo) return;
    const p = { ...profile, ...changes };
    profileRef.current = p;
    setProfile(p);
  };
  const mmss = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;

  return (
    <main className={`lobby ${demo ? "" : "voice-only"}`}>
      <header className="lobby-head">
        <h1>
          <span style={{ color: home.colours[0] }}>{home.name}</span>
          <span className="v">v</span>
          <span style={{ color: away.colours[1] }}>{away.name}</span>
        </h1>
        <p className="venue">{match.venue} · {dir.world.league}</p>
        <p className="countdown">{t(lang, "kickoffIn")} <strong>{mmss}</strong></p>
      </header>

      <section className="make-it-yours" aria-label={t(lang, "makeItYours")}>
        <h2>{t(lang, "makeItYours")}</h2>
        <Row label={t(lang, "team")}>
          {[home, away].map((c) => (
            <Option key={c.id} on={profile.team === c.id} onPick={() => pick({ team: c.id })}>
              <span className="crest" style={{ background: c.colours[0], borderColor: c.colours[1] }} />{c.short_name}
            </Option>
          ))}
          <Option on={profile.team === null} onPick={() => pick({ team: null })}>{t(lang, "neutral")}</Option>
        </Row>
        <Row label={t(lang, "language")}>
          {(Object.keys(LANGUAGE_NAMES) as Language[]).map((l) => (
            <Option key={l} on={profile.language === l} onPick={() => pick({ language: l })}>{LANGUAGE_NAMES[l]}</Option>
          ))}
        </Row>
        <Row label={t(lang, "watching")}>
          <Option on={profile.hands_free} onPick={() => pick({ hands_free: true })}>{t(lang, "onMyOwn")}</Option>
          <Option on={!profile.hands_free} onPick={() => pick({ hands_free: false })}>{t(lang, "withOthers")}</Option>
        </Row>
        <Row label={t(lang, "guide")}>
          <Option on={profile.level === "beginner"} onPick={() => pick({ level: "beginner" })}>
            {LEVELS.beginner[lang].name}
          </Option>
          <Option on={profile.level !== "beginner"} onPick={() => pick({ level: "big_picture" })}>
            {LEVELS.big_picture[lang].name}
          </Option>
        </Row>
      </section>

      <section className="achilles-says">
        <p className="achilles">{said}</p>
        <VoiceStatus state={listen.state} caption={listen.caption} lang={lang} inline handsFree />
      </section>

      {demo && <button className="btn primary start" onClick={() => start(profile)}>{t(lang, "kickoffNow")} →</button>}
    </main>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="miy-row">
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
