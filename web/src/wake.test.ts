import { describe, expect, it } from "vitest";
import { overlap, splitWake, tokens } from "./wake";

describe("splitWake", () => {
  it("finds the name and the request in one breath, in each language", () => {
    expect(splitWake("Achilles, switch to Mandarin please.")).toEqual({ woke: true, command: "switch to Mandarin please" });
    expect(splitWake("阿喀琉斯为什么那个进球不算？")).toEqual({ woke: true, command: "为什么那个进球不算" });
    expect(splitWake("¿Aquiles, quién es el número 9?")).toEqual({ woke: true, command: "quién es el número 9" });
  });

  it("allows a greeting before the name", () => {
    expect(splitWake("Hey Achilles what's the score").woke).toBe(true);
  });

  it("treats the name alone as a wake with no request", () => {
    expect(splitWake("Achilles.")).toEqual({ woke: true, command: "" });
  });

  it("ignores room chatter and the name buried mid-sentence", () => {
    expect(splitWake("So who do you think will win this one?").woke).toBe(false);
    expect(splitWake("I was reading a story about the hero Achilles yesterday").woke).toBe(false);
  });
});

describe("echo detection", () => {
  it("counts each Chinese character as a token", () => {
    expect(tokens("Kilburn进球了")).toEqual(["kilburn", "进", "球", "了"]);
  });

  it("recognises Kick-Off hearing its own commentary, in English and Mandarin", () => {
    expect(overlap("Harbour City score on the counter", "Harbour City score on the counter. That one hurts.")).toBeGreaterThan(0.6);
    expect(overlap("进球了比分", "Kilburn进球了！比分一比一。")).toBeGreaterThan(0.6);
  });

  it("does not mistake the viewer's question for commentary", () => {
    expect(overlap("why was that offside", "A corner to Kilburn, taken by Andersen.")).toBeLessThan(0.3);
  });
});
