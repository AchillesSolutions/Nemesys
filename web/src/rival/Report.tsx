/**
 * The viewer's match report at full time, written by the Match agent: the three moments that decided it and why
 * they mattered, a verdict, and the bets settled. In the viewer's language, with English beneath when it differs.
 */

export interface MatchReport {
  headline: string; headline_en: string;
  moments: { minute: string; what: string; why: string; what_en: string; why_en: string }[];
  verdict: string; verdict_en: string;
  bets: string[]; bets_en: string[];
}

function Both({ text, en, className }: { text: string; en: string; className: string }) {
  return (
    <p className={className}>
      {text}
      {en && en !== text && <span className="report-en">{en}</span>}
    </p>
  );
}

export function ReportCard({ report }: { report: MatchReport }) {
  return (
    <section className="report" aria-label="Your match report">
      <p className="report-kicker">Your match report · by the Match Agent</p>
      <Both className="report-headline" text={report.headline} en={report.headline_en} />
      <ol className="report-moments">
        {report.moments.slice(0, 3).map((m, i) => (
          <li key={i} className="report-moment" style={{ animationDelay: `${0.15 * i}s` }}>
            <span className="report-minute">{m.minute.replace(/'?$/, "'")}</span>
            <Both className="report-what" text={m.what} en={m.what_en} />
            <Both className="report-why" text={m.why} en={m.why_en} />
          </li>
        ))}
      </ol>
      <Both className="report-verdict" text={report.verdict} en={report.verdict_en} />
      {report.bets.length > 0 && (
        <ul className="report-bets">
          {report.bets.map((b, i) => (
            <li key={i}><span className="report-bet-tag">Bet</span><Both className="report-bet" text={b} en={report.bets_en[i] ?? ""} /></li>
          ))}
        </ul>
      )}
    </section>
  );
}
