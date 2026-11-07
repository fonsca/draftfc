from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from backend.exporter.models import (
    DraftedPlayerInput,
    DraftExportPayload,
    PatchSummary,
)

FREE_AGENTS_TEAM_ID = 111592
NATIONAL_LEAGUE_IDS = {"78", "2136"}
SPECIAL_EXHIBITION_TEAM_IDS = {111596, 111597, 112190, 112658}

SECTOR_ORDER: dict[str, int] = {
    "GK": 0,
    "CB": 1, "LB": 1, "RB": 1, "LWB": 1, "RWB": 1,
    "CDM": 2, "CM": 2, "CAM": 2, "LM": 2, "RM": 2,
    "LW": 3, "RW": 3, "CF": 3, "ST": 3,
}

ROLE_KEYWORDS = (
    "captain",
    "freekick",
    "fk",
    "corner",
    "penalt",
    "throwin",
    "kickid",
)

FALLBACK_STARTER_POSITIONS = [
    "0", "3", "4", "6", "7", "14", "14", "18", "23", "25", "27"
]


@dataclass
class RdbmTable:
    headers: list[str]
    rows: list[dict[str, str]]
    bom_kind: str
    line_sep: str
    trailing_newline: bool


def read_rdbm_table(path: Path) -> RdbmTable:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        bom_kind = "utf-16-le-bom"
        text = raw[2:].decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        bom_kind = "utf-16-be-bom"
        text = raw[2:].decode("utf-16-be")
    elif raw.startswith(b"\xef\xbb\xbf"):
        bom_kind = "utf-8-sig"
        text = raw[3:].decode("utf-8")
    else:
        try:
            bom_kind = "utf-8"
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            bom_kind = "cp1252"
            text = raw.decode("cp1252")

    line_sep = "\r\n" if "\r\n" in text else "\n"
    trailing_newline = text.endswith(("\r\n", "\n"))
    lines = text.splitlines()
    if not lines:
        raise ValueError(f"Tabela RDBM vazia: {path}")

    headers = [h.strip() for h in lines[0].split("\t")]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if line == "":
            continue
        cols = line.split("\t")
        if len(cols) < len(headers):
            cols.extend([""] * (len(headers) - len(cols)))
        rows.append(dict(zip(headers, cols[: len(headers)])))

    return RdbmTable(
        headers=headers,
        rows=rows,
        bom_kind=bom_kind,
        line_sep=line_sep,
        trailing_newline=trailing_newline,
    )


