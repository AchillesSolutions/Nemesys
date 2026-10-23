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

  it("shows a cut-in where it happened, in the middle of the other's line", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "That's never", 1000);
    lines = say(lines, "user", "It was", 1200);
    lines = say(lines, "rival", " a penalty", 1500);
    expect(lines.map((l) => l.text)).toEqual(["That's never", "It was", "a penalty"]);
  });

  it("splits the rival's line at the viewer's interjection, though the viewer's words arrive last", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "Your keeper", 1000);
    lines = say(lines, "rival", " couldn't catch", 1300);
    lines = say(lines, "rival", " a cold, and", 1700); // said after the viewer cut in...
    lines = say(lines, "rival", " he knows it.", 2000);
    lines = say(lines, "user", "Rubbish!", 1500); // ...but the viewer's words are transcribed afterwards
    expect(lines.map((l) => [l.who, l.text])).toEqual([
      ["rival", "Your keeper couldn't catch"], ["user", "Rubbish!"], ["rival", "a cold, and he knows it."],
    ]);
    lines = say(lines, "rival", " Two nil soon.", 2400);
    expect(lines.at(-1)!.text).toBe("a cold, and he knows it. Two nil soon.");
  });

  it("never breaks up the viewer's own sentence, even when the rival talks over its end", () => {
    let lines: Line[] = [];
    lines = say(lines, "user", "you're gonna be leaving this one in", 1000);
    lines = say(lines, "rival", "Oh, come off it", 2200); // the rival starts before the viewer's last word...
    lines = say(lines, "user", " tears", 2400); // ...which is transcribed a moment later
    expect(lines.filter((l) => l.who === "user").map((l) => l.text)).toEqual(["you're gonna be leaving this one in tears"]);
  });

  it("starts a new line after a pause", () => {
    let lines: Line[] = [];
    lines = say(lines, "rival", "One nil.", 1000);
    lines = say(lines, "rival", "Two nil!", 9000);
    expect(lines).toHaveLength(2);
  });
});
