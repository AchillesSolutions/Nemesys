import { useEffect, useState } from "react";
import { t } from "../i18n";
import type { ListenState } from "../listen";
import type { Language } from "../types";

const SUBTITLE_LINGER_MS = 2500;

/**
 * The voice interface on screen, like a voice-controlled TV: a small mic light that glows while
 * Kick-Off is listening to you, and a subtitle of what it heard that fades away. An optional hint
 * (shown briefly) tells new viewers how to talk to it.
 */
export function VoiceStatus({ state, caption, lang, hint, handsFree = false, inline = false }: {
  state: ListenState; caption?: string; lang: Language; hint?: string | null; handsFree?: boolean; inline?: boolean;
}) {
  const [subtitle, setSubtitle] = useState<string | null>(null);

  useEffect(() => { if (caption) setSubtitle(caption); }, [caption]);
  useEffect(() => {
    if (state !== "idle" && state !== "off") return;
    const id = setTimeout(() => setSubtitle(null), SUBTITLE_LINGER_MS);
    return () => clearTimeout(id);
  }, [state, subtitle]);

  const live = state === "awake" || state === "push" || state === "conversation" || (handsFree && state === "idle");
  const label = state === "off" ? t(lang, "micOff") : state === "thinking" ? t(lang, "thinking")
    : live ? t(lang, "listening") : "";
  return (
    <div className={`voice-ui ${inline ? "inline" : ""}`} role="status" aria-live="polite" aria-label={label}>
      {subtitle && <div className="subtitle">{subtitle}</div>}
      <div className="voice-row">
        {(hint || state === "off") && <span className="voice-hint">{state === "off" ? t(lang, "micOff") : hint}</span>}
        <span className={`mic-dot md-${state} ${live ? "live" : ""}`} title={label} />
      </div>
    </div>
  );
}