def write_rdbm_table(table: RdbmTable, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out_lines = ["\t".join(table.headers)]
    for row in table.rows:
        out_lines.append("\t".join(row.get(h, "") for h in table.headers))

    body = table.line_sep.join(out_lines)
    if table.trailing_newline:
        body += table.line_sep

    if table.bom_kind == "utf-16-le-bom":
        raw = b"\xff\xfe" + body.encode("utf-16-le")
    elif table.bom_kind == "utf-16-be-bom":
        raw = b"\xfe\xff" + body.encode("utf-16-be")
    elif table.bom_kind == "utf-8-sig":
        raw = b"\xef\xbb\xbf" + body.encode("utf-8")
    elif table.bom_kind == "cp1252":
        raw = body.encode("cp1252", errors="replace")
    else:
        raw = body.encode("utf-8")

    path.write_bytes(raw)


def _is_tpl_buffer_row(row: dict[str, str], idx: int) -> bool:
    """Detecta linhas de buffer no fim de teamplayerlinks.txt (onde teamid=1 ou 0 e artificialkey=0)."""
    pid = int(row.get("playerid") or 0)
    tid = int(row.get("teamid") or 0)
    if pid <= 0 or tid <= 0:
        return True
    if "artificialkey" in row and idx > 0 and int(row.get("artificialkey") or 0) == 0:
        return True
    return False


def _is_valid_dts_row(row: dict[str, str]) -> bool:
    """Ignora linhas de buffer no fim de default_teamsheets.txt (onde teamid=1 e playerid0=-1)."""
    tid = int(row.get("teamid") or 0)
    p0 = int(row.get("playerid0") or -1)
    p1 = int(row.get("playerid1") or -1)
    return tid > 0 and p0 > 0 and p1 > 0


def _is_player_ref_col(col: str) -> bool:
    low = col.lower()
    if low in {"teamid", "artificialkey"}:
        return False
    return low.startswith("playerid") or any(k in low for k in ROLE_KEYWORDS)


def _order_squad_tactically(
    players: list[DraftedPlayerInput],
) -> list[DraftedPlayerInput]:
    gks = sorted(
        [p for p in players if p.primary_position.upper() == "GK"],
        key=lambda x: -x.overall,
    )
    outfield = sorted(
        [p for p in players if p.primary_position.upper() != "GK"],
        key=lambda x: -x.overall,
    )
    if gks:
        starter_gk = gks[0]
        extra_gks = gks[1:]
    else:
        starter_gk = outfield[-1]
        outfield = outfield[:-1]
        extra_gks = []

    top_10_outfield = sorted(
        outfield[:10],
        key=lambda p: (SECTOR_ORDER.get(p.primary_position.upper(), 2), -p.overall),
    )
    bench_and_reserves = extra_gks[:1] + outfield[10:] + extra_gks[1:]
    return [starter_gk, *top_10_outfield, *bench_and_reserves]


def _load_national_team_ids(rdbm_dir: Path) -> set[int]:
    nat_ids: set[int] = set(SPECIAL_EXHIBITION_TEAM_IDS)
    ltl_path = rdbm_dir / "leagueteamlinks.txt"
    if not ltl_path.exists():
        return nat_ids
    ltl = read_rdbm_table(ltl_path)
    for idx, row in enumerate(ltl.rows):
        if "artificialkey" in row and idx > 0 and int(row.get("artificialkey") or 0) == 0:
            continue
        tid = int(row.get("teamid") or 0)
        lid = (row.get("leagueid") or "").strip()
        if tid > 0 and lid in NATIONAL_LEAGUE_IDS:
            nat_ids.add(tid)
    return nat_ids


def write_auxiliary_exports(
    payload: DraftExportPayload, output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "draft_transfers.csv"
    lua_path = output_dir / "apply_draft_live_editor.lua"

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "participant_name",
            "host_team_id",
            "slot_index",
            "role",
            "jersey_number",
            "player_id",
            "short_name",
            "primary_position",
            "overall",
        ])
        for team in payload.teams:
            ordered = _order_squad_tactically(team.players)
            for idx, p in enumerate(ordered):
                role = "XI" if idx <= 10 else ("SUB" if idx <= 17 else "RES")
                writer.writerow([
                    team.participant_name,
                    team.host_team_id,
                    idx,
                    role,
                    idx + 1,
                    p.player_id,
                    p.short_name,
                    p.primary_position,
                    p.overall,
                ])

    lua_lines = [
        f"-- DraftFC Live Editor Script ({payload.league_name})",
        "-- Alternativa rapida em memoria para FC Live Editor (Aranaktu)",
    ]
    for team in payload.teams:
        lua_lines.append(
            f"\n-- {team.participant_name} -> Host Team {team.host_team_id}"
        )
        for p in _order_squad_tactically(team.players):
            lua_lines.append(
                f"TransferPlayer({p.player_id}, {team.host_team_id})"
            )
    lua_path.write_text("\n".join(lua_lines) + "\n", encoding="utf-8")
    return csv_path, lua_path


def _repair_origin_teamsheet(
    ts_row: dict[str, str],
    roster_pids: list[int],
    headers: list[str],
    playerid_cols: list[str],
) -> None:
    """Garante que nenhum slot titular, reserva ou funcao tecnica aponte para fora do elenco do clube."""
    if not roster_pids:
        return
    roster_set = set(roster_pids)
    used_pids = {
        int(ts_row.get(c) or -1)
        for c in playerid_cols
        if int(ts_row.get(c) or -1) in roster_set
    }
    unused_pool = [pid for pid in roster_pids if pid not in used_pids]

    for col in playerid_cols:
        slot_k = int(col[8:])
        val = int(ts_row.get(col) or -1)
        if slot_k < 11:
            if val not in roster_set:
                rep = unused_pool.pop(0) if unused_pool else roster_pids[slot_k % len(roster_pids)]
                ts_row[col] = str(rep)
                used_pids.add(rep)
        else:
            if val > 0 and val not in roster_set:
                ts_row[col] = str(unused_pool.pop(0)) if unused_pool else "-1"

    leader = ts_row.get("playerid9") or ts_row.get("playerid0") or str(roster_pids[0])
    if int(leader or -1) not in roster_set:
        leader = str(roster_pids[0])

    for col in headers:
        low = col.lower()
        if _is_player_ref_col(col) and not low.startswith("playerid"):
            val = int(ts_row.get(col) or -1)
            if val > 0 and val not in roster_set:
                ts_row[col] = "-1" if "sub" in low else leader


