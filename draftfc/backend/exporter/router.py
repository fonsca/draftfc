from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.exporter.models import DraftExportPayload, PatchRequest, PatchSummary
from backend.exporter.rdbm_patcher import patch_rdbm_directory, write_auxiliary_exports

router = APIRouter(tags=["Phase 5 - PC Export"])
DEFAULT_DB = Path("data/draftfc_local.db")


def _load_known_clubs(edition: str) -> set[int]:
    if not DEFAULT_DB.exists():
        return set()
    with sqlite3.connect(DEFAULT_DB) as conn:
        rows = conn.execute(
            "SELECT club_id FROM clubs WHERE edition = ?", (edition,)
        ).fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


@router.post("/bundle")
def generate_export_bundle(payload: DraftExportPayload) -> dict[str, str]:
    slug = payload.league_name.lower().replace(" ", "_")
    out_dir = Path("data/exports") / slug
    csv_path, lua_path = write_auxiliary_exports(payload, out_dir)
    return {
        "csv_path": str(csv_path),
        "lua_path": str(lua_path),
        "output_dir": str(out_dir),
    }


@router.post("/patch-rdbm", response_model=PatchSummary)
def patch_rdbm_endpoint(req: PatchRequest) -> PatchSummary:
    try:
        known_clubs = _load_known_clubs(req.payload.edition)
        return patch_rdbm_directory(
            payload=req.payload,
            rdbm_input_dir=Path(req.rdbm_input_dir),
            output_dir=Path(req.output_dir),
            known_club_ids=known_clubs or None,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc