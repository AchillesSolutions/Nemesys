import type { ReactNode } from "react";
import type { Directory } from "../api";

/**
 * Player and club names in the conversation, coloured so it's clear who's being talked about:
 * players in one colour, clubs (and their nicknames) in another.
 */
export interface NameMatcher { pattern: RegExp; kind: Map<string, "player" | "club"> }

export function nameMatcher(dir: Directory, clubIds: string[]): NameMatcher {
  const kind = new Map<string, "player" | "club">();
  for (const id of clubIds) {
    const club = dir.club(id)!;
    for (const n of [club.name, club.short_name, club.nickname, club.nickname.replace(/^The /, "")]) kind.set(n, "club");
    for (const p of club.squad) {
      kind.set(p.name, "player");
      const surname = p.name.split(" ").slice(-1)[0];
      if (!kind.has(surname)) kind.set(surname, "player");
    }
  }
  const words = [...kind.keys()].sort((a, b) => b.length - a.length).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return { pattern: new RegExp(`\\b(${words.join("|")})\\b`, "g"), kind };
}

export function highlight(text: string, m: NameMatcher | null, keyBase: number): ReactNode[] {
  if (!m) return [text];
  const out: ReactNode[] = [];
  let last = 0;
  for (const hit of text.matchAll(m.pattern)) {
    out.push(text.slice(last, hit.index));
    out.push(<span key={`${keyBase}-${hit.index}`} className={`name-${m.kind.get(hit[0])}`}>{hit[0]}</span>);
    last = hit.index! + hit[0].length;
  }
  out.push(text.slice(last));
  return out;
}
