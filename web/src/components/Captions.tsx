import { useEffect, useState } from "react";
import type { Commentary } from "../types";
import { voice } from "../voice";

const LINGER_MS = 1500;

/**
 * Broadcast-style subtitles: the line Achilles is saying right now, on the lower part of the pitch,
 * fading shortly after it ends. They follow the voice exactly; with the voice switched off they show
 * each new line of commentary for a reading-length moment instead, so the words are never lost
 * (for viewers who can't hear, a noisy room, or a muted demo video).
 */
export function Captions({ voiceOn, feed }: { voiceOn: boolean; feed: Commentary[] }) {
  const [shown, setShown] = useState<string | null>(null);

  useEffect(() => {
    let clear: number | undefined;
    const off = voice.onLine((line) => {
      window.clearTimeout(clear);
      if (line) setShown(line.text);
      else clear = window.setTimeout(() => setShown(null), LINGER_MS);
    });
    return () => { off(); window.clearTimeout(clear); };
  }, []);

  const newest = feed.reduce<Commentary | null>((a, b) => (!a || (b.seq ?? 0) > (a.seq ?? 0) ? b : a), null);
  useEffect(() => {
    if (voiceOn || !newest) return;
    const text = newest.lines.map((l) => l.text).join(" ");
    setShown(text);
    const id = window.setTimeout(() => setShown(null), Math.min(10000, 2000 + text.length * 50));
    return () => window.clearTimeout(id);
  }, [newest?.seq, voiceOn]);

  if (!shown) return null;
  return (
    <div className="captions" aria-live="polite">
      <p>{shown}</p>
    </div>
  );
}
