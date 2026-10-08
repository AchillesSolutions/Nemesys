import { t } from "../i18n";
import type { ListenState } from "../listen";
import type { Language } from "../types";
import { WAKE_NAME } from "../wake";

/**
 * The only on-screen sign of the voice interface: what Achilles is doing, and what it heard.
 * ``open`` means any words count (during setup), so the hint asks for an answer instead of the name.
 */
export function VoiceStatus({ state, caption, lang, open = false }: {
  state: ListenState; caption?: string; lang: Language; open?: boolean;
}) {
  let text: string;
  switch (state) {
    case "off": text = t(lang, "micOff"); break;
    case "awake": case "push": text = caption ? `“${caption}”` : t(lang, "listening"); break;
    case "thinking": text = caption ? `“${caption}”` : t(lang, "thinking"); break;
    default: text = open ? (caption ? `“${caption}”` : t(lang, "answerAloud")) : t(lang, "sayWake").replace("{name}", WAKE_NAME);
  }
  return (
    <div className={`voice-status vs-${state}`} role="status" aria-live="polite">
      <span className="vs-mic" aria-hidden>{state === "off" ? "🎙︎" : "🎙"}</span>
      <span className="vs-text">{text}</span>
      {state === "thinking" && <span className="vs-dots" aria-hidden>•••</span>}
    </div>
  );
}
