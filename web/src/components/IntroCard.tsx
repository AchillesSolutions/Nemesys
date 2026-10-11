import { useEffect, useState } from "react";
import { t } from "../i18n";
import type { Commentary, Language } from "../types";
import { voice } from "../voice";

/**
 * Before kick-off: the starter points Achilles is running through, each appearing as it is said and
 * ticked once it has been, like a short lesson card over the pitch. With the voice off, all are shown.
 */
export function IntroCard({ feed, lang }: { feed: Commentary[]; lang: Language }) {
  const [said, setSaid] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState<string | null>(null);
  useEffect(() => voice.onLine((line) => {
    if (line) {
      setCurrent(line.text);
      setSaid((s) => new Set(s).add(line.text));
    } else setCurrent(null);
  }) as () => void, []);

  const points = feed.filter((c) => c.event_id === 0).sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))
    .flatMap((c) => c.lines).filter((l) => !l.headline && l.card_title && (said.has(l.text) || !voice.enabled));
  if (points.length === 0) return null;
  return (
    <section className="intro-card" aria-label={t(lang, "introTitle")}>
      <h2>{t(lang, "introTitle")}</h2>
      <ol>
        {points.map((l) => (
          <li key={l.text} className={l.text === current ? "now" : ""}>
            <span className="tick" aria-hidden>{l.text === current ? "•" : "✓"}</span>{l.card_title}
          </li>
        ))}
      </ol>
    </section>
  );
}
