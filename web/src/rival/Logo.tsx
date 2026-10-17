/** Nemesys: two footballs, each ringed in one side's colour, overlapping where two fans meet. */

const pentagon = (cx: number, cy: number, r: number, turn: number) =>
  Array.from({ length: 5 }, (_, k) => {
    const a = ((turn + k * 72) * Math.PI) / 180;
    return `${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`;
  }).join(" ");

/** A classic ball: a centre patch, five spokes, and the edge patches clipped by the ball's outline. */
function Ball({ id, cx, cy, r, ring }: { id: string; cx: number; cy: number; r: number; ring: string }) {
  const spokes = Array.from({ length: 5 }, (_, k) => {
    const a = ((-90 + k * 72) * Math.PI) / 180;
    return { x1: cx + 0.34 * r * Math.cos(a), y1: cy + 0.34 * r * Math.sin(a),
             x2: cx + 0.64 * r * Math.cos(a), y2: cy + 0.64 * r * Math.sin(a), a };
  });
  return (
    <g>
      <clipPath id={id}><circle cx={cx} cy={cy} r={r} /></clipPath>
      <circle cx={cx} cy={cy} r={r} className="ball" />
      <g clipPath={`url(#${id})`} className="patches">
        <polygon points={pentagon(cx, cy, 0.34 * r, -90)} />
        {spokes.map((s, k) => (
          <g key={k}>
            <line x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} />
            <polygon points={pentagon(cx + 0.96 * r * Math.cos(s.a), cy + 0.96 * r * Math.sin(s.a), 0.32 * r, -90 + k * 72 + 36)} />
          </g>
        ))}
      </g>
      <circle cx={cx} cy={cy} r={r + 1.9} className={`ring ${ring}`} />
    </g>
  );
}

export function Logo({ size = "large" }: { size?: "large" | "small" }) {
  const id = `logo-${size}`;
  return (
    <div className={`logo logo-${size}`} aria-label="Nemesys">
      <svg className="logo-mark" viewBox="0 0 42 26" aria-hidden>
        <Ball id={`${id}-a`} cx={13} cy={13} r={8.6} ring="ring-a" />
        <Ball id={`${id}-b`} cx={29} cy={13} r={8.6} ring="ring-b" />
      </svg>
      <span className="logo-word">Nemesys</span>
    </div>
  );
}
