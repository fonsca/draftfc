import csv
import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from scraper.normalizer import ClubRecord, PlayerRecord

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clubs (
    edition TEXT NOT NULL,
    club_id INTEGER NOT NULL,
    roster_id TEXT NOT NULL,
    club_name TEXT NOT NULL,
    league_id INTEGER NOT NULL,
    league_name TEXT NOT NULL,
    overall INTEGER,
    logo_url TEXT,
    PRIMARY KEY (edition, club_id)
);

CREATE TABLE IF NOT EXISTS players (
    edition TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    roster_id TEXT NOT NULL,
    short_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    primary_position TEXT NOT NULL,
    positions_json TEXT NOT NULL,
    age INTEGER NOT NULL,
    overall INTEGER NOT NULL,
    potential INTEGER NOT NULL,
    nationality_id INTEGER,
    nationality_name TEXT NOT NULL,
    club_id INTEGER,
    club_name TEXT NOT NULL,
    league_id INTEGER,
    league_name TEXT NOT NULL,
    value_eur INTEGER NOT NULL DEFAULT 0,
    wage_eur INTEGER NOT NULL DEFAULT 0,
    preferred_foot TEXT,
    face_url TEXT,
    gender TEXT NOT NULL DEFAULT 'male',
    PRIMARY KEY (edition, player_id)
);

CREATE INDEX IF NOT EXISTS idx_players_draft_pool ON players (edition, gender, overall DESC);
CREATE INDEX IF NOT EXISTS idx_players_club ON players (edition, club_id);
CREATE INDEX IF NOT EXISTS idx_players_league ON players (edition, league_id);
CREATE INDEX IF NOT EXISTS idx_players_nation ON players (edition, nationality_name);
"""

UPSERT_CLUB_SQL = """
INSERT INTO clubs (
    edition, club_id, roster_id, club_name, league_id, league_name, overall, logo_url
) VALUES (
    :edition, :club_id, :roster_id, :club_name, :league_id, :league_name, :overall, :logo_url
)
ON CONFLICT(edition, club_id) DO UPDATE SET
    roster_id = excluded.roster_id,
    club_name = excluded.club_name,
    league_id = excluded.league_id,
    league_name = excluded.league_name,
    overall = excluded.overall,
    logo_url = excluded.logo_url;
"""

UPSERT_PLAYER_SQL = """
INSERT INTO players (
    edition, player_id, roster_id, short_name, full_name, primary_position, positions_json,
    age, overall, potential, nationality_id, nationality_name, club_id, club_name,
    league_id, league_name, value_eur, wage_eur, preferred_foot, face_url, gender
) VALUES (
    :edition, :player_id, :roster_id, :short_name, :full_name, :primary_position, :positions_json,
    :age, :overall, :potential, :nationality_id, :nationality_name, :club_id, :club_name,
    :league_id, :league_name, :value_eur, :wage_eur, :preferred_foot, :face_url, :gender
)
ON CONFLICT(edition, player_id) DO UPDATE SET
    roster_id = excluded.roster_id,
    short_name = excluded.short_name,
    full_name = excluded.full_name,
    primary_position = excluded.primary_position,
    positions_json = excluded.positions_json,
    age = excluded.age,
    overall = excluded.overall,
    potential = excluded.potential,
    nationality_id = excluded.nationality_id,
    nationality_name = excluded.nationality_name,
    club_id = excluded.club_id,
    club_name = excluded.club_name,
    league_id = excluded.league_id,
    league_name = excluded.league_name,
    value_eur = excluded.value_eur,
    wage_eur = excluded.wage_eur,
    preferred_foot = excluded.preferred_foot,
    face_url = excluded.face_url,
    gender = excluded.gender;
"""


class DraftStorage:
    def __init__(self, db_path: Path, raw_dir: Path, processed_dir: Path) -> None:
        self.db_path = db_path
        self.raw_dir = raw_dir
        self.processed_dir = processed_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA_SQL)

    def checkpoint_path(self, edition: str, gender: str) -> Path:
        return self.raw_dir / f"checkpoint_{edition}_{gender}.json"

    def load_checkpoint(self, edition: str, gender: str) -> Optional[Dict]:
        path = self.checkpoint_path(edition, gender)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def save_checkpoint(self, edition: str, gender: str, roster_id: str, next_offset: int) -> None:
        path = self.checkpoint_path(edition, gender)
        payload = {"edition": edition, "gender": gender, "roster_id": roster_id, "next_offset": next_offset}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def clear_checkpoint(self, edition: str, gender: str) -> None:
        path = self.checkpoint_path(edition, gender)
        if path.exists():
            path.unlink()

    def upsert_clubs(self, clubs: Iterable[ClubRecord]) -> int:
        rows = [c.model_dump() for c in clubs]
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(UPSERT_CLUB_SQL, rows)
        return len(rows)

    def load_clubs_map(self, edition: str) -> Dict[int, ClubRecord]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM clubs WHERE edition = ?", (edition,))
            return {row["club_id"]: ClubRecord(**dict(row)) for row in cur.fetchall()}

    def upsert_players(self, players: Iterable[PlayerRecord]) -> int:
        rows = []
        for p in players:
            d = p.model_dump()
            d["positions_json"] = json.dumps(d.pop("positions"))
            rows.append(d)
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(UPSERT_PLAYER_SQL, rows)
        return len(rows)

    def export_edition_csv(self, edition: str) -> Path:
        out_csv = self.processed_dir / f"players_{edition}.csv"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM players WHERE edition = ? ORDER BY overall DESC, potential DESC",
                (edition,),
            ).fetchall()
        if not rows:
            return out_csv

        fieldnames = list(rows[0].keys())
        try:
            with out_csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                for r in rows:
                    writer.writerow(dict(r))
            return out_csv
        except PermissionError:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fallback_csv = self.processed_dir / f"players_{edition}_{ts}.csv"
            with fallback_csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                for r in rows:
                    writer.writerow(dict(r))
            return fallback_csv