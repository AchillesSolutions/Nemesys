import type { Club, MatchInfo, Player, TableRow, World } from "./types";

export async function loadStatic(): Promise<{ world: World; match: MatchInfo }> {
  const [world, match] = await Promise.all([
    fetch("/api/world").then((r) => r.json() as Promise<World>),
    fetch("/api/match").then((r) => r.json() as Promise<MatchInfo>),
  ]);
  return { world, match };
}

/** Lookups the components need, built once from the world. */
export class Directory {
  private players = new Map<string, Player>();
  private clubs = new Map<string, Club>();

  constructor(public world: World) {
    for (const club of world.clubs) {
      this.clubs.set(club.id, club);
      for (const p of club.squad) this.players.set(p.id, p);
    }
  }

  club(id: string | null | undefined): Club | undefined {
    return id ? this.clubs.get(id) : undefined;
  }

  player(id: string | null | undefined): Player | undefined {
    return id ? this.players.get(id) : undefined;
  }

  /** The club colour that stays readable on the dark pitch: the main colour unless it is too dark. */
  colour(id: string | null | undefined): string {
    const club = this.club(id);
    if (!club) return "#888";
    return luminance(club.colours[0]) > 0.12 ? club.colours[0] : club.colours[1];
  }
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The league table if the current score were the final score. */
export function projectTable(table: TableRow[], scores: { name: string; gf: number; ga: number }[]): TableRow[] {
  const rows = table.map((row) => {
    const s = scores.find((x) => x.name === row.club);
    if (!s) return row;
    const won = s.gf > s.ga ? 1 : 0, drawn = s.gf === s.ga ? 1 : 0, lost = s.gf < s.ga ? 1 : 0;
    const r = {
      ...row, played: row.played + 1, won: row.won + won, drawn: row.drawn + drawn, lost: row.lost + lost,
      goals_for: row.goals_for + s.gf, goals_against: row.goals_against + s.ga,
    };
    return { ...r, points: r.won * 3 + r.drawn, goal_difference: r.goals_for - r.goals_against };
  });
  return rows.sort((a, b) =>
    b.points - a.points || b.goal_difference - a.goal_difference || b.goals_for - a.goals_for ||
    a.club.localeCompare(b.club));
}
