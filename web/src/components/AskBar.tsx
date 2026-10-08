import { useState } from "react";
import { t } from "../i18n";
import { canListen, listenOnce } from "../listen";
import type { Language } from "../types";

/**
 * Talk to Kick-Off: tap the mic and speak, or type. Used mid-match ("why was that offside?",
 * "switch to Mandarin") and on the setup screen ("I'm new and I support Kilburn").
 */
export function AskBar({ lang, onAsk, busy, placeholder }: {
  lang: Language; onAsk: (text: string) => void; busy?: boolean; placeholder?: string;
}) {
  const [listening, setListening] = useState(false);
  const [text, setText] = useState("");
  const [heard, setHeard] = useState<string | null>(null);

  const listen = async () => {
    if (listening) return;
    setListening(true);
    setHeard(null);
    try {
      const result = await listenOnce(lang);
      if (result?.text) {
        setHeard(result.text);
        onAsk(result.text);
      } else {
        setHeard(t(lang, "didntCatch"));
      }
    } catch {
      setHeard(t(lang, "micUnavailable"));
    } finally {
      setListening(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    onAsk(text.trim());
    setText("");
  };

  return (
    <form className={`ask-bar ${listening ? "listening" : ""}`} onSubmit={submit}>
      {canListen() && (
        <button type="button" className="mic" onClick={listen} aria-pressed={listening}
                aria-label={listening ? t(lang, "listening") : t(lang, "tapToTalk")}>
          <span aria-hidden>{listening ? "●" : "🎤"}</span>
          <span>{listening ? t(lang, "listening") : t(lang, "tapToTalk")}</span>
        </button>
      )}
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder ?? t(lang, "askPlaceholder")}
             aria-label={t(lang, "askPlaceholder")} disabled={busy} />
      <button type="submit" className="btn" disabled={busy || !text.trim()}>{t(lang, "ask")}</button>
      {heard && <span className="heard">“{heard}”</span>}
      {busy && <span className="thinking" aria-live="polite">{t(lang, "thinking")}</span>}
    </form>
  );
}
