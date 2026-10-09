import { AGENT_NAMES, t } from "../i18n";
import type { Commentary, Language } from "../types";

/** The live feed of explanation cards, newest first. */
export function Feed({ feed, lang }: { feed: Commentary[]; lang: Language }) {
  const cards = feed.flatMap((c) => c.lines.map((line, i) => ({ key: `${c.event_id}-${i}-${line.headline}`, c, line })));
  return (
    <section className="panel feed" aria-label={t(lang, "commentary")}>
      <h2>{t(lang, "commentary")}</h2>
      {cards.length === 0 && <p className="empty">{t(lang, "commentaryEmpty")}</p>}
      <ol aria-live="polite">
        {cards.map(({ key, c, line }) => (
          <li key={key} className={`card tone-${line.tone} ${line.headline ? "card-headline" : ""} ${line.call ? "card-call" : ""}`}>
            <div className="card-top">
              <span className="card-minute">{c.minute}'</span>
              <span className="card-title">{line.card_title}</span>
            </div>
            <p className="card-text">{line.text}</p>
            {line.sources.length > 0 && (
              <div className="card-sources">
                {line.sources.map((s) => (
                  <span key={s} className={`chip chip-${s}`}>{AGENT_NAMES[s]?.[lang] ?? s}</span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
