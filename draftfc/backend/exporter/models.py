from __future__ import annotations

from pydantic import BaseModel, Field


class DraftedPlayerInput(BaseModel):
    player_id: int
    short_name: str = ""
    primary_position: str = "CM"
    overall: int = 75


class DraftTeamAssignment(BaseModel):
    participant_name: str
    host_team_id: int
    players: list[DraftedPlayerInput] = Field(min_length=11)


class DraftExportPayload(BaseModel):
    edition: str = "fc26"
    league_name: str = "DraftFC Local"
    teams: list[DraftTeamAssignment] = Field(min_length=1)


class PatchRequest(BaseModel):
    payload: DraftExportPayload
    rdbm_input_dir: str = "data/rdbm_clean"
    output_dir: str = "data/exports/latest_rdbm"


class PatchSummary(BaseModel):
    edition: str
    teams_patched: int
    players_transferred: int
    backfill_swaps: int
    loans_removed: int
    missing_player_ids: list[int]
    output_dir: str