import { describe, expect, it } from "vitest";
import { Directory } from "./api";
import { withTeams } from "./teams";
import type { World } from "./types";

const squad = (names: string[], club: string) => names.map((name, i) => ({
  id: `${club}-${i + 1}`, name, number: i + 1, position: "MF" as const, age: 25, traits: [],
}));
const dir = new Directory({
  clubs: [
    { id: "kil", name: "Kilburn Athletic", short_name: "Kilburn", nickname: "", stadium: "", colours: ["#000000", "#ffffff"],
      squad: squad(["Ollie Whitcombe", "Felix Andersen"], "kil") },
    { id: "hbr", name: "Harbour City", short_name: "Harbour City", nickname: "", stadium: "", colours: ["#000000", "#ffffff"],
      squad: squad(["Jordan Pike"], "hbr") },
  ],
  table: [],
} as unknown as World);

describe("withTeams", () => {
  it("puts each player's team beside their first mention", () => {
    expect(withTeams("Andersen finds Whitcombe, and Whitcombe scores!", dir, ["kil", "hbr"], "en"))
      .toBe("Andersen (Kilburn) finds Whitcombe (Kilburn), and Whitcombe scores!");
    expect(withTeams("Jordan Pike is sent off.", dir, ["kil", "hbr"], "en")).toBe("Jordan Pike (Harbour City) is sent off.");
  });
  it("handles possessives, Mandarin and lines that already say the team", () => {
    expect(withTeams("Pike's tackle", dir, ["hbr"], "en")).toBe("Pike's tackle");
    expect(withTeams("Pike (Harbour City) again", dir, ["hbr"], "en")).toBe("Pike (Harbour City) again");
    expect(withTeams("Pike被罚下。", dir, ["hbr"], "zh")).toBe("Pike（Harbour City）被罚下。");
  });
});
