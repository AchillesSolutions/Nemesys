import type { Club, MatchInfo, Player, World } from "./types";

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
