from enum import Enum
from typing import Dict, Tuple


class PitchSector(str, Enum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


POSITION_SYNONYMS: Dict[str, Tuple[str, PitchSector]] = {
    # Goleiros
    "GK": ("GK", PitchSector.GK),
    "GOL": ("GK", PitchSector.GK),
    "GL": ("GK", PitchSector.GK),
    # Defensores
    "CB": ("CB", PitchSector.DEF),
    "ZAG": ("CB", PitchSector.DEF),
    "LB": ("LB", PitchSector.DEF),
    "LE": ("LB", PitchSector.DEF),
    "RB": ("RB", PitchSector.DEF),
    "LD": ("RB", PitchSector.DEF),
    "LWB": ("LWB", PitchSector.DEF),
    "ADE": ("LWB", PitchSector.DEF),
    "RWB": ("RWB", PitchSector.DEF),
    "ADD": ("RWB", PitchSector.DEF),
    # Meio-campistas
    "CDM": ("CDM", PitchSector.MID),
    "VOL": ("CDM", PitchSector.MID),
    "CM": ("CM", PitchSector.MID),
    "MC": ("CM", PitchSector.MID),
    "CAM": ("CAM", PitchSector.MID),
    "MEI": ("CAM", PitchSector.MID),
    "LM": ("LM", PitchSector.MID),
    "ME": ("LM", PitchSector.MID),
    "RM": ("RM", PitchSector.MID),
    "MD": ("RM", PitchSector.MID),
    # Atacantes
    "LW": ("LW", PitchSector.FWD),
    "PE": ("LW", PitchSector.FWD),
    "RW": ("RW", PitchSector.FWD),
    "PD": ("RW", PitchSector.FWD),
    "CF": ("CF", PitchSector.FWD),
    "SA": ("CF", PitchSector.FWD),
    "ST": ("ST", PitchSector.FWD),
    "ATA": ("ST", PitchSector.FWD),
}


def normalize_position(raw_pos: str) -> str:
    key = (raw_pos or "").strip().upper()
    if key in POSITION_SYNONYMS:
        return POSITION_SYNONYMS[key][0]
    raise ValueError(f"Posição desconhecida no EA FC: '{raw_pos}'")


def get_sector(raw_or_canonical_pos: str) -> PitchSector:
    key = (raw_or_canonical_pos or "").strip().upper()
    if key in POSITION_SYNONYMS:
        return POSITION_SYNONYMS[key][1]
    raise ValueError(f"Não foi possível mapear setor para posição: '{raw_or_canonical_pos}'")