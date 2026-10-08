// The wake phrase: viewers address Kick-Off's guide by name, "Achilles, switch to Mandarin".
// Recognisers spell the name differently per language, so each spelling counts.

export const WAKE_NAME = "Achilles";

const ALIASES = ["achilles", "achillies", "akilles", "akilis", "aquiles", "achille", "阿喀琉斯", "阿基里斯", "阿奇里斯", "阿基利斯"];
const PUNCTUATION = /[.,!?;:¿¡'"“”‘’。，、！？；：]/g;

/** Does this utterance address Achilles, and what comes after the name? */
export function splitWake(text: string): { woke: boolean; command: string } {
  const lower = text.toLowerCase();
  for (const alias of ALIASES) {
    const at = lower.indexOf(alias);
    if (at !== -1 && at <= 12) { // the name comes first (allowing "hey", "ok", "嘿")
      const command = text.slice(at + alias.length).replace(PUNCTUATION, " ").trim();
      return { woke: true, command };
    }
  }
  return { woke: false, command: "" };
}

/** Rough overlap between two utterances, to recognise Kick-Off hearing its own voice. */
export function overlap(a: string, b: string): number {
  const words = (s: string) => new Set(s.toLowerCase().replace(PUNCTUATION, " ").split(/\s+/).filter(Boolean));
  const wa = words(a), wb = words(b);
  if (wa.size === 0 || wb.size === 0) return 0;
  let shared = 0;
  wa.forEach((w) => { if (wb.has(w)) shared++; });
  return shared / Math.min(wa.size, wb.size);
}
