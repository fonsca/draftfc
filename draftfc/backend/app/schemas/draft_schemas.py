from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.engine.draft_lottery import LotteryReceipt, PickSlot
from backend.app.engine.draft_state import QueueEntryStatus, RecordedPick
from backend.app.engine.fallback_autodraft import FallbackStrategy
from backend.app.engine.models import DraftPlayer, DraftRulesConfig, DraftStatus, RuleViolation
from backend.app.engine.positions import PitchSector


class EditionSummary(BaseModel):
    edition: str
    gender: str
    players_count: int
    clubs_count: int


class ViabilityCheckRequest(BaseModel):
    edition: str = "fc27"
    gender: str = "male"
    participants: List[str] = Field(min_length=2)
    rules: DraftRulesConfig = Field(default_factory=DraftRulesConfig)
    fallback_strategy: FallbackStrategy = FallbackStrategy.SMART_BALANCED


class CreateRoomRequest(BaseModel):
    room_name: str = Field(default="Campeonato entre Amigos", min_length=2, max_length=80)
    edition: str = "fc27"
    gender: str = "male"
    pick_clock_seconds: int = Field(default=60, ge=0, le=600)
    participants: List[str] = Field(min_length=2, max_length=32)
    use_manual_order: bool = False
    lottery_seed: Optional[str] = None
    rules: DraftRulesConfig = Field(default_factory=DraftRulesConfig)
    fallback_strategy: FallbackStrategy = FallbackStrategy.SMART_BALANCED


class MakePickRequest(BaseModel):
    player_id: int
    actor_participant_id: Optional[str] = None
    as_commissioner: bool = True
    expected_overall_pick: Optional[int] = None
    expected_state_version: Optional[int] = None


class AutoPickRequest(BaseModel):
    expected_overall_pick: Optional[int] = None
    strategy_override: Optional[FallbackStrategy] = None


class AutoPickModeRequest(BaseModel):
    enabled: bool


class QueueMutateRequest(BaseModel):
    player_id: int


class QueueReorderRequest(BaseModel):
    ordered_player_ids: List[int]


class PlayerPoolItem(BaseModel):
    player: DraftPlayer
    is_picked: bool
    eligible_for_active: bool
    violations: List[RuleViolation] = Field(default_factory=list)


class PlayerPoolPageResponse(BaseModel):
    total_matching: int
    offset: int
    limit: int
    evaluated_for_participant: Optional[str] = None
    items: List[PlayerPoolItem]


class FilterOptionsResponse(BaseModel):
    leagues: List[str]
    clubs: List[str]
    nationalities: List[str]
    positions: List[str]


class ParticipantRosterSummary(BaseModel):
    participant_id: str
    auto_pick_enabled: bool
    players: List[DraftPlayer]
    sector_counts: Dict[PitchSector, int]
    club_counts: Dict[str, int]
    league_counts: Dict[str, int]
    nationality_counts: Dict[str, int]
    spent_budget_eur: int
    queue: List[QueueEntryStatus]


class RoomSummaryItem(BaseModel):
    room_id: str
    room_name: str
    edition: str
    gender: str
    status: DraftStatus
    picks_made: int
    total_picks: int
    updated_at: str


class RoomStateSnapshot(BaseModel):
    room_id: str
    room_name: str
    edition: str
    gender: str
    pick_clock_seconds: int
    turn_duration_seconds: float
    paused_remaining_seconds: Optional[float] = None
    fallback_strategy: FallbackStrategy
    status: DraftStatus
    state_version: int
    turn_started_at_iso: str
    rules: DraftRulesConfig
    lottery_receipt: LotteryReceipt
    ordered_participants: List[str]
    current_slot: Optional[PickSlot] = None
    upcoming_slots: List[PickSlot] = Field(default_factory=list)
    total_picks: int
    picks_history: List[RecordedPick] = Field(default_factory=list)
    rosters: Dict[str, ParticipantRosterSummary]