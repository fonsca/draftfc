from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from backend.exporter.models import (
    DraftedPlayerInput,
    DraftExportPayload,
    DraftTeamAssignment,
)
from backend.exporter.rdbm_patcher import patch_rdbm_directory, read_rdbm_table


def _build_demo_payload(
    db_path: Path, edition: str, host_teams: list[int], rdbm_dir: Path
) -> DraftExportPayload:
    tpl = read_rdbm_table(rdbm_dir / "teamplayerlinks.txt")
    available_pids = {
        int(r.get("playerid") or 0)
        for r in tpl.rows
        if int(r.get("playerid") or 0) > 0 and int(r.get("teamid") or 0) > 0
    }

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT player_id, short_name, primary_position, overall "
            "FROM players WHERE edition = ? ORDER BY overall DESC",
            (edition,),
        ).fetchall()

    pool = [
        DraftedPlayerInput(
            player_id=int(r[0]),
            short_name=str(r[1]),
            primary_position=str(r[2]),
            overall=int(r[3]),
        )
        for r in rows
        if int(r[0]) in available_pids
    ]
    gks = [p for p in pool if p.primary_position.upper() == "GK"]
    outfield = [p for p in pool if p.primary_position.upper() != "GK"]

    teams: list[DraftTeamAssignment] = []
    for idx, hid in enumerate(host_teams):
        team_players = [
            *gks[idx * 2 : (idx + 1) * 2],
            *outfield[idx * 16 : (idx + 1) * 16],
        ]
        teams.append(
            DraftTeamAssignment(
                participant_name=f"Draft Team {idx + 1}",
                host_team_id=hid,
                players=team_players,
            )
        )
    return DraftExportPayload(
        edition=edition,
        league_name="Smoke Test KickOff",
        teams=teams,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="DraftFC Phase 5 RDBM Patcher")
    parser.add_argument("--draft-json", type=Path, help="JSON exportado da sala")
    parser.add_argument("--demo-from-db", action="store_true", help="Gera draft de teste do SQLite")
    parser.add_argument("--db-path", type=Path, default=Path("data/draftfc_local.db"))
    parser.add_argument("--edition", default="fc27")
    parser.add_argument("--host-teams", default="305,306", help="IDs dos clubes hospedeiros separados por virgula")
    parser.add_argument("--rdbm-dir", type=Path, required=True, help="Pasta com .txt exportados pelo RDBM")
    parser.add_argument("--out-dir", type=Path, default=Path("data/exports/rdbm_patched"))
    args = parser.parse_args()

    if args.draft_json:
        payload = DraftExportPayload.model_validate_json(
            args.draft_json.read_text(encoding="utf-8")
        )
    elif args.demo_from_db:
        host_ids = [int(x.strip()) for x in args.host_teams.split(",") if x.strip()]
        payload = _build_demo_payload(args.db_path, args.edition, host_ids, args.rdbm_dir)
    else:
        raise SystemExit("Informe --draft-json ou --demo-from-db.")

    known_clubs: set[int] = set()
    if args.db_path.exists():
        with sqlite3.connect(args.db_path) as conn:
            known_clubs = {
                int(r[0])
                for r in conn.execute(
                    "SELECT club_id FROM clubs WHERE edition = ?", (payload.edition,)
                ).fetchall()
            }

    summary = patch_rdbm_directory(
        payload=payload,
        rdbm_input_dir=args.rdbm_dir,
        output_dir=args.out_dir,
        known_club_ids=known_clubs or None,
    )
    print(json.dumps(summary.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()