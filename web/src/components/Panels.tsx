import { useMemo, useState } from "react";
import { projectTable, type Directory } from "../api";
import { AGENT_NAMES, t } from "../i18n";
import type { Commentary, Language, MatchInfo, Snapshot, TeamSnapshot } from "../types";

export function Learned({ learned, lang }: { learned: string[]; lang: Language }) {
  return (
    <section className="panel learned" aria-label={t(lang, "learned")}>
      <h2>{t(lang, "learned")} <span className="count">{learned.length}</span></h2>
      {learned.length === 0 ? <p className="empty">{t(lang, "learnedEmpty")}</p> : (
        <ul>{learned.map((l) => <li key={l}><span className="tick" aria-hidden>✓</span>{l}</li>)}</ul>
      )}
    </section>
  );
}

export function Stats({ dir, snapshot, lang }: { dir: Directory; snapshot: Snapshot | null; lang: Language }) {
  if (!snapshot) return null;
  const { home, away } = snapshot;
  const row = (label: string, f: (s: TeamSnapshot) => string | number) => (
    <tr><td>{f(home)}</td><th scope="row">{label}</th><td>{f(away)}</td></tr>
  );
  return (
    <section className="panel stats" aria-label={t(lang, "stats")}>
      <h2>{t(lang, "stats")}</h2>
      <div className="possession" aria-label={`${t(lang, "possession")} ${home.possession_pct}% – ${away.possession_pct}%`}>
        <div style={{ width: `${home.possession_pct}%`, background: dir.colour(home.id) }}>{Math.round(home.possession_pct)}%</div>
        <div style={{ width: `${away.possession_pct}%`, background: dir.colour(away.id) }}>{Math.round(away.possession_pct)}%</div>
      </div>
      <p className="possession-label">{t(lang, "possession")}</p>
      <table>
        <tbody>
          {row(t(lang, "shots"), (s) => `${s.shots} (${s.shots_on_target})`)}
          {row(t(lang, "corners"), (s) => s.corners)}
          {row(t(lang, "fouls"), (s) => s.fouls)}
          {row(t(lang, "cards"), (s) => `${s.yellow_cards}🟨 ${s.red_cards}🟥`)}
          {row(t(lang, "players"), (s) => s.players_on_pitch.length)}
        </tbody>
      </table>
    </section>
  );
}

export function LeagueTable({ dir, match, snapshot, lang }: {
  dir: Directory; match: MatchInfo; snapshot: Snapshot | null; lang: Language;
}) {
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  const hs = snapshot?.home.score ?? 0, as = snapshot?.away.score ?? 0;
  const projected = useMemo(() => projectTable(dir.world.table, [
    { name: home.name, gf: hs, ga: as }, { name: away.name, gf: as, ga: hs },
  ]), [dir, home.name, away.name, hs, as]);
  const before = new Map(dir.world.table.map((r, i) => [r.club, i + 1]));
  const after = new Map(projected.map((r, i) => [r.club, i + 1]));
  const ours = new Set([home.name, away.name]);
  const final = snapshot?.status === "full_time";
  // During the match the table stays as it was before kick-off (a reshuffle on every goal is confusing);
  // one line says what the current score would mean. At full time it becomes the final standings.
  const shown = (final ? projected : dir.world.table).slice(0, 8);
  const suffix = (n: number) => (n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th");
  const ordinal = (n: number) => t(lang, "ordinal").replace("{n}", String(n)).replace("{suffix}", suffix(n));
  const stake = (club: { name: string; short_name: string }) => {
    const b = before.get(club.name)!, a = after.get(club.name)!;
    const key = a < b ? "stakeUp" : a > b ? "stakeDown" : "stakeSame";
    return t(lang, key).replace("{team}", club.short_name).replace("{pos}", ordinal(a));
  };

  return (
    <section className="panel table" aria-label={t(lang, "table")}>
      <h2>{t(lang, "table")} <span className="sub">{t(lang, final ? "finalStandings" : "beforeKickoff")}</span></h2>
      <table>
        <tbody>
          {shown.map((r, i) => {
            const move = final && ours.has(r.club) ? (before.get(r.club) ?? i + 1) - (i + 1) : 0;
            return (
              <tr key={r.club} className={`${ours.has(r.club) ? "ours" : ""} ${i === 3 ? "cutoff" : ""}`}>
                <td className="pos">{i + 1}</td>
                <td className="club">{r.club}</td>
                <td className="move">{move > 0 ? `▲${move}` : move < 0 ? `▼${-move}` : ""}</td>
                <td className="pts">{r.points}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!final && snapshot && (
        <p className="stakes">{t(lang, "ifItEnds").replace("{h}", String(hs)).replace("{a}", String(as))} {stake(home)}, {stake(away)}.</p>
      )}
    </section>
  );
}

/** What the agents proposed and why the Producer chose what it did. */
export function UnderTheHood({ feed, lang }: { feed: Commentary[]; lang: Language }) {
  const [open, setOpen] = useState(false);
  const latest = feed.find((c) => c.proposals.length > 0);
  return (
    <section className="panel hood">
      <h2>
        <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "▾" : "▸"} {t(lang, "howItWorks")}
        </button>
      </h2>
      {open && latest && (
        <>
          <p className="hood-minute">{latest.minute}' · {t(lang, "producerSaid")}</p>
          <p className="hood-reason">{latest.reasoning}</p>
          <p className="hood-sub">{t(lang, "proposals")}</p>
          <ul>
            {latest.proposals.map((p) => (
              <li key={p.id} className={p.used ? "used" : "held"}>
                <span className={`chip chip-${p.agent}`}>{AGENT_NAMES[p.agent]?.[lang] ?? p.agent}</span>
                <span className="prio">P{p.priority}</span>
                <span className="state">{p.used ? t(lang, "used") : t(lang, "held")}</span>
                <span className="text">{p.text}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
