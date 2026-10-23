// Shapes of the data the Python server sends. Kept in step with src/kickoff/*.py.

export type Language = "en" | "zh" | "es";

export interface Player {
  id: string;
  name: string;
  number: number;
  position: "GK" | "DF" | "MF" | "FW";
  age: number;
  traits: string[];
}

export interface Club {
  id: string;
  name: string;
  short_name: string;
  nickname: string;
  stadium: string;
  colours: [string, string];
  style: { summary: string };
  squad: Player[];
}

export interface TableRow {
  club: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goals_for: number;
  goals_against: number;
  points: number;
  goal_difference: number;
  form: string[];
}

export interface World {
  league: string;
  season: string;
  matchweek: number;
  clubs: Club[];
  table: TableRow[];
}

export interface MatchInfo {
  id: string;
  home: string;
  away: string;
  venue: string;
  matchweek: number;
  duration: number;
}

export interface MatchEvent {
  id: number;
  elapsed: number;
  period: number;
  clock: number;
  minute: string;
  type: string;
  team?: string;
  player?: string;
  other_player?: string;
  x?: number;
  y?: number;
  end_x?: number;
  end_y?: number;
  outcome?: string;
  detail: Record<string, unknown>;
  frame?: Record<string, number[]>; // every player's [x, y] at this event
}

export interface TeamSnapshot {
  id: string;
  name: string;
  score: number;
  possession_pct: number;
  possession_last_10_pct: number;
  shots: number;
  shots_on_target: number;
  corners: number;
  fouls: number;
  offsides: number;
  yellow_cards: number;
  red_cards: number;
  passes: number;
  pass_accuracy_pct: number;
  players_on_pitch: string[];
  lineup: (string | null)[];
  booked: string[];
  sent_off: string[];
  goals: { player: string; minute: string; penalty: boolean; event_id: number }[];
}

export interface Snapshot {
  status: "pre_match" | "live" | "half_time" | "full_time";
  period: number;
  minute: string;
  elapsed: number;
  home: TeamSnapshot;
  away: TeamSnapshot;
  in_possession: string | null;
  ball: [number, number] | null;
  momentum: number;
  seconds_since_highlight: number;
}
