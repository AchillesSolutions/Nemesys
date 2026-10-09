// Instant understanding of the simple lobby answers ("English", "中文", "Kilburn", "a little", "yes"),
// so the reply comes straight away. Anything longer, mixed or unclear goes to the Host agent instead.

import type { HelpLevel, Language, Profile } from "./types";

export type LobbyStep = "language" | "team" | "level" | "alone";

const MAX_WORDS = 7; // longer answers may say several things at once: let the Host understand them

const LANGUAGE: [RegExp, Language][] = [
  [/mandarin|chinese|中文|普通话|汉语|华语|chino|mandarín/i, "zh"],
  [/spanish|español|espanol|西班牙/i, "es"],
  [/english|inglés|ingles|英语|英文/i, "en"],
];
const TEAM: [RegExp, string | null][] = [
  [/kilburn/i, "kil"],
  [/harbou?r/i, "hbr"],
  [/neutral|neither|nobody|no one|中立|都不|ninguno|neutro/i, null],
];
// Order matters: "a little familiar" means a little.
const LEVEL: [RegExp, HelpLevel][] = [
  [/little|bit|basics|some|略懂|一点|一些|un poco|algo/i, "getting_there"],
  [/first|new|never|beginner|第一次|新手|从来没|primer|nuevo|nunca/i, "beginner"],
  [/quite|familiar|know the rules|a lot|very|熟悉|很懂|bastante|bien|mucho/i, "big_picture"],
];
const ALONE: [RegExp, boolean][] = [
  [/\b(no|nope|not)\b|with (my )?(friends|family|others|partner)|不是|和朋友|和家人|acompañado|con amigos|con mi/i, false],
  [/\b(yes|yeah|yep|alone|just me|on my own|by myself)\b|是的|是啊|对|一个人|只有我|^sí|^si\b|solo|sola/i, true],
];

function words(text: string): number {
  return (text.match(/[㐀-鿿]/g)?.length ?? 0) / 2 + (text.match(/[A-Za-zÀ-ÿ]+/g)?.length ?? 0);
}

/** The only matching answer; two different answers are too ambiguous to guess. */
function only<T>(table: [RegExp, T][], text: string): { value: T } | null {
  const hits = table.filter(([re]) => re.test(text));
  return hits.length === 1 ? { value: hits[0][1] } : null;
}

/** The first matching answer, for tables ordered by priority. */
function first<T>(table: [RegExp, T][], text: string): { value: T } | null {
  const hit = table.find(([re]) => re.test(text));
  return hit ? { value: hit[1] } : null;
}

/** The settings a short, unambiguous answer to this question gives, or null to ask the Host. */
export function quickAnswer(step: LobbyStep, text: string): Partial<Profile> | null {
  const t = text.trim();
  if (!t || words(t) > MAX_WORDS) return null;
  // Mentions a language or a team while answering something else? It says more than one thing: Host.
  if ((step !== "language" && LANGUAGE.some(([re]) => re.test(t))) || (step !== "team" && TEAM.some(([re]) => re.test(t)))) {
    return null;
  }
  switch (step) {
    case "language": { const m = only(LANGUAGE, t); return m && { language: m.value }; }
    case "team": { const m = only(TEAM, t); return m && { team: m.value }; }
    case "level": { const m = first(LEVEL, t); return m && { level: m.value }; }
    case "alone": { const m = first(ALONE, t); return m && { hands_free: m.value }; }
  }
}
