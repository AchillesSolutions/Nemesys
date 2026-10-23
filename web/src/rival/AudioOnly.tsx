/**
 * Nemesys is audio only: the listener hears the rival and sees nothing. Everything on this screen is a demo view
 * of what's happening behind the voice. The ribbon across the top says so all the time, with the rival's voice as
 * a live sound wave; the card says it once, large, as the curtain lifts.
 */

const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export function Headphones({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} {...stroke} aria-hidden>
      <path d="M4 15v-3a8 8 0 0 1 16 0v3" />
      <rect x="3" y="14" width="4.5" height="7" rx="1.6" />
      <rect x="16.5" y="14" width="4.5" height="7" rx="1.6" />
    </svg>
  );
}

const BARS = 18;

/** The rival's voice as a sound wave: alive while they talk, a flat line while they listen. */
export function VoiceWave({ speaking }: { speaking: boolean }) {
  return (
    <span className={`voice-wave ${speaking ? "is-speaking" : ""}`} aria-hidden>
      {Array.from({ length: BARS }, (_, i) => (
        <i key={i} style={{ animationDelay: `${-((i * 137) % 900) / 1000}s`, animationDuration: `${0.55 + ((i * 53) % 5) / 10}s` }} />
      ))}
    </span>
  );
}

export function AudioRibbon({ rival, speaking }: { rival: string; speaking: boolean }) {
  return (
    <div className="audio-ribbon" role="note">
      <span className="audio-pill"><Headphones size={15} /> Audio only</span>
      <span className="audio-hear">The listener hears just one thing: {rival}'s voice</span>
      <VoiceWave speaking={speaking} />
      <span className="audio-demo">Everything on this screen is a demo view of what's happening behind the voice</span>
    </div>
  );
}

export function AudioCard({ rival }: { rival: string }) {
  return (
    <div className="audio-card" aria-hidden>
      <div className="audio-card-inner">
        <Headphones size={64} />
        <p className="audio-card-title">Nemesys is audio only</p>
        <p className="audio-card-sub">In real life you'd just hear {rival} in your ear, at the match, on the sofa or on a train.</p>
        <p className="audio-card-sub">This screen shows what's happening behind the voice.</p>
      </div>
    </div>
  );
}
