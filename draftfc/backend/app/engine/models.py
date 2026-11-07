from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, model_validator
from backend.app.engine.positions import PitchSector, get_sector, normalize_position


class DraftFormat(str, Enum):
    SNAKE = "snake"
    THIRD_ROUND_REVERSAL = "third_round_reversal"
    LINEAR = "linear"


class DraftStatus(str, Enum):
    SETUP = "setup"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class DraftPlayer(BaseModel):
    player_id: int
    short_name: str
    full_name: str
    primary_position: str
    raw_primary_position: str
    positions: List[str]
    sector: PitchSector
    overall: int
    potential: int
    age: int
    nationality_id: Optional[int] = None
    nationality_name: str
    club_id: Optional[int] = None
    club_name: str = "Free Agents"
    league_id: Optional[int] = None
    league_name: str = "Free Agents"
    value_eur: int = 0
    wage_eur: int = 0
    face_url: Optional[str] = None

    @classmethod
    def from_raw(
        cls,
        player_id: int,
        short_name: str,
        primary_position: str,
        overall: int,
        nationality_name: str,
        club_name: str = "Free Agents",
        league_name: str = "Free Agents",
        full_name: Optional[str] = None,
        positions: Optional[List[str]] = None,
        potential: Optional[int] = None,
        age: int = 25,
        nationality_id: Optional[int] = None,
        club_id: Optional[int] = None,
        league_id: Optional[int] = None,
        value_eur: int = 0,
        wage_eur: int = 0,
        face_url: Optional[str] = None,
    ) -> "DraftPlayer":
        canon_primary = normalize_position(primary_position)
        raw_list = positions or [primary_position]
        canon_positions: List[str] = []
        for p in raw_list:
            cp = normalize_position(p)
            if cp not in canon_positions:
                canon_positions.append(cp)

        return cls(
            player_id=player_id,
            short_name=short_name,
            full_name=full_name or short_name,
            primary_position=canon_primary,
            raw_primary_position=primary_position,
            positions=canon_positions,
            sector=get_sector(canon_primary),
            overall=overall,
            potential=potential if potential is not None else overall,
            age=age,
            nationality_id=nationality_id,
            nationality_name=nationality_name,
            club_id=club_id,
            club_name=club_name,
            league_id=league_id,
            league_name=league_name,
            value_eur=value_eur,
            wage_eur=wage_eur,
            face_url=face_url,
        )

    @property
    def is_free_agent(self) -> bool:
        return self.club_id is None or self.club_name.strip().lower() == "free agents"


class OvrTierCap(BaseModel):
    min_ovr: int = Field(ge=1, le=99)
    max_ovr: int = Field(ge=1, le=99, default=99)
    max_players: int = Field(ge=0)


class DraftRulesConfig(BaseModel):
    roster_size: int = Field(default=18, ge=1, le=52)
    draft_format: DraftFormat = DraftFormat.SNAKE
    max_per_club: Optional[int] = Field(default=1, ge=1)
    max_per_league: Optional[int] = Field(default=None, ge=1)
    max_per_nationality: Optional[int] = Field(default=None, ge=1)
    ignore_free_agents_in_group_limits: bool = True

    min_overall: int = Field(default=1, ge=1, le=99)
    max_overall: int = Field(default=99, ge=1, le=99)
    max_budget_eur: Optional[int] = Field(default=None, ge=0)
    ovr_tier_caps: List[OvrTierCap] = Field(default_factory=list)

    min_by_sector: Dict[PitchSector, int] = Field(
        default_factory=lambda: {
            PitchSector.GK: 1,
            PitchSector.DEF: 4,
            PitchSector.MID: 3,
            PitchSector.FWD: 2,
        }
    )
    max_by_sector: Dict[PitchSector, int] = Field(default_factory=dict)
    max_by_position: Dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_rules_consistency(self) -> "DraftRulesConfig":
        if self.min_overall > self.max_overall:
            raise ValueError("min_overall não pode ser maior que max_overall.")
        total_min_sectors = sum(self.min_by_sector.values())
        if total_min_sectors > self.roster_size:
            raise ValueError(
                f"Soma dos mínimos por setor ({total_min_sectors}) excede roster_size ({self.roster_size})."
            )
        for sec, max_v in self.max_by_sector.items():
            min_v = self.min_by_sector.get(sec, 0)
            if max_v < min_v:
                raise ValueError(f"Setor {sec}: max_by_sector ({max_v}) menor que min_by_sector ({min_v}).")
        return self


class RuleViolationCode(str, Enum):
    ALREADY_PICKED = "ALREADY_PICKED"
    ROSTER_FULL = "ROSTER_FULL"
    CLUB_LIMIT_REACHED = "CLUB_LIMIT_REACHED"
    LEAGUE_LIMIT_REACHED = "LEAGUE_LIMIT_REACHED"
    NATIONALITY_LIMIT_REACHED = "NATIONALITY_LIMIT_REACHED"
    OVR_OUT_OF_RANGE = "OVR_OUT_OF_RANGE"
    OVR_TIER_LIMIT_REACHED = "OVR_TIER_LIMIT_REACHED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SECTOR_MAX_REACHED = "SECTOR_MAX_REACHED"
    POSITION_MAX_REACHED = "POSITION_MAX_REACHED"
    REQUIRED_SECTOR_RESERVATION = "REQUIRED_SECTOR_RESERVATION"


class RuleViolation(BaseModel):
    code: RuleViolationCode
    message: str


class ValidationResult(BaseModel):
    eligible: bool
    violations: List[RuleViolation] = Field(default_factory=list)