import type { Language } from "./types";

// The few fixed labels on the fixture graphic, in each language the lobby speaks.
const STRINGS = {
  en: { firstHalf: "First half", secondHalf: "Second half", halfTime: "Half-time", fullTime: "Full-time", pen: "pen" },
  zh: { firstHalf: "上半场", secondHalf: "下半场", halfTime: "中场", fullTime: "全场结束", pen: "点球" },
  es: { firstHalf: "Primer tiempo", secondHalf: "Segundo tiempo", halfTime: "Medio tiempo", fullTime: "Final", pen: "pen." },
} as const;

export type StringKey = keyof (typeof STRINGS)["en"];

export function t(lang: Language, key: StringKey): string {
  return STRINGS[lang][key];
}
