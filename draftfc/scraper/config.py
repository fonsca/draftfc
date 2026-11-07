from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "draftfc_local.db"

SOFIFA_BASE_URL = "https://sofifa.com"
PAGE_SIZE = 75
INTERNATIONAL_LEAGUE_IDS = {78, 2136}  # Seleções masculinas (78) e femininas (2136)

EDITION_CONFIG = {
    "fc26": {
        "label": "EA Sports FC 26",
        "roster_prefix": "26",
        "default_roster": "260001",
    },
    "fc27": {
        "label": "EA Sports FC 27",
        "roster_prefix": "27",
        "default_roster": "270001",
    },
}

GENDER_PARAM = {
    "male": "0",
    "female": "1",
    "all": None,
}

PLAYER_SHOW_COLS = ["pi", "ae", "oa", "pt", "vl", "wg", "pf"]


def build_teams_url(roster_id: str, offset: int = 0, gender: str = "male") -> str:
    params = [f"r={roster_id}", "set=true", "col=oa", "sort=desc"]
    gender_code = GENDER_PARAM.get(gender)
    if gender_code is not None:
        params.append(f"gender={gender_code}")
    if offset > 0:
        params.append(f"offset={offset}")
    return f"{SOFIFA_BASE_URL}/teams?" + "&".join(params)


def build_players_url(roster_id: str, offset: int = 0, gender: str = "male") -> str:
    params = [f"r={roster_id}", "set=true", "col=oa", "sort=desc"]
    gender_code = GENDER_PARAM.get(gender)
    if gender_code is not None:
        params.append(f"gender={gender_code}")
    for col in PLAYER_SHOW_COLS:
        params.append(f"showCol[]={col}")
    if offset > 0:
        params.append(f"offset={offset}")
    return f"{SOFIFA_BASE_URL}/players?" + "&".join(params)