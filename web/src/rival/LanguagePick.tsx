/**
 * The first thing the lobby asks: which language. English, Mandarin and Spanish are the ones on offer, large and
 * lit; behind them, faintly, the languages of football fans everywhere, so it reads as "your language", with
 * these three picked out. Choosing is by voice; the tiles are for testing.
 */

export const PICKS = [
  { id: "English", label: "English", hello: "Welcome" },
  { id: "Mandarin", label: "中文", hello: "欢迎" },
  { id: "Spanish", label: "Español", hello: "Bienvenido" },
];

const EVERYWHERE = [
  "Français", "Deutsch", "日本語", "한국어", "العربية", "Português", "हिन्दी", "Italiano", "Русский", "Türkçe",
  "Polski", "Nederlands", "Svenska", "Tiếng Việt", "ไทย", "Bahasa Indonesia", "Ελληνικά", "עברית", "Kiswahili",
  "Українська", "Čeština", "Magyar", "Română", "Dansk", "Suomi", "Norsk", "فارسی", "বাংলা", "Filipino", "Hausa",
  "Yorùbá", "isiZulu", "Català", "Hrvatski", "Srpski", "Gaeilge", "Cymraeg", "Íslenska", "اردو", "தமிழ்",
];

// Fixed places around the edge of an ellipse, leaving the middle clear for the three picked out.
const PLACES = EVERYWHERE.map((_, i) => {
  const angle = (i / EVERYWHERE.length) * Math.PI * 2 + (i % 3) * 0.21;
  const reach = 0.74 + ((i * 37) % 9) / 32; // 0.74 .. 0.99 of the way out
  return {
    left: 50 + Math.cos(angle) * reach * 50,
    top: 50 + Math.sin(angle) * reach * 50,
    size: 0.78 + ((i * 53) % 7) / 14,
    fade: 0.16 + ((i * 29) % 5) / 30,
  };
});

export function LanguagePick({ heard, onPick }: { heard: string; onPick: (id: string, label: string) => void }) {
  return (
    <div className="language-pick">
      <div className="language-cloud" aria-hidden>
        {EVERYWHERE.map((name, i) => (
          <span key={name} style={{ left: `${PLACES[i].left}%`, top: `${PLACES[i].top}%`,
                                    fontSize: `${PLACES[i].size}rem`, opacity: PLACES[i].fade }}>{name}</span>
        ))}
      </div>
      <div className="language-picks">
        {PICKS.map((p) => (
          <button key={p.id} className="language-tile" onClick={() => onPick(p.id, p.label)}>
            <b>{p.label}</b>
            <span>{p.hello}</span>
          </button>
        ))}
      </div>
      <p className="lobby-heard language-heard">{heard ? `“${heard}”` : "Say your language · 说出你的语言 · Di tu idioma"}</p>
    </div>
  );
}
