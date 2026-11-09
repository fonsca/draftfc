from __future__ import annotations

import csv
import json
import os
import shutil
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DraftExportPlayer(BaseModel):
    player_id: int
    short_name: str
    primary_position: str = "CM"
    overall: int = 75
    jersey_number: int | None = None


class DraftExportTeam(BaseModel):
    participant_id: int
    participant_name: str
    host_team_id: int
    players: list[DraftExportPlayer] = Field(default_factory=list)


class DraftExportRequest(BaseModel):
    edition: str = "fc26"
    teams: list[DraftExportTeam] = Field(default_factory=list)


class DraftExportResult(BaseModel):
    edition: str
    teams_patched: int
    players_transferred: int
    backfill_swaps: int
    loans_removed: int
    missing_player_ids: list[int] = Field(default_factory=list)
    output_dir: str


POSITION_TO_EA_ID: dict[str, int] = {
    "GK": 0,
    "GL": 0,
    "SW": 1,
    "RWB": 2,
    "RB": 3,
    "LD": 3,
    "RCB": 4,
    "CB": 5,
    "ZAG": 5,
    "LCB": 6,
    "LB": 7,
    "LE": 7,
    "LWB": 8,
    "RDM": 9,
    "CDM": 10,
    "VOL": 10,
    "LDM": 11,
    "RM": 12,
    "MD": 12,
    "RCM": 13,
    "CM": 14,
    "MC": 14,
    "LCM": 15,
    "LM": 16,
    "ME": 16,
    "RAM": 17,
    "CAM": 18,
    "MEI": 18,
    "LAM": 19,
    "RF": 20,
    "CF": 21,
    "SA": 21,
    "LF": 22,
    "RW": 23,
    "PD": 23,
    "RS": 24,
    "ST": 25,
    "ATA": 25,
    "LS": 26,
    "LW": 27,
    "PE": 27,
}


def read_bits_le(buf: bytes | bytearray | memoryview, bit_offset: int, bit_depth: int) -> int:
    """Lê um inteiro sem sinal de `bit_depth` bits a partir de `bit_offset` (Little-Endian)."""
    byte_pos = bit_offset >> 3
    bit_shift = bit_offset & 7
    needed_bytes = (bit_shift + bit_depth + 7) >> 3
    chunk = bytes(buf[byte_pos : byte_pos + needed_bytes])
    if len(chunk) < needed_bytes:
        chunk = chunk + b"\x00" * (needed_bytes - len(chunk))
    val = int.from_bytes(chunk, "little")
    mask = (1 << bit_depth) - 1
    return (val >> bit_shift) & mask


def write_bits_le(buf: bytearray, bit_offset: int, bit_depth: int, value: int) -> None:
    """Escreve um inteiro sem sinal de `bit_depth` bits em `bit_offset` (Little-Endian) in-place."""
    if value < 0:
        value = 0
    mask = (1 << bit_depth) - 1
    value &= mask

    byte_pos = bit_offset >> 3
    bit_shift = bit_offset & 7
    needed_bytes = (bit_shift + bit_depth + 7) >> 3

    orig_chunk = bytes(buf[byte_pos : byte_pos + needed_bytes])
    if len(orig_chunk) < needed_bytes:
        orig_chunk += b"\x00" * (needed_bytes - len(orig_chunk))

    orig_int = int.from_bytes(orig_chunk, "little")
    clear_mask = ~(mask << bit_shift)
    updated_int = (orig_int & clear_mask) | (value << bit_shift)
    buf[byte_pos : byte_pos + needed_bytes] = updated_int.to_bytes(needed_bytes, "little")


def _read_rdbm_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Lê arquivo .txt exportado pelo RDBM (UTF-16 LE com BOM ou UTF-8)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        text = raw[2:].decode("utf-16-le", errors="replace")
    elif raw.startswith(b"\xfe\xff"):
        text = raw[2:].decode("utf-16-be", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], []

    headers = [h.strip() for h in lines[0].split("\t")]
    rows: list[dict[str, str]] = []
    for ln in lines[1:]:
        vals = ln.split("\t")
        if len(vals) < len(headers):
            vals += ["0"] * (len(headers) - len(vals))
        row_dict = {headers[i]: vals[i].strip() for i in range(len(headers))}
        # Ignora linhas de buffer zeradas no final do arquivo
        if all(v in ("0", "") for v in row_dict.values()):
            continue
        rows.append(row_dict)
    return headers, rows


