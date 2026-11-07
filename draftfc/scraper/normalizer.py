import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from scraper.config import INTERNATIONAL_LEAGUE_IDS

CURRENCY_RE = re.compile(r"€\s*([\d.,]+)\s*([MK])?", re.IGNORECASE)


def parse_currency_eur(raw: Optional[str]) -> int:
    if not raw:
        return 0
    match = CURRENCY_RE.search(raw.strip())
    if not match:
        return 0
    amount = float(match.group(1).replace(",", "."))
    suffix = (match.group(2) or "").upper()
    if suffix == "M":
        return int(round(amount * 1_000_000))
    if suffix == "K":
        return int(round(amount * 1_000))
    return int(round(amount))


class ClubRecord(BaseModel):
    edition: str
    roster_id: str
    club_id: int = Field(gt=0)
    club_name: str
    league_id: int = Field(ge=0)
    league_name: str
    overall: Optional[int] = Field(default=None, ge=1, le=99)
    logo_url: Optional[str] = None


class PlayerRecord(BaseModel):
    edition: str
    roster_id: str
    player_id: int = Field(gt=0)
    short_name: str
    full_name: str
    primary_position: str
    positions: List[str]
    age: int = Field(ge=14, le=60)
    overall: int = Field(ge=1, le=99)
    potential: int = Field(ge=1, le=99)
    nationality_id: Optional[int] = None
    nationality_name: str
    club_id: Optional[int] = None
    club_name: str = "Free Agents"
    league_id: Optional[int] = None
    league_name: str = "Free Agents"
    value_eur: int = Field(ge=0, default=0)
    wage_eur: int = Field(ge=0, default=0)
    preferred_foot: Optional[str] = None
    face_url: Optional[str] = None
    gender: str = "male"

    @field_validator("positions")
    @classmethod
    def validate_positions(cls, values: List[str]) -> List[str]:
        cleaned: List[str] = []
        for pos in values:
            norm = pos.strip().upper()
            if norm and norm not in cleaned:
                cleaned.append(norm)
        if not cleaned:
            raise ValueError("Jogador precisa ter ao menos uma posição válida.")
        return cleaned


def normalize_player_row(
    raw: Dict[str, Any],
    edition: str,
    roster_id: str,
    gender: str,
    clubs_map: Dict[int, ClubRecord],
) -> PlayerRecord:
    candidate_ids: List[int] = raw.get("candidate_club_ids", [])
    matched_club: Optional[ClubRecord] = None

    for cid in candidate_ids:
        club = clubs_map.get(cid)
        if club and club.league_id not in INTERNATIONAL_LEAGUE_IDS:
            matched_club = club
            break

    club_id: Optional[int] = None
    club_name = "Free Agents"
    league_id: Optional[int] = None
    league_name = "Free Agents"

    if matched_club:
        club_id = matched_club.club_id
        club_name = matched_club.club_name
        league_id = matched_club.league_id
        league_name = matched_club.league_name
    elif raw.get("club_id") and not clubs_map:
        club_id = raw["club_id"]
        club_name = raw.get("club_name") or "Unknown Club"

    positions = raw.get("positions") or ["MC"]

    return PlayerRecord(
        edition=edition,
        roster_id=roster_id,
        player_id=raw["player_id"],
        short_name=raw["short_name"],
        full_name=raw["full_name"],
        primary_position=positions[0].strip().upper(),
        positions=positions,
        age=raw["age"],
        overall=raw["overall"],
        potential=max(raw["potential"], raw["overall"]),
        nationality_id=raw.get("nationality_id"),
        nationality_name=raw.get("nationality_name") or "Unknown",
        club_id=club_id,
        club_name=club_name,
        league_id=league_id,
        league_name=league_name,
        value_eur=parse_currency_eur(raw.get("value_raw")),
        wage_eur=parse_currency_eur(raw.get("wage_raw")),
        preferred_foot=raw.get("preferred_foot"),
        face_url=raw.get("face_url"),
        gender=gender,
    )