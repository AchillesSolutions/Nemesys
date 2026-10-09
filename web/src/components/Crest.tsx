import type { Club } from "../types";

/**
 * Club crests, drawn for the fictional clubs. Kilburn Athletic ("the Otters"): a teal shield with a
 * river and a K. Harbour City ("the Gulls"): a navy roundel with a gull over the water. Any other
 * club gets a plain roundel in its colours with its initial.
 */
export function Crest({ club, className = "crest" }: { club: Club; className?: string }) {
  if (club.id === "kil") {
    return (
      <svg className={className} viewBox="0 0 64 76" role="img" aria-label={club.name}>
        <path d="M32 2 L60 10 V38 C60 56 47 68 32 74 C17 68 4 56 4 38 V10 Z" fill="#0f8b8d" stroke="#fff" strokeWidth="2.5" />
        <path d="M4 44 C16 38 22 50 32 44 S48 38 60 44 V48 C48 42 42 54 32 48 S16 42 4 48 Z" fill="#fff" opacity=".9" />
        <path d="M24 16 V38 M24 28 L38 16 M28 25 L39 38" fill="none" stroke="#fff" strokeWidth="4.2"
              strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (club.id === "hbr") {
    return (
      <svg className={className} viewBox="0 0 64 76" role="img" aria-label={club.name}>
        <circle cx="32" cy="38" r="29" fill="#1d2b53" stroke="#ff8c1a" strokeWidth="2.5" />
        <path d="M14 34 C20 26 26 26 32 33 C38 26 44 26 50 34 C44 31 38 32 32 39 C26 32 20 31 14 34 Z" fill="#ff8c1a" />
        <path d="M14 48 Q23 43 32 48 T50 48 M18 54 Q25 50 32 54 T46 54" fill="none" stroke="#fff" strokeWidth="2.2"
              strokeLinecap="round" opacity=".85" />
      </svg>
    );
  }
  return (
    <svg className={className} viewBox="0 0 64 76" role="img" aria-label={club.name}>
      <circle cx="32" cy="38" r="29" fill={club.colours[0]} stroke={club.colours[1]} strokeWidth="2.5" />
      <text x="32" y="39" textAnchor="middle" dominantBaseline="central" fill={club.colours[1]} fontSize="26"
            fontWeight="800">{club.short_name[0]}</text>
    </svg>
  );
}