@dataclass
class BinaryFieldSchema:
    column_name: str
    field_type: int
    bit_offset: int
    bit_depth: int
    range_low: int
    short_tag: str


@dataclass
class BinaryTableSchema:
    table_name: str
    short_tag: str
    table_offset: int
    records_data_offset: int
    record_size: int
    num_records: int
    valid_records: int
    crc_offset: int | None
    crc_mode: str | None
    fields: dict[str, BinaryFieldSchema]


def discover_local_squads_files() -> list[dict[str, Any]]:
    """Localiza arquivos Squads* nas pastas padrão do EA SPORTS FC 26 e 27 no Windows."""
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    user_prof = Path(os.environ.get("USERPROFILE", ""))

    search_dirs = [
        local_app / "EA SPORTS FC 26" / "settings",
        local_app / "EA SPORTS FC 27" / "settings",
        user_prof / "Documents" / "FC 26" / "settings",
        user_prof / "Documents" / "FC 27" / "settings",
        Path("data/backup_squads"),
    ]

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    for d in search_dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("Squads*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and not f.name.endswith((".txt", ".json", ".bak", ".csv", ".lua")):
                resolved = str(f.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                stat = f.stat()
                found.append(
                    {
                        "name": f.name,
                        "path": resolved,
                        "folder": str(d),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
    return found


def _compute_crc_variants(data: bytes) -> dict[str, bytes]:
    c_std = zlib.crc32(data) & 0xFFFFFFFF
    c_inv = (~c_std) & 0xFFFFFFFF
    return {
        "zlib_le": c_std.to_bytes(4, "little"),
        "zlib_be": c_std.to_bytes(4, "big"),
        "inv_le": c_inv.to_bytes(4, "little"),
        "inv_be": c_inv.to_bytes(4, "big"),
    }


def _apply_crc_mode(data: bytes, mode: str) -> bytes:
    variants = _compute_crc_variants(data)
    return variants.get(mode, variants["zlib_le"])


class SquadsBinaryPatcher:
    """
    Motor de leitura, auto-calibração e escrita binária direta em arquivos Squads (DB 8.0) do EA FC.
    """

    def __init__(
        self,
        rdbm_clean_dir: Path = Path("data/rdbm_clean"),
        schema_cache_path: Path = Path("data/squads_binary_schema.json"),
        backup_dir: Path = Path("data/backup_squads"),
    ) -> None:
        self.rdbm_clean_dir = rdbm_clean_dir
        self.schema_cache_path = schema_cache_path
        self.backup_dir = backup_dir

    def _find_db08_blocks(self, raw: bytes | bytearray) -> list[int]:
        magic = b"DB\x00\x08\x00\x00\x00\x00"
        offsets: list[int] = []
        pos = 0
        while True:
            idx = raw.find(magic, pos)
            if idx == -1:
                break
            offsets.append(idx)
            pos = idx + 8
        return offsets

    def _parse_raw_db_tables(self, raw: bytes | bytearray, db_start: int) -> list[dict[str, Any]]:
        if len(raw) < db_start + 24:
            return []

        db_size = struct.unpack_from("<I", raw, db_start + 8)[0]
        num_tables = struct.unpack_from("<I", raw, db_start + 16)[0]
        if num_tables == 0 or num_tables > 500:
            return []

        dir_start = db_start + 24
        table_data_base = dir_start + (num_tables * 8) + 4
        parsed_tables: list[dict[str, Any]] = []

        for i in range(num_tables):
            entry_pos = dir_start + i * 8
            if entry_pos + 8 > len(raw):
                break
            short_tag = raw[entry_pos : entry_pos + 4].decode("latin-1", errors="replace")
            rel_off = struct.unpack_from("<I", raw, entry_pos + 4)[0]
            tbl_pos = table_data_base + rel_off
            if tbl_pos + 32 > len(raw):
                continue

            record_size = struct.unpack_from("<I", raw, tbl_pos + 4)[0]
            bit_records = struct.unpack_from("<I", raw, tbl_pos + 8)[0]
            compressed_len = struct.unpack_from("<I", raw, tbl_pos + 12)[0]
            num_records = struct.unpack_from("<H", raw, tbl_pos + 16)[0]
            valid_records = struct.unpack_from("<H", raw, tbl_pos + 18)[0]
            num_fields = raw[tbl_pos + 24]

            fields_start = tbl_pos + 32
            fields_end = fields_start + (num_fields * 16)
            if fields_end + 4 > len(raw):
                continue

            raw_fields: list[dict[str, Any]] = []
            for f_idx in range(num_fields):
                f_pos = fields_start + f_idx * 16
                f_type, bit_off = struct.unpack_from("<II", raw, f_pos)
                f_tag = raw[f_pos + 8 : f_pos + 12].decode("latin-1", errors="replace")
                bit_depth = struct.unpack_from("<I", raw, f_pos + 12)[0]
                raw_fields.append(
                    {
                        "field_type": f_type,
                        "bit_offset": bit_off,
                        "short_tag": f_tag,
                        "bit_depth": bit_depth,
                    }
                )

            records_data_offset = fields_end + 4
            records_byte_len = num_records * record_size

            parsed_tables.append(
                {
                    "short_tag": short_tag,
                    "table_offset": tbl_pos,
                    "records_data_offset": records_data_offset,
                    "record_size": record_size,
                    "bit_records": bit_records,
                    "compressed_len": compressed_len,
                    "num_records": num_records,
                    "valid_records": valid_records,
                    "num_fields": num_fields,
                    "raw_fields": raw_fields,
                    "records_byte_len": records_byte_len,
                    "db_size": db_size,
                }
            )
        return parsed_tables

    def calibrate_table_schema(
        self,
        raw: bytes | bytearray,
        raw_tables: list[dict[str, Any]],
        table_name: str,
        txt_path: Path,
        required_columns: list[str],
    ) -> BinaryTableSchema | None:
        if not txt_path.exists():
            return None

        headers, txt_rows = _read_rdbm_tsv(txt_path)
        if not txt_rows:
            return None

        sample_rows = txt_rows[: min(25, len(txt_rows))]
        num_txt_rows = len(txt_rows)
        num_txt_cols = len(headers)

        candidates = [
            t
            for t in raw_tables
            if t["num_fields"] == num_txt_cols
            and t["record_size"] > 0
            and abs(t["valid_records"] - num_txt_rows) <= max(50, int(num_txt_rows * 0.15))
        ]
        if not candidates:
            candidates = [
                t
                for t in raw_tables
                if t["num_fields"] == num_txt_cols and t["valid_records"] >= len(sample_rows)
            ]

        for cand in candidates:
            rec_base = cand["records_data_offset"]
            rec_size = cand["record_size"]
            int_fields = [
                f for f in cand["raw_fields"] if f["field_type"] == 3 and 1 <= f["bit_depth"] <= 32
            ]

            matched_fields: dict[str, BinaryFieldSchema] = {}
            used_bit_offsets: set[int] = set()

            for col_name in required_columns:
                if col_name not in headers:
                    continue
                try:
                    sample_vals = [int(r[col_name]) for r in sample_rows]
                except ValueError:
                    continue

                for f_desc in int_fields:
                    b_off = f_desc["bit_offset"]
                    b_depth = f_desc["bit_depth"]
                    if b_off in used_bit_offsets:
                        continue

                    diffs: set[int] = set()
                    valid_field = True
                    for r_idx, target_val in enumerate(sample_vals):
                        row_pos = rec_base + (r_idx * rec_size)
                        if row_pos + rec_size > len(raw):
                            valid_field = False
                            break
                        raw_unsigned = read_bits_le(
                            memoryview(raw)[row_pos : row_pos + rec_size],
                            b_off,
                            b_depth,
                        )
                        diffs.add(target_val - raw_unsigned)
                        if len(diffs) > 1:
                            valid_field = False
                            break

                    if valid_field and len(diffs) == 1:
                        range_low = diffs.pop()
                        matched_fields[col_name] = BinaryFieldSchema(
                            column_name=col_name,
                            field_type=f_desc["field_type"],
                            bit_offset=b_off,
                            bit_depth=b_depth,
                            range_low=range_low,
                            short_tag=f_desc["short_tag"],
                        )
                        used_bit_offsets.add(b_off)
                        break

            if len(matched_fields) == len(required_columns):
                crc_off, crc_mode = self._detect_table_crc(raw, cand)
                return BinaryTableSchema(
                    table_name=table_name,
                    short_tag=cand["short_tag"],
                    table_offset=cand["table_offset"],
                    records_data_offset=rec_base,
                    record_size=rec_size,
                    num_records=cand["num_records"],
                    valid_records=cand["valid_records"],
                    crc_offset=crc_off,
                    crc_mode=crc_mode,
                    fields=matched_fields,
                )

        return None

    def _detect_table_crc(
        self, raw: bytes | bytearray, cand: dict[str, Any]
    ) -> tuple[int | None, str | None]:
        rec_start = cand["records_data_offset"]
        rec_len = cand["records_byte_len"]
        for tail_extra in range(0, 512, 4):
            end_pos = rec_start + rec_len + tail_extra
            if end_pos + 4 > len(raw):
                break
            stored = bytes(raw[end_pos : end_pos + 4])
            for mode, digest in _compute_crc_variants(bytes(raw[rec_start:end_pos])).items():
                if stored == digest:
                    return end_pos, mode
        return None, None

    def _detect_outer_save_crcs(
        self, raw: bytes | bytearray, db_start: int
    ) -> list[tuple[int, int, int, str]]:
        matches: list[tuple[int, int, int, str]] = []
        if db_start <= 0:
            return matches

        total_len = len(raw)
        candidate_ranges = [
            (db_start, total_len),
            (0x22, total_len),
            (0x2E, total_len),
        ]
        if db_start + 12 <= total_len:
            declared_db_size = struct.unpack_from("<I", raw, db_start + 8)[0]
            if 0 < declared_db_size <= total_len - db_start:
                candidate_ranges.append((db_start, db_start + declared_db_size))

        for p_start, p_end in candidate_ranges:
            if p_start >= p_end or p_end > total_len:
                continue
            variants = _compute_crc_variants(bytes(raw[p_start:p_end]))
            for pos in range(0, max(0, db_start - 3)):
                stored = bytes(raw[pos : pos + 4])
                for mode, digest in variants.items():
                    if stored == digest:
                        matches.append((pos, p_start, p_end, mode))
        return matches

    def _read_table_rows_from_binary(
        self, raw: bytes | bytearray, schema: BinaryTableSchema
    ) -> list[dict[str, int]]:
        rows: list[dict[str, int]] = []
        rec_base = schema.records_data_offset
        rec_size = schema.record_size
        for r_idx in range(schema.valid_records):
            row_pos = rec_base + (r_idx * rec_size)
            if row_pos + rec_size > len(raw):
                break
            slice_buf = memoryview(raw)[row_pos : row_pos + rec_size]
            row_dict: dict[str, int] = {"_row_index": r_idx}
            for col_name, f_schema in schema.fields.items():
                u_val = read_bits_le(slice_buf, f_schema.bit_offset, f_schema.bit_depth)
                row_dict[col_name] = f_schema.range_low + u_val
            rows.append(row_dict)
        return rows

    def _write_row_field_in_binary(
        self,
        raw: bytearray,
        schema: BinaryTableSchema,
        row_index: int,
        col_name: str,
        new_value: int,
    ) -> None:
        f_schema = schema.fields[col_name]
        row_pos = schema.records_data_offset + (row_index * schema.record_size)
        u_val = new_value - f_schema.range_low
        row_slice = bytearray(raw[row_pos : row_pos + schema.record_size])
        write_bits_le(row_slice, f_schema.bit_offset, f_schema.bit_depth, u_val)
        raw[row_pos : row_pos + schema.record_size] = row_slice

    def patch_squads_binary_file(
        self,
        squads_path: Path,
        request: DraftExportRequest,
        out_export_dir: Path = Path("data/exports/rdbm_patched"),
    ) -> dict[str, Any]:
        """
        Aplica as transferências do Draft diretamente nos registros bit-packed do arquivo Squads,
        criando backup prévio e gerando planilha de auditoria + script Lua complementar.
        """
        if not squads_path.exists():
            raise FileNotFoundError(f"Arquivo Squads não encontrado: {squads_path}")

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        out_export_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"{squads_path.name}_{ts}.bak"
        shutil.copy2(squads_path, backup_file)

        raw = bytearray(squads_path.read_bytes())
        db_offsets = self._find_db08_blocks(raw)
        if not db_offsets:
            raise ValueError(
                f"Assinatura DB 8.0 ('DB\\x00\\x08') não encontrada em {squads_path.name}."
            )

        main_db_start = db_offsets[0]
        outer_crcs = self._detect_outer_save_crcs(raw, main_db_start)
        raw_tables = self._parse_raw_db_tables(raw, main_db_start)

        # Identifica IDs de Seleções Nacionais (leagueid == 78) para não sobrescrevê-las
        national_team_ids: set[int] = set()
        ltl_path = self.rdbm_clean_dir / "leagueteamlinks.txt"
        if ltl_path.exists():
            _, ltl_rows = _read_rdbm_tsv(ltl_path)
            for r in ltl_rows:
                if int(r.get("leagueid", 0) or 0) == 78:
                    national_team_ids.add(int(r.get("teamid", 0) or 0))

        tpl_schema = self.calibrate_table_schema(
            raw=raw,
            raw_tables=raw_tables,
            table_name="teamplayerlinks",
            txt_path=self.rdbm_clean_dir / "teamplayerlinks.txt",
            required_columns=["artificialkey", "teamid", "playerid", "jerseynumber", "position"],
        )
        dts_cols = ["teamid"] + [f"playerid{i}" for i in range(18)]
        dts_schema = self.calibrate_table_schema(
            raw=raw,
            raw_tables=raw_tables,
            table_name="default_teamsheets",
            txt_path=self.rdbm_clean_dir / "default_teamsheets.txt",
            required_columns=dts_cols,
        )

        binary_tables_patched: list[str] = []
        players_transferred = 0
        backfill_swaps = 0
        loans_removed = 0
        csv_rows: list[dict[str, Any]] = []
        lua_lines: list[str] = ["-- Script Lua gerado automaticamente pelo DraftFC"]

        all_drafted_pids: set[int] = {
            int(p.player_id) for t in request.teams for p in t.players
        }

        if tpl_schema is not None:
            bin_rows = self._read_table_rows_from_binary(raw, tpl_schema)
            club_rows_by_team: dict[int, list[dict[str, int]]] = {}
            player_club_row: dict[int, dict[str, int]] = {}

            for r in bin_rows:
                tid = r["teamid"]
                pid = r["playerid"]
                if tid in national_team_ids:
                    continue
                club_rows_by_team.setdefault(tid, []).append(r)
                if pid > 0 and pid not in player_club_row:
                    player_club_row[pid] = r

            for team in request.teams:
                host_id = int(team.host_team_id)
                host_slots = club_rows_by_team.get(host_id, [])
                displaced_slots = [
                    s for s in host_slots if s["playerid"] not in all_drafted_pids
                ]

                for idx, p in enumerate(team.players):
                    pid = int(p.player_id)
                    jersey = p.jersey_number or ((idx % 98) + 1)
                    pos_code = POSITION_TO_EA_ID.get(p.primary_position.upper(), 14)
                    role = "starter" if idx < 11 else ("sub" if idx < 18 else "reserve")

                    origin_row = player_club_row.get(pid)
                    if origin_row is not None and origin_row["teamid"] == host_id:
                        r_idx = origin_row["_row_index"]
                        self._write_row_field_in_binary(raw, tpl_schema, r_idx, "jerseynumber", jersey)
                        self._write_row_field_in_binary(raw, tpl_schema, r_idx, "position", pos_code)
                        players_transferred += 1
                    elif displaced_slots:
                        host_slot = displaced_slots.pop(0)
                        displaced_pid = host_slot["playerid"]
                        h_idx = host_slot["_row_index"]

                        # Coloca o jogador draftado no slot do clube hospedeiro
                        self._write_row_field_in_binary(raw, tpl_schema, h_idx, "playerid", pid)
                        self._write_row_field_in_binary(raw, tpl_schema, h_idx, "jerseynumber", jersey)
                        self._write_row_field_in_binary(raw, tpl_schema, h_idx, "position", pos_code)
                        players_transferred += 1

                        # Backfill: envia o jogador deslocado do clube host para o clube de origem
                        if origin_row is not None and origin_row["teamid"] != host_id:
                            o_idx = origin_row["_row_index"]
                            self._write_row_field_in_binary(
                                raw, tpl_schema, o_idx, "playerid", displaced_pid
                            )
                            origin_row["playerid"] = displaced_pid
                            player_club_row[displaced_pid] = origin_row
                            backfill_swaps += 1

                        host_slot["playerid"] = pid
                        player_club_row[pid] = host_slot

                    csv_rows.append(
                        {
                            "participant_name": team.participant_name,
                            "host_team_id": host_id,
                            "role": role,
                            "jersey_number": jersey,
                            "player_id": pid,
                            "short_name": p.short_name,
                            "primary_position": p.primary_position,
                            "overall": p.overall,
                        }
                    )
                    lua_lines.append(f"-- {team.participant_name}: {p.short_name} -> Club {host_id}")

            if tpl_schema.crc_offset is not None and tpl_schema.crc_mode is not None:
                rec_start = tpl_schema.records_data_offset
                crc_off = tpl_schema.crc_offset
                raw[crc_off : crc_off + 4] = _apply_crc_mode(
                    bytes(raw[rec_start:crc_off]), tpl_schema.crc_mode
                )
            binary_tables_patched.append("teamplayerlinks")

        if dts_schema is not None:
            bin_dts_rows = self._read_table_rows_from_binary(raw, dts_schema)
            dts_by_team = {r["teamid"]: r for r in bin_dts_rows}

            for team in request.teams:
                host_id = int(team.host_team_id)
                dts_row = dts_by_team.get(host_id)
                if dts_row is None or not team.players:
                    continue

                # Ordena titulares garantindo 1 GK no slot 0
                gks = [p for p in team.players if p.primary_position.upper() in ("GK", "GL")]
                outfield = [p for p in team.players if p.primary_position.upper() not in ("GK", "GL")]
                ordered = (gks[:1] + outfield + gks[1:])[:18]

                r_idx = dts_row["_row_index"]
                for slot_i, pl in enumerate(ordered):
                    col = f"playerid{slot_i}"
                    if col in dts_schema.fields:
                        self._write_row_field_in_binary(
                            raw, dts_schema, r_idx, col, int(pl.player_id)
                        )

            if dts_schema.crc_offset is not None and dts_schema.crc_mode is not None:
                rec_start = dts_schema.records_data_offset
                crc_off = dts_schema.crc_offset
                raw[crc_off : crc_off + 4] = _apply_crc_mode(
                    bytes(raw[rec_start:crc_off]), dts_schema.crc_mode
                )
            binary_tables_patched.append("default_teamsheets")

        loans_txt_path = self.rdbm_clean_dir / "playerloans.txt"
        if loans_txt_path.exists():
            loans_schema = self.calibrate_table_schema(
                raw=raw,
                raw_tables=raw_tables,
                table_name="playerloans",
                txt_path=loans_txt_path,
                required_columns=["playerid"],
            )
            if loans_schema is not None:
                bin_loan_rows = self._read_table_rows_from_binary(raw, loans_schema)
                for b_row in bin_loan_rows:
                    if b_row["playerid"] in all_drafted_pids:
                        r_idx = b_row["_row_index"]
                        min_val = loans_schema.fields["playerid"].range_low
                        self._write_row_field_in_binary(
                            raw, loans_schema, r_idx, "playerid", min_val
                        )
                        loans_removed += 1
                if loans_schema.crc_offset is not None and loans_schema.crc_mode is not None:
                    rec_start = loans_schema.records_data_offset
                    crc_off = loans_schema.crc_offset
                    raw[crc_off : crc_off + 4] = _apply_crc_mode(
                        bytes(raw[rec_start:crc_off]), loans_schema.crc_mode
                    )
                binary_tables_patched.append("playerloans")

        for crc_pos, p_start, p_end, mode in outer_crcs:
            raw[crc_pos : crc_pos + 4] = _apply_crc_mode(bytes(raw[p_start:p_end]), mode)

        squads_path.write_bytes(bytes(raw))

        # Salva artefatos de auditoria (.csv e .lua)
        csv_path = out_export_dir / "draft_transfers.csv"
        if csv_rows:
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
                writer.writeheader()
                writer.writerows(csv_rows)

        lua_path = out_export_dir / "apply_draft_live_editor.lua"
        lua_path.write_text("\n".join(lua_lines) + "\n", encoding="utf-8")

        return {
            "ok": True,
            "squads_file": str(squads_path),
            "backup_file": str(backup_file),
            "binary_tables_patched": binary_tables_patched,
            "teams_patched": len(request.teams),
            "players_transferred": players_transferred,
            "backfill_swaps": backfill_swaps,
            "loans_removed": loans_removed,
            "output_dir": str(out_export_dir),
            "lua_script": str(lua_path),
            "csv_summary": str(csv_path),
        }