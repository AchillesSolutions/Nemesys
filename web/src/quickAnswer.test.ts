import { describe, expect, it } from "vitest";
import { quickAnswer } from "./quickAnswer";

describe("quickAnswer", () => {
  it("understands short language answers in any language", () => {
    expect(quickAnswer("language", "English please")).toEqual({ language: "en" });
    expect(quickAnswer("language", "中文")).toEqual({ language: "zh" });
    expect(quickAnswer("language", "Español")).toEqual({ language: "es" });
    expect(quickAnswer("language", "Mandarin.")).toEqual({ language: "zh" });
  });

  it("understands teams and neutral", () => {
    expect(quickAnswer("team", "Kilburn!")).toEqual({ team: "kil" });
    expect(quickAnswer("team", "Harbor City")).toEqual({ team: "hbr" });
    expect(quickAnswer("team", "I'm neutral")).toEqual({ team: null });
    expect(quickAnswer("team", "我支持Harbour City")).toEqual({ team: "hbr" });
  });

  it("understands how familiar the viewer is", () => {
    expect(quickAnswer("level", "It's my first match")).toEqual({ level: "beginner" });
    expect(quickAnswer("level", "I know a little")).toEqual({ level: "getting_there" });
    expect(quickAnswer("level", "a little familiar")).toEqual({ level: "getting_there" });
    expect(quickAnswer("level", "Quite familiar")).toEqual({ level: "big_picture" });
  });

  it("understands yes and no to watching alone", () => {
    expect(quickAnswer("alone", "Yes")).toEqual({ hands_free: true });
    expect(quickAnswer("alone", "just me")).toEqual({ hands_free: true });
    expect(quickAnswer("alone", "No, with friends")).toEqual({ hands_free: false });
    expect(quickAnswer("alone", "sí")).toEqual({ hands_free: true });
  });

  it("leaves long, mixed or unclear answers to the Host", () => {
    expect(quickAnswer("language", "English or maybe Spanish")).toBeNull();
    expect(quickAnswer("team", "Hmm, I'm not sure yet, who's playing well this season?")).toBeNull();
    expect(quickAnswer("team", "Spanish please")).toBeNull();
    expect(quickAnswer("language", "Spanish, and I support Kilburn")).toBeNull();
  });
});
