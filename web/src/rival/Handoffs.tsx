import { type CSSProperties, useLayoutEffect, useRef, useState } from "react";

/**
 * Hand-offs on the demo view: a glowing dot travelling across the page from one part of the system to the next
 * (an agent's card, the rival's memory), found by their data-node. A hand-off "via" a third part goes there
 * first. The part receiving it pulses as the dot lands.
 */

export interface Handoff { from: string; to: string; via?: string; key: number }

export const HANDOFF_MS = 900; // one leg of the journey

interface Dot { key: string; x0: number; y0: number; x1: number; y1: number }

function centre(node: string) {
  const r = document.querySelector(`[data-node="${node}"]`)?.getBoundingClientRect();
  return r && r.width ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
}

function receive(node: string) {
  const el = document.querySelector(`[data-node="${node}"]`);
  if (!el) return;
  el.classList.remove("is-receiving");
  void (el as HTMLElement).offsetWidth; // restart the pulse if it's already running
  el.classList.add("is-receiving");
  setTimeout(() => el.classList.remove("is-receiving"), 900);
}

export function HandoffLayer({ handoffs }: { handoffs: Handoff[] }) {
  const seen = useRef(new Set<number>());
  const [dots, setDots] = useState<Dot[]>([]);

  useLayoutEffect(() => {
    for (const h of handoffs) {
      if (seen.current.has(h.key)) continue;
      seen.current.add(h.key);
      const legs = h.via ? [[h.from, h.via], [h.via, h.to]] : [[h.from, h.to]];
      legs.forEach(([from, to], i) => setTimeout(() => {
        const a = centre(from), b = centre(to);
        if (!a || !b) return;
        const key = `${h.key}-${i}`;
        setDots((d) => [...d, { key, x0: a.x, y0: a.y, x1: b.x, y1: b.y }]);
        setTimeout(() => {
          setDots((d) => d.filter((x) => x.key !== key));
          receive(to);
        }, HANDOFF_MS);
      }, i * HANDOFF_MS));
    }
  }, [handoffs]);

  return (
    <div className="handoff-layer" aria-hidden>
      {dots.map((d) => (
        <span key={d.key} className="handoff-dot" style={{
          "--x0": `${d.x0}px`, "--y0": `${d.y0}px`, "--x1": `${d.x1}px`, "--y1": `${d.y1}px`, "--ms": `${HANDOFF_MS}ms`,
        } as CSSProperties} />
      ))}
    </div>
  );
}
