export type PitchSector = "GK" | "DEF" | "MID" | "FWD";
export type DraftFormat = "snake" | "third_round_reversal" | "linear";
export type DraftStatus = "setup" | "active" | "paused" | "completed";
export type FallbackStrategy = "smart_balanced" | "strict_overall";

export interface EditionSummary {
  edition: string;
  gender: string;
  players_count: number;
  clubs_count: number;
}

export interface OvrTierCap {
  min_ovr: number;
  max_ovr: number;
  max_players: number;
}

export interface DraftRulesConfig {
  roster_size: number;
  draft_format: DraftFormat;
  max_per_club: number | null;
  max_per_league: number | null;
  max_per_nationality: number | null;
  ignore_free_agents_in_group_limits: boolean;
  min_overall: number;
  max_overall: number;
  max_budget_eur: number | null;
  ovr_tier_caps: OvrTierCap[];
  min_by_sector: Record<PitchSector, number>;
  max_by_sector: Partial<Record<PitchSector, number>>;
  max_by_position: Record<string, number>;
}

export interface DraftPlayer {
  player_id: number;
  short_name: string;
  full_name: string;
  primary_position: string;
  raw_primary_position: string;
  positions: string[];
  sector: PitchSector;
  overall: number;
  potential: number;
  age: number;
  nationality_name: string;
  club_id: number | null;
  club_name: string;
  league_name: string;
  value_eur: number;
  face_url: string | null;
}

export interface RuleViolation {
  code: string;
  message: string;
}

export interface PlayerPoolItem {
  player: DraftPlayer;
  is_picked: boolean;
  eligible_for_active: boolean;
  violations: RuleViolation[];
}

export interface PlayerPoolPageResponse {
  total_matching: number;
  offset: number;
  limit: number;
  evaluated_for_participant: string | null;
  items: PlayerPoolItem[];
}

export interface FilterOptionsResponse {
  leagues: string[];
  clubs: string[];
  nationalities: string[];
  positions: string[];
}

export interface PickSlot {
  overall_pick: number;
  round_number: number;
  pick_in_round: number;
  participant_id: string;
}

export interface RecordedPick {
  overall_pick: number;
  round_number: number;
  pick_in_round: number;
  participant_id: string;
  player_id: number;
  player_short_name: string;
  club_name: string;
  league_name: string;
  overall: number;
  sector: PitchSector;
  made_by_commissioner: boolean;
  auto_drafted: boolean;
  fallback_reason: string | null;
}

export interface QueueEntryStatus {
  priority: number;
  player_id: number;
  short_name: string;
  overall: number;
  primary_position: string;
  club_name: string;
  eligible_now: boolean;
  violations: RuleViolation[];
}

export interface ParticipantRosterSummary {
  participant_id: string;
  auto_pick_enabled: boolean;
  players: DraftPlayer[];
  sector_counts: Record<PitchSector, number>;
  club_counts: Record<string, number>;
  league_counts: Record<string, number>;
  nationality_counts: Record<string, number>;
  spent_budget_eur: number;
  queue: QueueEntryStatus[];
}

export interface RoomSummaryItem {
  room_id: string;
  room_name: string;
  edition: string;
  gender: string;
  status: DraftStatus;
  picks_made: number;
  total_picks: number;
  updated_at: string;
}

export interface RoomStateSnapshot {
  room_id: string;
  room_name: string;
  edition: string;
  gender: string;
  pick_clock_seconds: number;
  turn_duration_seconds: number;
  paused_remaining_seconds: number | null;
  fallback_strategy: FallbackStrategy;
  status: DraftStatus;
  state_version: number;
  turn_started_at_iso: string;
  rules: DraftRulesConfig;
  lottery_receipt: {
    ordered_participant_ids: string[];
    seed_used: string;
    verification_hash: string;
  };
  ordered_participants: string[];
  current_slot: PickSlot | null;
  upcoming_slots: PickSlot[];
  total_picks: number;
  picks_history: RecordedPick[];
  rosters: Record<string, ParticipantRosterSummary>;
}

export interface ViabilityReport {
  viable: boolean;
  total_picks_simulated: number;
  elapsed_ms: number;
  error_message: string | null;
}