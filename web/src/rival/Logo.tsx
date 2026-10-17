/** Nemesys: two rings, one in each side's colour, overlapping where two fans meet. */
export function Logo({ size = "large" }: { size?: "large" | "small" }) {
  return (
    <div className={`logo logo-${size}`} aria-label="Nemesys">
      <svg className="logo-mark" viewBox="0 0 40 26" aria-hidden>
        <circle cx="13" cy="13" r="10" className="ring-a" />
        <circle cx="27" cy="13" r="10" className="ring-b" />
        <path d="M20 5.86a10 10 0 0 1 0 14.28a10 10 0 0 1 0-14.28z" className="lens" />
      </svg>
      <span className="logo-word">Nemesys</span>
    </div>
  );
}
