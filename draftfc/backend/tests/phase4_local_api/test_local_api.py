import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.api.players import get_store
from backend.app.core.database import LocalDraftStore
from backend.app.main import app


@pytest.fixture
def client_with_db(tmp_path: Path) -> TestClient:
    db_file = tmp_path / "test_phase4.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE clubs (
            edition TEXT, club_id INT, roster_id TEXT, club_name TEXT,
            league_id INT, league_name TEXT, overall INT, logo_url TEXT,
            PRIMARY KEY (edition, club_id)
        );
        CREATE TABLE players (
            edition TEXT, player_id INT, roster_id TEXT, short_name TEXT, full_name TEXT,
            primary_position TEXT, positions_json TEXT, age INT, overall INT, potential INT,
            nationality_id INT, nationality_name TEXT, club_id INT, club_name TEXT,
            league_id INT, league_name TEXT, value_eur INT, wage_eur INT,
            preferred_foot TEXT, face_url TEXT, gender TEXT,
            PRIMARY KEY (edition, player_id)
        );
        """
    )
    players = [
        ("fc27", 231747, "270006", "K. Mbappé", "Kylian Mbappé", "ATA", '["ATA"]', 27, 91, 92, 18, "França", 243, "Real Madrid", 53, "LaLiga EA Sports", 142000000, 380000, "Direito", None, "male"),
        ("fc27", 252371, "270006", "J. Bellingham", "Jude Bellingham", "MEI", '["MEI"]', 23, 90, 94, 14, "Inglaterra", 243, "Real Madrid", 53, "LaLiga EA Sports", 137500000, 320000, "Direito", None, "male"),
        ("fc27", 239085, "270006", "E. Haaland", "Erling Haaland", "ATA", '["ATA"]', 26, 91, 93, 36, "Noruega", 10, "Manchester City", 13, "Premier League", 148000000, 390000, "Esquerdo", None, "male"),
        ("fc27", 212831, "270006", "Alisson", "Alisson Becker", "GOL", '["GOL"]', 33, 89, 89, 54, "Brasil", 9, "Liverpool", 13, "Premier League", 55000000, 220000, "Direito", None, "male"),
        ("fc27", 239818, "270006", "Rúben Dias", "Rúben Dias", "ZAG", '["ZAG"]', 29, 88, 89, 38, "Portugal", 10, "Manchester City", 13, "Premier League", 80000000, 250000, "Direito", None, "male"),
        ("fc27", 255253, "270006", "Vitinha", "Vítor Ferreira", "MC", '["MC"]', 26, 90, 92, 38, "Portugal", 73, "Paris Saint-Germain", 16, "Ligue 1", 129500000, 260000, "Direito", None, "male"),
    ]
    conn.executemany("INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", players)
    conn.execute("INSERT INTO clubs VALUES ('fc27', 243, '270006', 'Real Madrid', 53, 'LaLiga EA Sports', 86, NULL)")
    conn.commit()
    conn.close()

    test_store = LocalDraftStore(db_path=db_file)
    app.dependency_overrides[get_store] = lambda: test_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_full_local_commissioner_lifecycle(client_with_db: TestClient) -> None:
    ed_res = client_with_db.get("/api/editions")
    assert ed_res.status_code == 200
    assert ed_res.json()[0]["players_count"] == 6

    create_payload = {
        "room_name": "Copa de Sexta",
        "edition": "fc27",
        "gender": "male",
        "pick_clock_seconds": 60,
        "participants": ["Pedro", "Mateus"],
        "use_manual_order": True,
        "rules": {
            "roster_size": 2,
            "max_per_club": 1,
            "min_by_sector": {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0},
        },
    }
    room_res = client_with_db.post("/api/drafts", json=create_payload)
    assert room_res.status_code == 200
    room = room_res.json()
    rid = room["room_id"]
    assert room["current_slot"]["participant_id"] == "Pedro"

    # Busca sem acento ("mbappe" encontra "K. Mbappé")
    search_res = client_with_db.get(f"/api/drafts/{rid}/players", params={"q": "mbappe"})
    assert search_res.status_code == 200
    assert search_res.json()["total_matching"] == 1

    # Pick #1 por Pedro -> Mbappé (Real Madrid)
    p1 = client_with_db.post(
        f"/api/drafts/{rid}/pick",
        json={"player_id": 231747, "expected_overall_pick": 1, "as_commissioner": True},
    )
    assert p1.status_code == 200
    assert p1.json()["current_slot"]["overall_pick"] == 2

    # Duplo clique no Pick #1 retorna 409 Conflict
    dup = client_with_db.post(
        f"/api/drafts/{rid}/pick",
        json={"player_id": 239085, "expected_overall_pick": 1, "as_commissioner": True},
    )
    assert dup.status_code == 409

    # Pausar e retomar preserva o tempo restante congelado
    paused = client_with_db.post(f"/api/drafts/{rid}/pause").json()
    assert paused["status"] == "paused"
    assert paused["paused_remaining_seconds"] is not None
    resumed = client_with_db.post(f"/api/drafts/{rid}/resume").json()
    assert resumed["status"] == "active"

    # Undo restaura o turno #1 para Pedro
    undone = client_with_db.post(f"/api/drafts/{rid}/undo").json()
    assert undone["current_slot"]["overall_pick"] == 1