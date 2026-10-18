import { describe, expect, it } from "vitest";
import { addPiece, type Line } from "./lines";

const say = (lines: Line[], who: "user" | "rival", text: string, start: number) =>
  addPiece(lines, who, text, start, start + 300);

describe("the conversation's order", () => {
  it("puts the viewer's line before the reply it prompted, even when its text arrives second", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "Ha! You", 5000); // the reply starts arriving first...
    lines = say(lines, "user", "We'll win this", 3000); // ...but the viewer started speaking before it
    expect(lines.map((l) => l.who)).toEqual(["user", "rival"]);
  });

  it("joins pieces that carry straight on into one line", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "Oh", 1000);
    lines = say(lines, "rival", " yes!", 1400);
    expect(lines).toHaveLength(1);
    expect(lines[0].text).toBe("Oh yes!");
  });

  it("keeps each speaker's line whole while they talk over each other", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "That's never", 1000);
    lines = say(lines, "user", "It was", 1200);
    lines = say(lines, "rival", " a penalty", 1500);
    expect(lines.map((l) => l.text)).toEqual(["That's never a penalty", "It was"]);
  });

  it("starts a new line after a pause", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "One nil.", 1000);
    lines = say(lines, "rival", "Two nil!", 9000);
    expect(lines).toHaveLength(2);
  });
});
