import {
  DraftRulesConfig,
  EditionSummary,
  FallbackStrategy,
  FilterOptionsResponse,
  PitchSector,
  PlayerPoolPageResponse,
  RoomStateSnapshot,
  RoomSummaryItem,
  ViabilityReport,
} from "../types/draft";

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `Erro HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export interface PlayerQueryFilters {
  q?: string;
  sector?: PitchSector | "";
  league_name?: string;
  club_name?: string;
  only_eligible?: boolean;
  hide_picked?: boolean;
  for_participant?: string;
  offset?: number;
  limit?: number;
}

export const localAdapter = {
  listEditions: () => apiFetch<EditionSummary[]>("/api/editions"),
  listRooms: () => apiFetch<RoomSummaryItem[]>("/api/drafts"),

  simulateViability: (payload: {
    edition: string;
    gender: string;
    participants: string[];
    rules: DraftRulesConfig;
    fallback_strategy: FallbackStrategy;
  }) =>
    apiFetch<ViabilityReport>("/api/drafts/simulate-viability", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  createRoom: (payload: {
    room_name: string;
    edition: string;
    gender: string;
    pick_clock_seconds: number;
    participants: string[];
    use_manual_order: boolean;
    lottery_seed?: string;
    rules: DraftRulesConfig;
    fallback_strategy: FallbackStrategy;
  }) =>
    apiFetch<RoomStateSnapshot>("/api/drafts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getRoom: (roomId: string) => apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}`),

  getFilterOptions: (roomId: string) =>
    apiFetch<FilterOptionsResponse>(`/api/drafts/${roomId}/filter-options`),

  queryPlayers: (roomId: string, filters: PlayerQueryFilters) => {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.sector) params.set("sector", filters.sector);
    if (filters.league_name) params.set("league_name", filters.league_name);
    if (filters.club_name) params.set("club_name", filters.club_name);
    if (filters.only_eligible) params.set("only_eligible", "true");
    if (filters.hide_picked !== undefined) params.set("hide_picked", String(filters.hide_picked));
    if (filters.for_participant) params.set("for_participant", filters.for_participant);
    params.set("offset", String(filters.offset ?? 0));
    params.set("limit", String(filters.limit ?? 50));
    return apiFetch<PlayerPoolPageResponse>(`/api/drafts/${roomId}/players?${params.toString()}`);
  },

  makePick: (roomId: string, playerId: number, expectedOverallPick: number) =>
    apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}/pick`, {
      method: "POST",
      body: JSON.stringify({
        player_id: playerId,
        as_commissioner: true,
        expected_overall_pick: expectedOverallPick,
      }),
    }),

  autoPick: (roomId: string, expectedOverallPick: number) =>
    apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}/auto-pick`, {
      method: "POST",
      body: JSON.stringify({ expected_overall_pick: expectedOverallPick }),
    }),

  undoLastPick: (roomId: string) =>
    apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}/undo`, { method: "POST" }),

  pauseRoom: (roomId: string) =>
    apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}/pause`, { method: "POST" }),

  resumeRoom: (roomId: string) =>
    apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}/resume`, { method: "POST" }),

  resetClock: (roomId: string) =>
    apiFetch<RoomStateSnapshot>(`/api/drafts/${roomId}/reset-clock`, { method: "POST" }),

  setAutoPickMode: (roomId: string, participantId: string, enabled: boolean) =>
    apiFetch<RoomStateSnapshot>(
      `/api/drafts/${roomId}/participants/${encodeURIComponent(participantId)}/auto-pick-mode`,
      { method: "POST", body: JSON.stringify({ enabled }) }
    ),

  enqueuePlayer: (roomId: string, participantId: string, playerId: number) =>
    apiFetch<RoomStateSnapshot>(
      `/api/drafts/${roomId}/participants/${encodeURIComponent(participantId)}/queue`,
      { method: "POST", body: JSON.stringify({ player_id: playerId }) }
    ),

  removeFromQueue: (roomId: string, participantId: string, playerId: number) =>
    apiFetch<RoomStateSnapshot>(
      `/api/drafts/${roomId}/participants/${encodeURIComponent(participantId)}/queue/${playerId}`,
      { method: "DELETE" }
    ),

  reorderQueue: (roomId: string, participantId: string, orderedIds: number[]) =>
    apiFetch<RoomStateSnapshot>(
      `/api/drafts/${roomId}/participants/${encodeURIComponent(participantId)}/queue/reorder`,
      { method: "PUT", body: JSON.stringify({ ordered_player_ids: orderedIds }) }
    ),
};