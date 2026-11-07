import json
import sqlite3
from pathlib import Path
from typing import Dict
from backend.app.engine.models import DraftPlayer


def load_player_pool_from_sqlite(
    db_path: Path,
    edition: str = "fc27",
    gender: str = "male",
) -> Dict[int, DraftPlayer]:
    if not db_path.exists():
        raise FileNotFoundError(f"Banco SQLite não encontrado em: {db_path}")

    pool: Dict[int, DraftPlayer] = {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM players
            WHERE edition = ? AND gender = ?
            ORDER BY overall DESC, potential DESC
            """,
            (edition, gender),
        ).fetchall()

    for row in rows:
        positions = json.loads(row["positions_json"]) if row["positions_json"] else [row["primary_position"]]
        player = DraftPlayer.from_raw(
            player_id=row["player_id"],
            short_name=row["short_name"],
            full_name=row["full_name"],
            primary_position=row["primary_position"],
            positions=positions,
            overall=row["overall"],
            potential=row["potential"],
            age=row["age"],
            nationality_id=row["nationality_id"],
            nationality_name=row["nationality_name"],
            club_id=row["club_id"],
            club_name=row["club_name"],
            league_id=row["league_id"],
            league_name=row["league_name"],
            value_eur=row["value_eur"],
            wage_eur=row["wage_eur"],
            face_url=row["face_url"],
        )
        pool[player.player_id] = player

    return pool