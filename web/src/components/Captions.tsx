import { useEffect, useState } from "react";
import { t } from "../i18n";
import type { Commentary, Language, Line } from "../types";
import { voice } from "../voice";

type Kind = "live" | "learning" | "commentary" | "league" | "achilles";

/** What kind of line this is, shown as a small tag: which agent (or the live caller) is speaking. */
function kindOf(line: Line): Kind {
  if (line.sources.includes("host")) return "achilles";
  if (line.call || line.headline) return "live";
  if (line.sources.includes("teacher")) return "learning";
  if (line.sources.includes("league_guide") && !line.sources.includes("storyteller")) return "league";
  return "commentary";
}

/**
 * Broadcast-style subtitles in a bar under the pitch: the line Achilles is saying right now, tagged
 * with what kind of line it is. They follow the voice exactly; the last line stays (dimmed) until the
 * next one. With the voice off, each new line of commentary is shown instead, so the words are never
 * lost (for viewers who can't hear, a noisy room, or a muted demo video).
 */
export function Captions({ voiceOn, feed, lang }: { voiceOn: boolean; feed: Commentary[]; lang: Language }) {
  const [shown, setShown] = useState<{ line: Line; speaking: boolean } | null>(null);

  useEffect(() => voice.onLine((line) => {
    setShown((prev) => (line ? { line, speaking: true } : prev && { ...prev, speaking: false }));
  }) as () => void, []);

  const newest = feed.reduce<Commentary | null>((a, b) => (!a || (b.seq ?? 0) > (a.seq ?? 0) ? b : a), null);
  useEffect(() => {
    if (voiceOn || !newest?.lines[0]) return;
    setShown({ line: { ...newest.lines[0], text: newest.lines.map((l) => l.text).join(" ") }, speaking: true });
  }, [newest?.seq, voiceOn]);

  return (
    <section className={`caption-bar ${shown?.speaking ? "speaking" : ""}`} aria-live="polite">
      {shown ? (
        <>
          <span className={`caption-tag tag-${kindOf(shown.line)}`}>{t(lang, `tag_${kindOf(shown.line)}`)}</span>
          <p className="caption-text">{shown.line.text}</p>
        </>
      ) : (
        <p className="caption-text empty">{t(lang, "commentaryEmpty")}</p>
      )}
    </section>
  );
}

/** A standing reminder of what can be said to Achilles, in the viewer's language. */
export function TalkCard({ lang, handsFree }: { lang: Language; handsFree: boolean }) {
  const prefix = handsFree ? "" : t(lang, "talkPrefix");
  return (
    <section className="panel talk-card">
      <h2>🎙 {t(lang, handsFree ? "talkTitleAlone" : "talkTitle")}</h2>
      <ul>
        {(["talkEx1", "talkEx2", "talkEx3"] as const).map((k) => <li key={k}>“{prefix}{t(lang, k)}”</li>)}
      </ul>
    </section>
  );
}
