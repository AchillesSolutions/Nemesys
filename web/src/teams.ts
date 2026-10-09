// On screen, a player named in a line gets their team beside them, "Pike (Harbour City)", so a newcomer
// can tell who is who. It is only written, never said: the voice reads the line as the agents wrote it.

import type { Directory } from "./api";
import type { Language } from "./types";

/** The line with each player's team in brackets after the first mention of them. */
export function withTeams(text: string, dir: Directory, clubIds: string[], lang: Language): string {
  let out = text;
  for (const id of clubIds) {
    const club = dir.club(id);
    if (!club) continue;
    const label = lang === "zh" ? `（${club.short_name}）` : ` (${club.short_name})`;
    for (const p of club.squad) {
      const surname = p.name.split(" ").slice(-1)[0];
      // The full name if it is used, else the surname, as a whole word (Latin letters: names sit inside
      // Mandarin text without spaces), not a possessive, and not already followed by a bracket.
      const re = new RegExp(`(?<![A-Za-zÀ-ž])(${escape(p.name)}|${escape(surname)})(?![A-Za-zÀ-ž'’]|\\s*[（(])`);
      const m = re.exec(out);
      if (!m) continue;
      const end = m.index + m[0].length;
      out = out.slice(0, end) + label + out.slice(end);
    }
  }
  return out;
}

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
