from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

SectorType = Literal["GK", "DEF", "MID", "ATT"]


class RoomPlayer(BaseModel):
    player_id: int
    short_name: str
    overall: int
    primary_position: str
    sector: SectorType = "MID"
    club_id: int = 0
    club_name: str = "Free Agent"
    league_name: str = "Unknown League"
    nationality: str = "Unknown"
    value_eur: int = 0


class RoomConfig(BaseModel):
    room_name: str = Field(default="Liga DraftFC", min_length=2, max_length=60)
    password: str = Field(default="", max_length=64)
    edition: str = Field(default="fc26")
    num_teams: int = Field(default=16, ge=2, le=32)
    rounds: int = Field(default=16, ge=1, le=30)
    pick_clock_seconds: int = Field(default=60, ge=5, le=600)
    max_per_club: int | None = Field(default=None, ge=1)
    max_per_league: int | None = Field(default=None, ge=1)
    max_per_nationality: int | None = Field(default=None, ge=1)


class CreateRoomRequest(BaseModel):
    config: RoomConfig = Field(default_factory=RoomConfig)
    commissioner_name: str = Field(default="Comissário", min_length=1, max_length=40)
    custom_players: list[RoomPlayer] | None = None


class JoinRoomRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=40)
    password: str = Field(default="")
    seat_number: int | None = Field(default=None, ge=1)
    seat_token: str | None = None


class PickActionRequest(BaseModel):
    seat_token: str
    player_id: int
    request_id: str | None = None


class CommissionerActionRequest(BaseModel):
    commissioner_token: str


class DraftPickRecord(BaseModel):
    overall_pick: int
    round_number: int
    pick_in_round: int
    seat_number: int
    participant_name: str
    player: RoomPlayer
    source: Literal["manual", "commissioner", "autodraft_queue", "autodraft_best"] = "manual"
    timestamp_ms: int


class ParticipantState(BaseModel):
    seat_number: int
    display_name: str
    claimed: bool = False
    is_online: bool = False
    roster: list[RoomPlayer] = Field(default_factory=list)
    queue: list[int] = Field(default_factory=list)


class RoomSummary(BaseModel):
    room_code: str
    room_name: str
    edition: str
    status: Literal["lobby", "active", "paused", "completed"]
    num_teams: int
    claimed_seats: int
    online_seats: int
    rounds: int
    current_round: int
    current_pick_index: int
    total_picks: int
    has_password: bool


class PublicRoomState(BaseModel):
    room_code: str
    room_name: str
    edition: str
    status: Literal["lobby", "active", "paused", "completed"]
    num_teams: int
    rounds: int
    pick_clock_seconds: int
    remaining_seconds: int
    current_pick_index: int
    total_picks: int
    current_round: int
    current_seat: int | None
    snake_order_preview: list[int]
    participants: list[ParticipantState]
    picks_history: list[DraftPickRecord]
    drafted_player_ids: list[int]
    available_players_count: int
    meta: dict[str, Any] = Field(default_factory=dict)