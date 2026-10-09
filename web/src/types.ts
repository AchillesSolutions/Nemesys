// Shapes of the data the Python server sends. Kept in step with src/kickoff/*.py.

export type Language = "en" | "zh" | "es";
export type HelpLevel = "beginner" | "getting_there" | "big_picture" | "analyst";
export type Mode = "full" | "key_moments";
export type Tone = "neutral" | "calm" | "excited" | "hopeful" | "tense" | "disappointed";

export interface Profile {
  team: string | null;
  language: Language;
  level: HelpLevel;
  hands_free: boolean;
}

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

export interface Chapter {
  id: number;
  start: number;
  end: number;
  moment: number;
  event_id: number;
  minute: string;
  titles: Record<Language, string>;
}

export interface MatchInfo {
  id: string;
  home: string;
  away: string;
  venue: string;
  matchweek: number;
  duration: number;
  chapters: Chapter[];
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

export interface Line {
  text: string;
  tone: Tone;
  card_title: string;
  sources: string[];
  proposal_ids: number[];
  concepts: string[];
  headline: boolean;
  call?: boolean; // a quick play-by-play call
  language?: Language; // speak this line in this language, whatever the rest of the batch is in
}

export interface ProposalView {
  id: number;
  agent: string;
  priority: number;
  text: string;
  used: boolean;
}

export interface Commentary {
  /** Unique per feed entry (assigned in the browser): one event can have a call and an explanation. */
  seq?: number;
  event_id: number;
  minute: string;
  elapsed?: number; // match second of the moment it is about
  language?: Language; // what it was written in
  lines: Line[];
  reasoning: string;
  proposals: ProposalView[];
  learned: string[];
}

export type ServerMessage =
  | { type: "ready"; chapters: Chapter[]; profile: Profile; mode: Mode; speed: number }
  | { type: "chapter"; chapter: Chapter }
  | { type: "event"; event: MatchEvent; next_gap: number | null; pace: number; salience: number;
      signals: Record<string, unknown>[]; snapshot: Snapshot }
  | { type: "bridge"; from_minute: string; to_minute: string }
  | ({ type: "commentary" } & Commentary)
  | { type: "profile"; profile: Profile; learned: string[] }
  | { type: "host"; question: string; answer: string; changes: Partial<Profile>; tools: string[]; language: Language }
  | { type: "ignored"; question: string }
  | { type: "finished" }
  | { type: "error"; message: string };

export type Control =
  | { action: "pause" | "resume" | "next_chapter" }
  | { action: "speed"; value: number }
  | { action: "mode"; mode: Mode }
  | { action: "seek_chapter"; chapter: number }
  | { action: "seek"; minute: number }
  | { action: "ask"; text: string; language?: Language }
  | { action: "voice"; speaking: boolean }
  | ({ action: "profile" } & Partial<Profile>);
