/**
 * The conversation as lines, in the order things were said. Pieces of transcript arrive out of order: the
 * viewer's words are transcribed after they finish speaking, so they arrive after the rival has started its
 * reply. Each piece carries when it was said (start_ms, on the watch's timeline), and lines are ordered by
 * when each one began. When one of them cuts in while the other is talking, the other's line is split where
 * the interruption came, so the interruption shows in the middle of it, where it happened.
 */

export type Speaker = "user" | "rival";

export interface Line {
  id: number;
  who: Speaker;
  text: string;
  start: number; // ms on the watch's timeline, when this line began
  end: number; // ...and when its latest piece ended
  at: number; // Date.now() when it last grew, for settling (translation)
  marks?: { start: number; offset: number }[]; // where each piece began, in time and in the text: for splitting
}

export const PAUSE_MS = 1800; // a piece this long after the speaker's last one starts a new line
const USER_PAUSE_MS = 3000; // the viewer's speech arrives in bursts as it's transcribed: a longer pause for theirs

let nextId = 1;

/** Add one piece of transcript; returns the lines, ordered by when each began. */
export function addPiece(lines: Line[], who: Speaker, delta: string, start: number, end: number, now = Date.now()): Line[] {
  // The speaker's most recent line, if this piece carries straight on from it (and nobody cut in since).
  let open = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].who === who) {
      const line = lines[i];
      // The rival's line breaks where the viewer cut in; the viewer's own sentence is never broken up.
      const cutIn = who === "rival" && lines.some((l) => l.who === "user" && l.start > line.start && l.start < start);
      open = start - line.end < (who === "user" ? USER_PAUSE_MS : PAUSE_MS) && !cutIn ? i : -1;
      break;
    }
  }
  let next: Line[];
  if (open >= 0) {
    const line = lines[open];
    next = lines.slice();
    next[open] = { ...line, text: line.text + delta, end: Math.max(line.end, end), at: now,
                   marks: [...(line.marks ?? []), { start, offset: line.text.length }] };
  } else {
    // A new line: if the other one was mid-line when this began, split theirs where it came in.
    next = who === "user"
      ? lines.flatMap((l) => (l.who === "rival" && l.start < start && l.end > start ? split(l, start, now) : [l]))
      : lines.slice();
    next.push({ id: nextId++, who, text: delta.trimStart(), start, end, at: now, marks: [{ start, offset: 0 }] });
  }
  return next.sort((a, b) => a.start - b.start || a.id - b.id);
}

/** A line in two at the first piece said at or after ``when``; whole if every piece came before it. */
function split(line: Line, when: number, now: number): Line[] {
  const marks = line.marks ?? [];
  const cut = marks.findIndex((m) => m.start >= when && m.offset > 0);
  if (cut < 0) return [line];
  const at = marks[cut].offset;
  const head = line.text.slice(0, at).trimEnd();
  const tail = line.text.slice(at).trimStart();
  if (!head || !tail) return [line];
  const shift = line.text.length - line.text.slice(at).trimStart().length;
  return [
    { ...line, text: head, end: marks[cut].start, marks: marks.slice(0, cut) },
    { ...line, id: nextId++, text: tail, start: marks[cut].start, at: now,
      marks: marks.slice(cut).map((m) => ({ start: m.start, offset: m.offset - shift })) },
  ];
}
