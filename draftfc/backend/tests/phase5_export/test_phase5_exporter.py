from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.exporter.models import (
    DraftedPlayerInput,
    DraftExportPayload,
    DraftTeamAssignment,
)
from backend.exporter.rdbm_patcher import (
    FREE_AGENTS_TEAM_ID,
    patch_rdbm_directory,
    read_rdbm_table,
    write_rdbm_table,
)
from backend.exporter.router import router as export_router


def _write_utf16_tsv(path: Path, headers: list[str], rows: list[list[str]]) -> bytes:
    lines = ["\t".join(headers)] + ["\t".join(r) for r in rows]
    raw = b"\xff\xfe" + ("\r\n".join(lines) + "\r\n").encode("utf-16-le")
    path.write_bytes(raw)
    return raw


def _build_mock_rdbm_env(base_dir: Path) -> None:
    tpl_headers = ["artificialkey", "teamid", "playerid", "jerseynumber", "position"]
    dts_headers = ["teamid", "captainid", "penaltiesid"] + [f"playerid{i}" for i in range(18)]

    tpl_rows: list[list[str]] = []
    dts_rows: list[list[str]] = []
    akey = 1

    # Inclui Arsenal (1), Real Madrid (243), Man City (10) e Hospedeiros (305, 306)
    for tid, base_pid in [(1, 500), (243, 1000), (10, 2000), (305, 3000), (306, 4000)]:
        pids: list[str] = []
        for slot in range(15):
            pid = str(base_pid + slot)
            pos = "0" if slot == 0 else (str(slot + 2) if slot <= 10 else "28")
            tpl_rows.append([str(akey), str(tid), pid, str(slot + 1), pos])
            pids.append(pid)
            akey += 1
        dts_rows.append([str(tid), pids[9], pids[10]] + pids + ["-1", "-1", "-1"])

    for slot in range(10):
        pid = str(9000 + slot)
        pos = "0" if slot == 0 else "29"
        tpl_rows.append([str(akey), str(FREE_AGENTS_TEAM_ID), pid, "99", pos])
        akey += 1

    # Selecao Francesa (1335, leagueid 78)
    tpl_rows.append([str(akey), "1335", "1009", "10", "25"])
    akey += 1

    # Linhas de buffer reais do RDBM (bitpack low: teamid=1, artificialkey=0, playerid0=-1)
    tpl_rows.append(["0", "1", "1", "1", "0"])
    tpl_rows.append(["0", "0", "0", "0", "0"])
    dts_rows.append(["1", "-1", "-1"] + ["-1"] * 18)

    _write_utf16_tsv(base_dir / "teamplayerlinks.txt", tpl_headers, tpl_rows)
    _write_utf16_tsv(base_dir / "default_teamsheets.txt", dts_headers, dts_rows)
    _write_utf16_tsv(
        base_dir / "leagueteamlinks.txt",
        ["artificialkey", "leagueid", "teamid"],
        [["1", "78", "1335"], ["2", "13", "1"], ["3", "53", "243"], ["4", "65", "305"], ["0", "1", "1"]],
    )
    _write_utf16_tsv(
        base_dir / "playerloans.txt",
        ["artificialkey", "playerid", "teamidloanedfrom"],
        [["1", "1009", "50"], ["0", "1", "1"]],
    )


def test_utf16le_bom_and_buffer_rows_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "sample.txt"
    original = _write_utf16_tsv(
        p, ["artificialkey", "teamid", "playerid"], [["1", "243", "231747"], ["0", "0", "0"]]
    )
    table = read_rdbm_table(p)
    out = tmp_path / "out.txt"
    write_rdbm_table(table, out)
    assert out.read_bytes() == original


def test_patch_transfers_backfills_origin_and_preserves_national_team(tmp_path: Path) -> None:
    in_dir = tmp_path / "clean"
    out_dir = tmp_path / "patched"
    in_dir.mkdir()
    _build_mock_rdbm_env(in_dir)

    joao_players = [
        DraftedPlayerInput(player_id=1000, short_name="Courtois", primary_position="GK", overall=90),
        *[
            DraftedPlayerInput(
                player_id=1001 + i,
                short_name=f"StarRM_{i}",
                primary_position="CB" if i < 4 else ("CM" if i < 7 else "ST"),
                overall=91 - i,
            )
            for i in range(11)
        ],
    ]
    payload = DraftExportPayload(
        edition="fc26",
        league_name="Liga Amigos",
        teams=[DraftTeamAssignment(participant_name="Joao", host_team_id=305, players=joao_players)],
    )

    summary = patch_rdbm_directory(payload, in_dir, out_dir)
    assert summary.teams_patched == 1
    assert summary.players_transferred == 12
    assert summary.backfill_swaps == 12
    assert summary.loans_removed == 1

    patched_tpl = read_rdbm_table(out_dir / "teamplayerlinks.txt")
    patched_dts = read_rdbm_table(out_dir / "default_teamsheets.txt")

    # Selecao Nacional 1335 permanece intacta com o atleta 1009
    nt_rows = [r for r in patched_tpl.rows if r["teamid"] == "1335"]
    assert len(nt_rows) == 1 and nt_rows[0]["playerid"] == "1009"

    # Clube hospedeiro 305 contem exclusivamente os 12 atletas draftados do Joao
    host_pids = {int(r["playerid"]) for r in patched_tpl.rows if r["teamid"] == "305"}
    assert host_pids == {p.player_id for p in joao_players}

    # Real Madrid (243) manteve exatamente 15 jogadores e zero orfaos em default_teamsheets
    rm_pids = {int(r["playerid"]) for r in patched_tpl.rows if r["teamid"] == "243"}
    assert len(rm_pids) == 15
    rm_ts = next(r for r in patched_dts.rows if r["teamid"] == "243")
    for k in range(11):
        assert int(rm_ts[f"playerid{k}"]) in rm_pids
    assert int(rm_ts["captainid"]) in rm_pids

    # Linha de buffer final preservada
    assert patched_tpl.rows[-1]["artificialkey"] == "0"
    assert (out_dir / "draft_transfers.csv").exists()
    assert (out_dir / "apply_draft_live_editor.lua").exists()


def test_export_api_endpoints(tmp_path: Path) -> None:
    in_dir = tmp_path / "clean"
    out_dir = tmp_path / "api_out"
    in_dir.mkdir()
    _build_mock_rdbm_env(in_dir)

    app = FastAPI()
    app.include_router(export_router, prefix="/api/export")
    client = TestClient(app)

    players = [
        DraftedPlayerInput(player_id=2000, short_name="Ederson", primary_position="GK", overall=88),
        *[
            DraftedPlayerInput(player_id=2001 + i, short_name=f"City_{i}", primary_position="CM", overall=85)
            for i in range(10)
        ],
    ]
    res = client.post(
        "/api/export/patch-rdbm",
        json={
            "payload": {
                "edition": "fc26",
                "league_name": "Teste API",
                "teams": [{"participant_name": "Pedro", "host_team_id": 306, "players": [p.model_dump() for p in players]}],
            },
            "rdbm_input_dir": str(in_dir),
            "output_dir": str(out_dir),
        },
    )
    assert res.status_code == 200
    assert res.json()["players_transferred"] == 11