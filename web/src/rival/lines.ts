/**
 * The conversation as lines, in the order things were said. Pieces of transcript arrive out of order: the
 * viewer's words are transcribed after they finish speaking, so they arrive after the rival has started its
 * reply. Each piece carries when it was said (start_ms, on the watch's timeline), and lines are ordered by
 * when each one began.
 */

export type Speaker = "user" | "rival";

export interface Line {
  id: number;
  who: Speaker;
  text: string;
  start: number; // ms on the watch's timeline, when this line began
  end: number; // ...and when its latest piece ended
  at: number; // Date.now() when it last grew, for settling (translation)
}

export const PAUSE_MS = 1800; // a piece this long after the speaker's last one starts a new line

let nextId = 1;

/** Add one piece of transcript; returns the lines, ordered by when each began. */
export function addPiece(lines: Line[], who: Speaker, delta: string, start: number, end: number, now = Date.now()): Line[] {
  // The speaker's most recent line, if this piece carries straight on from it.
  let open = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].who === who) {
      open = start - lines[i].end < PAUSE_MS ? i : -1;
      break;
    }
  }
  let next: Line[];
  if (open >= 0) {
    next = lines.slice();
    next[open] = { ...lines[open], text: lines[open].text + delta, end: Math.max(lines[open].end, end), at: now };
  } else {
    next = [...lines, { id: nextId++, who, text: delta.trimStart(), start, end, at: now }];
  }
  return next.sort((a, b) => a.start - b.start || a.id - b.id);
}