def patch_rdbm_directory(
    payload: DraftExportPayload,
    rdbm_input_dir: Path,
    output_dir: Path,
    known_club_ids: set[int] | None = None,
) -> PatchSummary:
    tpl_path = rdbm_input_dir / "teamplayerlinks.txt"
    dts_path = rdbm_input_dir / "default_teamsheets.txt"
    if not tpl_path.exists() or not dts_path.exists():
        raise FileNotFoundError(
            "Arquivos obrigatorios ausentes: exporte teamplayerlinks.txt e "
            "default_teamsheets.txt pelo RDBM."
        )

    tpl = read_rdbm_table(tpl_path)
    dts = read_rdbm_table(dts_path)
    national_ids = _load_national_team_ids(rdbm_input_dir)

    host_ids = [t.host_team_id for t in payload.teams]
    if len(host_ids) != len(set(host_ids)):
        raise ValueError("Cada participante precisa de um host_team_id exclusivo.")
    if FREE_AGENTS_TEAM_ID in host_ids:
        raise ValueError("Free Agents (111592) nao pode ser usado como time hospedeiro.")
    host_id_set = set(host_ids)

    all_requested_pids = [p.player_id for t in payload.teams for p in t.players]
    if len(all_requested_pids) != len(set(all_requested_pids)):
        raise ValueError("Existem jogadores duplicados entre os elencos do draft.")

    # Indexa apenas linhas validas de default_teamsheets (ignorando buffer final com teamid=1 e playerid0=-1)
    dts_by_team: dict[int, dict[str, str]] = {}
    for row in dts.rows:
        if not _is_valid_dts_row(row):
            continue
        tid = int(row.get("teamid") or 0)
        if tid not in dts_by_team:
            dts_by_team[tid] = row

    for hid in host_ids:
        if hid not in dts_by_team:
            raise ValueError(
                f"Clube hospedeiro {hid} nao encontrado em default_teamsheets.txt."
            )

    pid_to_row_idx: dict[int, int] = {}
    team_to_row_indices: dict[int, list[int]] = {}
    for idx, row in enumerate(tpl.rows):
        if _is_tpl_buffer_row(row, idx):
            continue
        pid = int(row.get("playerid") or 0)
        tid = int(row.get("teamid") or 0)
        if tid in national_ids:
            continue
        if pid in pid_to_row_idx:
            prev_tid = int(tpl.rows[pid_to_row_idx[pid]]["teamid"])
            if known_club_ids and tid in known_club_ids and prev_tid not in known_club_ids:
                team_to_row_indices[prev_tid].remove(pid_to_row_idx[pid])
            elif tid in dts_by_team and prev_tid not in dts_by_team:
                team_to_row_indices[prev_tid].remove(pid_to_row_idx[pid])
            else:
                continue
        pid_to_row_idx[pid] = idx
        team_to_row_indices.setdefault(tid, []).append(idx)

    missing_pids: list[int] = []
    valid_teams: list[tuple[int, str, list[DraftedPlayerInput]]] = []
    valid_drafted_set: set[int] = set()

    for team in payload.teams:
        present: list[DraftedPlayerInput] = []
        for p in team.players:
            if p.player_id in pid_to_row_idx:
                present.append(p)
                valid_drafted_set.add(p.player_id)
            else:
                missing_pids.append(p.player_id)
        if len(present) < 11:
            raise ValueError(
                f"Time de {team.participant_name} ficou com menos de 11 atletas "
                f"existentes neste arquivo Squads ({len(present)} encontrados)."
            )
        valid_teams.append((team.host_team_id, team.participant_name, present))

    host_templates: dict[int, list[str]] = {}
    host_orig_pids: dict[int, set[str]] = {}
    gk_fillers: list[int] = []
    out_fillers: list[int] = []

    for hid in host_ids:
        ts_row = dts_by_team[hid]
        starter_pos: list[str] = []
        for s_idx in range(11):
            spid = int(ts_row.get(f"playerid{s_idx}") or -1)
            r_idx = pid_to_row_idx.get(spid)
            if r_idx is not None:
                starter_pos.append(tpl.rows[r_idx].get("position") or FALLBACK_STARTER_POSITIONS[s_idx])
            else:
                starter_pos.append(FALLBACK_STARTER_POSITIONS[s_idx])
        starter_pos[0] = "0"
        host_templates[hid] = starter_pos

        orig_set: set[str] = set()
        ts_gk_pid = int(ts_row.get("playerid0") or -1)
        for r_idx in team_to_row_indices.get(hid, []):
            pid = int(tpl.rows[r_idx]["playerid"])
            orig_set.add(str(pid))
            if pid not in valid_drafted_set:
                is_gk = tpl.rows[r_idx].get("position") == "0" or pid == ts_gk_pid
                (gk_fillers if is_gk else out_fillers).append(pid)
        host_orig_pids[hid] = orig_set

    fa_gks: list[int] = []
    fa_out: list[int] = []
    for r_idx in team_to_row_indices.get(FREE_AGENTS_TEAM_ID, []):
        pid = int(tpl.rows[r_idx]["playerid"])
        if pid not in valid_drafted_set:
            is_gk = tpl.rows[r_idx].get("position") == "0"
            (fa_gks if is_gk else fa_out).append(pid)

    def pop_filler(want_gk: bool) -> int:
        pools = (
            (gk_fillers, fa_gks, out_fillers, fa_out)
            if want_gk
            else (out_fillers, fa_out, gk_fillers, fa_gks)
        )
        for pool in pools:
            if pool:
                return pool.pop(0)
        raise RuntimeError("Pool de reposicao esgotado ao recompor clubes de origem.")

    moved_pids: set[int] = set(valid_drafted_set)
    touched_origin_clubs: set[int] = set()
    backfill_swaps = 0

    for _, _, players in valid_teams:
        for p in players:
            orig_row = tpl.rows[pid_to_row_idx[p.player_id]]
            orig_tid = int(orig_row["teamid"])
            if orig_tid in host_id_set or orig_tid == FREE_AGENTS_TEAM_ID:
                continue

            orig_pos = orig_row.get("position") or "28"
            orig_jersey = orig_row.get("jerseynumber") or "99"
            orig_ts = dts_by_team.get(orig_tid)
            was_gk = (
                p.primary_position.upper() == "GK"
                or orig_pos == "0"
                or (orig_ts is not None and orig_ts.get("playerid0") == str(p.player_id))
            )

            filler_pid = pop_filler(want_gk=was_gk)
            moved_pids.add(filler_pid)
            filler_row = tpl.rows[pid_to_row_idx[filler_pid]]
            filler_row["teamid"] = str(orig_tid)
            filler_row["position"] = orig_pos
            filler_row["jerseynumber"] = orig_jersey

            if orig_ts is not None:
                old_str = str(p.player_id)
                new_str = str(filler_pid)
                for col in dts.headers:
                    if _is_player_ref_col(col) and orig_ts.get(col) == old_str:
                        orig_ts[col] = new_str

            touched_origin_clubs.add(orig_tid)
            backfill_swaps += 1

    for leftover_pid in [*gk_fillers, *out_fillers]:
        moved_pids.add(leftover_pid)
        l_row = tpl.rows[pid_to_row_idx[leftover_pid]]
        l_row["teamid"] = str(FREE_AGENTS_TEAM_ID)
        l_row["position"] = "29"

    total_transferred = 0
    playerid_cols = sorted(
        [c for c in dts.headers if c.lower().startswith("playerid") and c[8:].isdigit()],
        key=lambda c: int(c[8:]),
    )

    for hid, _, players in valid_teams:
        ordered = _order_squad_tactically(players)
        template_pos = host_templates[hid]
        new_pid_strs = {str(p.player_id) for p in ordered}

        for idx, p in enumerate(ordered):
            r = tpl.rows[pid_to_row_idx[p.player_id]]
            r["teamid"] = str(hid)
            r["jerseynumber"] = str(idx + 1)
            if idx == 0:
                r["position"] = "0"
            elif idx <= 10:
                r["position"] = template_pos[idx]
            elif idx <= 17:
                r["position"] = "28"
            else:
                r["position"] = "29"
            total_transferred += 1

        ts_row = dts_by_team[hid]
        for col in playerid_cols:
            slot_k = int(col[8:])
            ts_row[col] = str(ordered[slot_k].player_id) if slot_k < len(ordered) else "-1"

        leader_id = str(max(ordered[1:11], key=lambda x: x.overall).player_id)
        for col in dts.headers:
            low = col.lower()
            if any(k in low for k in ROLE_KEYWORDS):
                if ts_row.get(col, "-1") not in {"-1", "0", ""}:
                    ts_row[col] = leader_id
            elif _is_player_ref_col(col) and not low.startswith("playerid"):
                if ts_row.get(col) in host_orig_pids[hid] and ts_row.get(col) not in new_pid_strs:
                    ts_row[col] = "-1" if "sub" in low else leader_id

    # Recompoe eventuais reservas/funcoes orfas pre-existentes nos clubes de origem tocados
    club_current_pids: dict[int, list[int]] = {}
    for pid, r_idx in pid_to_row_idx.items():
        tid = int(tpl.rows[r_idx].get("teamid") or 0)
        if tid in touched_origin_clubs:
            club_current_pids.setdefault(tid, []).append(pid)

    for cid in touched_origin_clubs:
        if cid in dts_by_team and cid in club_current_pids:
            _repair_origin_teamsheet(
                dts_by_team[cid],
                club_current_pids[cid],
                dts.headers,
                playerid_cols,
            )

    loans_removed = 0
    loans_in = rdbm_input_dir / "playerloans.txt"
    loans_table: RdbmTable | None = None
    if loans_in.exists():
        loans_table = read_rdbm_table(loans_in)
        buffer_template: dict[str, str] | None = None
        if loans_table.rows:
            last_r = loans_table.rows[-1]
            if int(last_r.get("artificialkey") or 0) == 0 or int(last_r.get("playerid") or 0) == 0:
                buffer_template = dict(last_r)

        kept_rows: list[dict[str, str]] = []
        for idx, r in enumerate(loans_table.rows):
            is_buf = idx > 0 and "artificialkey" in r and int(r.get("artificialkey") or 0) == 0
            pid = int(r.get("playerid") or 0)
            if not is_buf and pid in moved_pids:
                loans_removed += 1
            else:
                kept_rows.append(r)
        if buffer_template is not None and loans_removed > 0:
            kept_rows.extend(dict(buffer_template) for _ in range(loans_removed))
        loans_table.rows = kept_rows

    _validate_patched_state(
        tpl=tpl,
        dts_by_team=dts_by_team,
        pid_to_row_idx=pid_to_row_idx,
        inspected_clubs=host_id_set | touched_origin_clubs,
        playerid_cols=playerid_cols,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_rdbm_table(tpl, output_dir / "teamplayerlinks.txt")
    write_rdbm_table(dts, output_dir / "default_teamsheets.txt")
    if loans_table is not None:
        write_rdbm_table(loans_table, output_dir / "playerloans.txt")
    write_auxiliary_exports(payload, output_dir)

    return PatchSummary(
        edition=payload.edition,
        teams_patched=len(valid_teams),
        players_transferred=total_transferred,
        backfill_swaps=backfill_swaps,
        loans_removed=loans_removed,
        missing_player_ids=missing_pids,
        output_dir=str(output_dir),
    )


def _validate_patched_state(
    tpl: RdbmTable,
    dts_by_team: dict[int, dict[str, str]],
    pid_to_row_idx: dict[int, int],
    inspected_clubs: set[int],
    playerid_cols: list[str],
) -> None:
    club_rosters: dict[int, set[int]] = {}
    seen_pid_club: dict[int, int] = {}

    for pid, r_idx in pid_to_row_idx.items():
        row = tpl.rows[r_idx]
        tid = int(row.get("teamid") or 0)
        if pid in seen_pid_club:
            raise AssertionError(
                f"Inconsistencia detectada: atleta {pid} duplicado nos clubes "
                f"{seen_pid_club[pid]} e {tid}."
            )
        seen_pid_club[pid] = tid
        club_rosters.setdefault(tid, set()).add(pid)

    for cid in inspected_clubs:
        roster = club_rosters.get(cid, set())
        if len(roster) < 11:
            raise AssertionError(f"Clube {cid} ficou com apenas {len(roster)} atletas.")
        ts = dts_by_team.get(cid)
        if ts is None:
            continue
        for k in range(11):
            spid = int(ts.get(f"playerid{k}") or -1)
            if spid not in roster:
                raise AssertionError(
                    f"Titular slot {k} ({spid}) do clube {cid} nao pertence ao elenco em teamplayerlinks."
                )
        for col in playerid_cols:
            val = int(ts.get(col) or -1)
            if val > 0 and val not in roster:
                raise AssertionError(
                    f"Atleta orfao {val} na coluna {col} de default_teamsheets do clube {cid}."
                )