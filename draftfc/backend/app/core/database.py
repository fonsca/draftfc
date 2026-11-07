import json
import secrets
import sqlite3
import threading
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.app.core.config import DB_PATH
from backend.app.engine.draft_lottery import LotteryReceipt
from backend.app.engine.draft_state import DraftEngineError, DraftSessionEngine, RecordedPick
from backend.app.engine.fallback_autodraft import FallbackReason, FallbackStrategy, build_sorted_pool
from backend.app.engine.models import DraftPlayer, DraftRulesConfig, DraftStatus
from backend.app.engine.pool_loader import load_player_pool_from_sqlite
from backend.app.engine.positions import PitchSector
from backend.app.engine.rules_validator import FastRosterContext, validate_candidate_pick
from backend.app.schemas.draft_schemas import (
    CreateRoomRequest,
    EditionSummary,
    FilterOptionsResponse,
    ParticipantRosterSummary,
    PlayerPoolItem,
    PlayerPoolPageResponse,
    RoomStateSnapshot,
    RoomSummaryItem,
)

ROOMS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS draft_rooms (
    room_id TEXT PRIMARY KEY,
    room_name TEXT NOT NULL,
    edition TEXT NOT NULL,
    gender TEXT NOT NULL,
    pick_clock_seconds INTEGER NOT NULL DEFAULT 60,
    turn_duration_seconds REAL NOT NULL DEFAULT 60.0,
    paused_remaining_seconds REAL,
    fallback_strategy TEXT NOT NULL DEFAULT 'smart_balanced',
    rules_json TEXT NOT NULL,
    ordered_participants_json TEXT NOT NULL,
    lottery_receipt_json TEXT NOT NULL,
    status TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 1,
    turn_started_at_iso TEXT NOT NULL,
    user_queues_json TEXT NOT NULL,
    auto_pick_users_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_picks (
    room_id TEXT NOT NULL,
    overall_pick INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    pick_in_round INTEGER NOT NULL,
    participant_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    player_short_name TEXT NOT NULL,
    club_id INTEGER,
    club_name TEXT NOT NULL,
    league_name TEXT NOT NULL,
    overall INTEGER NOT NULL,
    primary_position TEXT NOT NULL,
    sector TEXT NOT NULL,
    made_by_commissioner INTEGER NOT NULL DEFAULT 0,
    auto_drafted INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    PRIMARY KEY (room_id, overall_pick)
);
"""


def strip_accents(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in norm if not unicodedata.combining(ch)).lower()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CachedPoolBundle:
    def __init__(self, pool: Dict[int, DraftPlayer]) -> None:
        self.pool = pool
        self.sorted_players = build_sorted_pool(pool)
        self.search_blobs: Dict[int, str] = {
            pid: strip_accents(
                f"{p.short_name} {p.full_name} {p.club_name} {p.nationality_name} {p.league_name}"
            )
            for pid, p in pool.items()
        }
        self.filter_options = FilterOptionsResponse(
            leagues=sorted({p.league_name for p in pool.values() if p.league_name}),
            clubs=sorted({p.club_name for p in pool.values() if p.club_name}),
            nationalities=sorted({p.nationality_name for p in pool.values() if p.nationality_name}),
            positions=sorted({p.primary_position for p in pool.values()}),
        )


class ManagedRoom:
    def __init__(
        self,
        room_name: str,
        gender: str,
        pick_clock_seconds: int,
        turn_duration_seconds: float,
        paused_remaining_seconds: Optional[float],
        turn_started_at_iso: str,
        engine: DraftSessionEngine,
    ) -> None:
        self.room_name = room_name
        self.gender = gender
        self.pick_clock_seconds = pick_clock_seconds
        self.turn_duration_seconds = turn_duration_seconds
        self.paused_remaining_seconds = paused_remaining_seconds
        self.turn_started_at_iso = turn_started_at_iso
        self.engine = engine

    def reset_turn_clock(self) -> None:
        self.turn_started_at_iso = utc_now_iso()
        self.turn_duration_seconds = float(self.pick_clock_seconds)
        if self.engine.status == DraftStatus.PAUSED and self.pick_clock_seconds > 0:
            self.paused_remaining_seconds = float(self.pick_clock_seconds)
        else:
            self.paused_remaining_seconds = None

    def freeze_clock_on_pause(self) -> None:
        if self.pick_clock_seconds > 0 and self.paused_remaining_seconds is None:
            started = datetime.fromisoformat(self.turn_started_at_iso)
            elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
            self.paused_remaining_seconds = round(max(1.0, self.turn_duration_seconds - elapsed), 1)
        self.engine.pause()

    def unfreeze_clock_on_resume(self) -> None:
        if self.pick_clock_seconds > 0 and self.paused_remaining_seconds is not None:
            self.turn_duration_seconds = self.paused_remaining_seconds
        self.paused_remaining_seconds = None
        self.turn_started_at_iso = utc_now_iso()
        self.engine.resume()


class LocalDraftStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._pools: Dict[Tuple[str, str], CachedPoolBundle] = {}
        self._rooms: Dict[str, ManagedRoom] = {}
        if self.db_path.exists():
            self._init_rooms_tables()

    def _init_rooms_tables(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(ROOMS_SCHEMA_SQL)

    def get_pool_bundle(self, edition: str, gender: str = "male") -> CachedPoolBundle:
        key = (edition, gender)
        with self._lock:
            if key not in self._pools:
                raw_pool = load_player_pool_from_sqlite(self.db_path, edition=edition, gender=gender)
                if not raw_pool:
                    raise DraftEngineError(
                        f"Nenhum jogador encontrado no SQLite para edition='{edition}', gender='{gender}'."
                    )
                self._pools[key] = CachedPoolBundle(raw_pool)
            return self._pools[key]

    def list_editions(self) -> List[EditionSummary]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            has_players = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='players'"
            ).fetchone()
            if not has_players:
                return []
            rows = conn.execute(
                """
                SELECT edition, gender, COUNT(*) as p_cnt
                FROM players
                GROUP BY edition, gender
                ORDER BY edition DESC, gender ASC
                """
            ).fetchall()
            summaries: List[EditionSummary] = []
            for r in rows:
                c_cnt = conn.execute(
                    "SELECT COUNT(*) FROM clubs WHERE edition = ?", (r["edition"],)
                ).fetchone()[0]
                summaries.append(
                    EditionSummary(
                        edition=r["edition"],
                        gender=r["gender"],
                        players_count=r["p_cnt"],
                        clubs_count=c_cnt,
                    )
                )
            return summaries

    def create_room(self, req: CreateRoomRequest) -> ManagedRoom:
        with self._lock:
            self._init_rooms_tables()
            bundle = self.get_pool_bundle(req.edition, req.gender)
            room_id = secrets.token_hex(4)
            now = utc_now_iso()

            engine = DraftSessionEngine(
                room_id=room_id,
                edition=req.edition,
                rules=req.rules,
                player_pool=bundle.pool,
                participants=req.participants,
                lottery_seed=req.lottery_seed,
                manual_order=req.participants if req.use_manual_order else None,
                fallback_strategy=req.fallback_strategy,
            )
            managed = ManagedRoom(
                room_name=req.room_name.strip(),
                gender=req.gender,
                pick_clock_seconds=req.pick_clock_seconds,
                turn_duration_seconds=float(req.pick_clock_seconds),
                paused_remaining_seconds=None,
                turn_started_at_iso=now,
                engine=engine,
            )
            self._rooms[room_id] = managed
            self.persist_room(managed, is_new=True)
            return managed

    def persist_room(self, managed: ManagedRoom, is_new: bool = False) -> None:
        with self._lock:
            self._init_rooms_tables()
            eng = managed.engine
            now = utc_now_iso()

            payload = {
                "room_id": eng.room_id,
                "room_name": managed.room_name,
                "edition": eng.edition,
                "gender": managed.gender,
                "pick_clock_seconds": managed.pick_clock_seconds,
                "turn_duration_seconds": managed.turn_duration_seconds,
                "paused_remaining_seconds": managed.paused_remaining_seconds,
                "fallback_strategy": eng.fallback_strategy.value,
                "rules_json": eng.rules.model_dump_json(),
                "ordered_participants_json": json.dumps(eng.ordered_participants, ensure_ascii=False),
                "lottery_receipt_json": eng.lottery_receipt.model_dump_json(),
                "status": eng.status.value,
                "state_version": eng.state_version,
                "turn_started_at_iso": managed.turn_started_at_iso,
                "user_queues_json": json.dumps(eng._user_queues, ensure_ascii=False),
                "auto_pick_users_json": json.dumps(sorted(eng._auto_pick_participants), ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            }

            with sqlite3.connect(self.db_path) as conn:
                if is_new:
                    conn.execute(
                        """
                        INSERT INTO draft_rooms (
                            room_id, room_name, edition, gender, pick_clock_seconds,
                            turn_duration_seconds, paused_remaining_seconds, fallback_strategy,
                            rules_json, ordered_participants_json, lottery_receipt_json, status,
                            state_version, turn_started_at_iso, user_queues_json, auto_pick_users_json,
                            created_at, updated_at
                        ) VALUES (
                            :room_id, :room_name, :edition, :gender, :pick_clock_seconds,
                            :turn_duration_seconds, :paused_remaining_seconds, :fallback_strategy,
                            :rules_json, :ordered_participants_json, :lottery_receipt_json, :status,
                            :state_version, :turn_started_at_iso, :user_queues_json, :auto_pick_users_json,
                            :created_at, :updated_at
                        )
                        """,
                        payload,
                    )
                else:
                    conn.execute(
                        """
                        UPDATE draft_rooms SET
                            turn_duration_seconds = :turn_duration_seconds,
                            paused_remaining_seconds = :paused_remaining_seconds,
                            status = :status,
                            state_version = :state_version,
                            turn_started_at_iso = :turn_started_at_iso,
                            user_queues_json = :user_queues_json,
                            auto_pick_users_json = :auto_pick_users_json,
                            updated_at = :updated_at
                        WHERE room_id = :room_id
                        """,
                        payload,
                    )

                conn.execute("DELETE FROM draft_picks WHERE room_id = ?", (eng.room_id,))
                if eng.picks_history:
                    rows = []
                    for pk in eng.picks_history:
                        pl = eng.player_pool[pk.player_id]
                        rows.append(
                            (
                                eng.room_id,
                                pk.overall_pick,
                                pk.round_number,
                                pk.pick_in_round,
                                pk.participant_id,
                                pk.player_id,
                                pk.player_short_name,
                                pl.club_id,
                                pk.club_name,
                                pk.league_name,
                                pk.overall,
                                pl.primary_position,
                                pk.sector.value,
                                1 if pk.made_by_commissioner else 0,
                                1 if pk.auto_drafted else 0,
                                pk.fallback_reason.value if pk.fallback_reason else None,
                            )
                        )
                    conn.executemany(
                        """
                        INSERT INTO draft_picks (
                            room_id, overall_pick, round_number, pick_in_round, participant_id,
                            player_id, player_short_name, club_id, club_name, league_name,
                            overall, primary_position, sector, made_by_commissioner,
                            auto_drafted, fallback_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )

    def get_room(self, room_id: str) -> ManagedRoom:
        with self._lock:
            if room_id in self._rooms:
                return self._rooms[room_id]
            managed = self._rehydrate_room_from_sqlite(room_id)
            if managed is None:
                raise KeyError(f"Sala de draft '{room_id}' não encontrada.")
            self._rooms[room_id] = managed
            return managed

    def _rehydrate_room_from_sqlite(self, room_id: str) -> Optional[ManagedRoom]:
        if not self.db_path.exists():
            return None
        self._init_rooms_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            r_row = conn.execute("SELECT * FROM draft_rooms WHERE room_id = ?", (room_id,)).fetchone()
            if not r_row:
                return None
            p_rows = conn.execute(
                "SELECT * FROM draft_picks WHERE room_id = ? ORDER BY overall_pick ASC",
                (room_id,),
            ).fetchall()

        bundle = self.get_pool_bundle(r_row["edition"], r_row["gender"])
        rules = DraftRulesConfig.model_validate_json(r_row["rules_json"])
        ordered_participants = json.loads(r_row["ordered_participants_json"])
        receipt = LotteryReceipt.model_validate_json(r_row["lottery_receipt_json"])

        eng = DraftSessionEngine(
            room_id=room_id,
            edition=r_row["edition"],
            rules=rules,
            player_pool=bundle.pool,
            participants=ordered_participants,
            manual_order=ordered_participants,
            fallback_strategy=FallbackStrategy(r_row["fallback_strategy"]),
        )
        eng.lottery_receipt = receipt
        eng.status = DraftStatus(r_row["status"])
        eng.state_version = r_row["state_version"]
        eng._user_queues = json.loads(r_row["user_queues_json"])
        eng._auto_pick_participants = set(json.loads(r_row["auto_pick_users_json"]))

        for pr in p_rows:
            pid = pr["player_id"]
            uid = pr["participant_id"]
            if pid in bundle.pool:
                eng.rosters[uid].append(bundle.pool[pid])
                eng.picked_player_ids.add(pid)
            eng.picks_history.append(
                RecordedPick(
                    overall_pick=pr["overall_pick"],
                    round_number=pr["round_number"],
                    pick_in_round=pr["pick_in_round"],
                    participant_id=uid,
                    player_id=pid,
                    player_short_name=pr["player_short_name"],
                    club_name=pr["club_name"],
                    league_name=pr["league_name"],
                    overall=pr["overall"],
                    sector=PitchSector(pr["sector"]),
                    made_by_commissioner=bool(pr["made_by_commissioner"]),
                    auto_drafted=bool(pr["auto_drafted"]),
                    fallback_reason=FallbackReason(pr["fallback_reason"]) if pr["fallback_reason"] else None,
                )
            )
            eng._queue_undo_snapshots.append({})

        return ManagedRoom(
            room_name=r_row["room_name"],
            gender=r_row["gender"],
            pick_clock_seconds=r_row["pick_clock_seconds"],
            turn_duration_seconds=r_row["turn_duration_seconds"],
            paused_remaining_seconds=r_row["paused_remaining_seconds"],
            turn_started_at_iso=r_row["turn_started_at_iso"],
            engine=eng,
        )

    def list_rooms(self) -> List[RoomSummaryItem]:
        if not self.db_path.exists():
            return []
        self._init_rooms_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM draft_rooms ORDER BY updated_at DESC").fetchall()
            items: List[RoomSummaryItem] = []
            for r in rows:
                picks_cnt = conn.execute(
                    "SELECT COUNT(*) FROM draft_picks WHERE room_id = ?", (r["room_id"],)
                ).fetchone()[0]
                rules = DraftRulesConfig.model_validate_json(r["rules_json"])
                parts = json.loads(r["ordered_participants_json"])
                items.append(
                    RoomSummaryItem(
                        room_id=r["room_id"],
                        room_name=r["room_name"],
                        edition=r["edition"],
                        gender=r["gender"],
                        status=DraftStatus(r["status"]),
                        picks_made=picks_cnt,
                        total_picks=len(parts) * rules.roster_size,
                        updated_at=r["updated_at"],
                    )
                )
            return items

    def build_room_snapshot(self, managed: ManagedRoom) -> RoomStateSnapshot:
        eng = managed.engine
        with eng._lock:
            idx = len(eng.picks_history)
            upcoming = eng.pick_schedule[idx : idx + 12]
            rosters_out: Dict[str, ParticipantRosterSummary] = {}
            for uid in eng.ordered_participants:
                r_list = eng.rosters[uid]
                sec_cnt = {s: 0 for s in PitchSector}
                for p in r_list:
                    sec_cnt[p.sector] += 1
                rosters_out[uid] = ParticipantRosterSummary(
                    participant_id=uid,
                    auto_pick_enabled=eng.is_auto_pick_enabled(uid),
                    players=r_list,
                    sector_counts=sec_cnt,
                    club_counts=dict(Counter(p.club_name for p in r_list if not p.is_free_agent)),
                    league_counts=dict(Counter(p.league_name for p in r_list if not p.is_free_agent)),
                    nationality_counts=dict(Counter(p.nationality_name for p in r_list)),
                    spent_budget_eur=sum(p.value_eur for p in r_list),
                    queue=eng.inspect_participant_queue(uid),
                )

            return RoomStateSnapshot(
                room_id=eng.room_id,
                room_name=managed.room_name,
                edition=eng.edition,
                gender=managed.gender,
                pick_clock_seconds=managed.pick_clock_seconds,
                turn_duration_seconds=managed.turn_duration_seconds,
                paused_remaining_seconds=managed.paused_remaining_seconds,
                fallback_strategy=eng.fallback_strategy,
                status=eng.status,
                state_version=eng.state_version,
                turn_started_at_iso=managed.turn_started_at_iso,
                rules=eng.rules,
                lottery_receipt=eng.lottery_receipt,
                ordered_participants=eng.ordered_participants,
                current_slot=eng.current_slot,
                upcoming_slots=upcoming,
                total_picks=len(eng.pick_schedule),
                picks_history=list(eng.picks_history),
                rosters=rosters_out,
            )

    def query_room_players(
        self,
        managed: ManagedRoom,
        q: Optional[str] = None,
        sector: Optional[PitchSector] = None,
        position: Optional[str] = None,
        league_name: Optional[str] = None,
        club_name: Optional[str] = None,
        nationality_name: Optional[str] = None,
        min_ovr: Optional[int] = None,
        max_ovr: Optional[int] = None,
        hide_picked: bool = True,
        only_eligible: bool = False,
        for_participant: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> PlayerPoolPageResponse:
        eng = managed.engine
        bundle = self.get_pool_bundle(eng.edition, managed.gender)
        norm_q = strip_accents(q.strip()) if q and q.strip() else None
        norm_pos = position.strip().upper() if position and position.strip() else None

        with eng._lock:
            eval_uid = for_participant or (
                eng.current_slot.participant_id if eng.current_slot else eng.ordered_participants[0]
            )
            if eval_uid not in eng.rosters:
                eval_uid = eng.ordered_participants[0]
            active_roster = eng.rosters[eval_uid]
            picked_ids = eng.picked_player_ids
            fast_ctx = FastRosterContext(active_roster, picked_ids, eng.rules)

            matched: List[DraftPlayer] = []
            for p in bundle.sorted_players:
                if hide_picked and p.player_id in picked_ids:
                    continue
                if sector is not None and p.sector != sector:
                    continue
                if norm_pos is not None and norm_pos not in p.positions:
                    continue
                if min_ovr is not None and p.overall < min_ovr:
                    continue
                if max_ovr is not None and p.overall > max_ovr:
                    continue
                if league_name and p.league_name != league_name:
                    continue
                if club_name and p.club_name != club_name:
                    continue
                if nationality_name and p.nationality_name != nationality_name:
                    continue
                if norm_q and norm_q not in bundle.search_blobs[p.player_id]:
                    continue
                if only_eligible and not fast_ctx.is_eligible(p):
                    continue
                matched.append(p)

            page_slice = matched[offset : offset + limit]
            items = [
                PlayerPoolItem(
                    player=p,
                    is_picked=p.player_id in picked_ids,
                    eligible_for_active=(val := validate_candidate_pick(p, active_roster, picked_ids, eng.rules)).eligible,
                    violations=val.violations,
                )
                for p in page_slice
            ]

            return PlayerPoolPageResponse(
                total_matching=len(matched),
                offset=offset,
                limit=limit,
                evaluated_for_participant=eval_uid,
                items=items,
            )


draft_store = LocalDraftStore()