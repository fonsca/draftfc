from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import random
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from backend.multiplayer.models import (
    CreateRoomRequest,
    DraftPickRecord,
    JoinRoomRequest,
    ParticipantState,
    PublicRoomState,
    RoomConfig,
    RoomPlayer,
    RoomSummary,
    SectorType,
)

GK_CODES = {"GK", "GL", "GOL", "GR", "POR", "G", "GOLEIRO", "TW"}
DEF_CODES = {
    "CB", "LCB", "RCB", "ZAG", "DC", "DFC",
    "LB", "RB", "LE", "LD", "DE", "DD", "LI",
    "LWB", "RWB", "ADE", "ADD", "ALA", "AL", "SW", "DEF",
}
MID_CODES = {
    "CDM", "LDM", "RDM", "VOL", "MDC", "MCD",
    "CM", "LCM", "RCM", "MC",
    "CAM", "LAM", "RAM", "MEI", "MO", "MCO",
    "LM", "RM", "ME", "MD", "MID", "WM",
}
ATT_CODES = {
    "ST", "LS", "RS", "ATA", "CA", "PL",
    "CF", "LF", "RF", "SA", "MP", "SD",
    "LW", "RW", "PE", "PD", "MAE", "MAD", "PTE", "PTD", "EXI", "EXD", "ATT", "FWD",
}


def normalize_position_and_sector(raw_pos: str, fallback_positions: str = "") -> tuple[str, SectorType]:
    """
    Extrai a posição primária limpa (PT-BR ou EN) e retorna (posicao, setor).
    Evita que jogadores em português (GOL, ZAG, MC, etc.) caiam todos como ATT.
    """
    combined = f"{raw_pos or ''} {fallback_positions or ''}".upper()
    for ch in (",", "/", ";", "|", "-"):
        combined = combined.replace(ch, " ")
    tokens = [t.strip() for t in combined.split() if t.strip() and t.strip() not in ("SUB", "RES", "UNK")]

    for tok in tokens:
        if tok in GK_CODES:
            return tok, "GK"
        if tok in DEF_CODES:
            return tok, "DEF"
        if tok in MID_CODES:
            return tok, "MID"
        if tok in ATT_CODES:
            return tok, "ATT"

    first = tokens[0] if tokens else "CM"
    return first, "MID"


def build_snake_schedule(num_teams: int, rounds: int) -> list[tuple[int, int, int, int]]:
    schedule: list[tuple[int, int, int, int]] = []
    overall = 1
    for rnd in range(1, rounds + 1):
        seats = list(range(1, num_teams + 1)) if rnd % 2 == 1 else list(range(num_teams, 0, -1))
        for idx, seat in enumerate(seats, start=1):
            schedule.append((overall, rnd, idx, seat))
            overall += 1
    return schedule


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class DraftRoom:
    def __init__(
        self,
        room_code: str,
        config: RoomConfig,
        players_pool: list[RoomPlayer],
        commissioner_name: str,
    ) -> None:
        self.room_code = room_code
        self.config = config
        self.password_hash = _hash_password(config.password)
        self.status: str = "lobby"
        self.lock = asyncio.Lock()

        # Garante que todos os atletas do pool tenham posição e setor normalizados
        normalized_pool: list[RoomPlayer] = []
        for p in players_pool:
            pos, sec = normalize_position_and_sector(p.primary_position)
            normalized_pool.append(p.model_copy(update={"primary_position": pos, "sector": sec}))

        self.players_by_id: dict[int, RoomPlayer] = {p.player_id: p for p in normalized_pool}
        self.sorted_player_ids: list[int] = [
            p.player_id
            for p in sorted(normalized_pool, key=lambda x: (-x.overall, -x.value_eur, x.player_id))
        ]
        self.drafted_player_ids: set[int] = set()
        self.picks_history: list[DraftPickRecord] = []

        self.schedule = build_snake_schedule(config.num_teams, config.rounds)
        self.current_pick_index: int = 0
        self.remaining_seconds: int = config.pick_clock_seconds

        self.participants: dict[int, ParticipantState] = {
            seat: ParticipantState(
                seat_number=seat,
                display_name=commissioner_name if seat == 1 else f"Time {seat}",
                claimed=(seat == 1),
            )
            for seat in range(1, config.num_teams + 1)
        }
        self.commissioner_token: str = secrets.token_urlsafe(16)
        self.seat_tokens: dict[int, str] = {1: self.commissioner_token}
        self.token_to_seat: dict[str, int] = {self.commissioner_token: 1}

        self.connections: dict[WebSocket, int] = {}
        self.clock_task: asyncio.Task[None] | None = None
        self.auto_clock_enabled: bool = False

    def ensure_clock_running(self) -> None:
        """Garante que a task do relógio esteja rodando sempre que a sala estiver ativa."""
        if self.auto_clock_enabled and (self.clock_task is None or self.clock_task.done()):
            self.clock_task = asyncio.create_task(self._room_clock_loop())

    async def _room_clock_loop(self) -> None:
        try:
            while self.status in ("active", "paused"):
                await asyncio.sleep(1.0)
                if self.status == "active":
                    try:
                        await self.step_clock(1)
                    except Exception as exc:
                        print(f"[AVISO] Erro no tick do relógio ({self.room_code}): {exc}")
        except asyncio.CancelledError:
            return

    @property
    def current_slot(self) -> tuple[int, int, int, int] | None:
        if 0 <= self.current_pick_index < len(self.schedule):
            return self.schedule[self.current_pick_index]
        return None

    def summary(self) -> RoomSummary:
        slot = self.current_slot
        return RoomSummary(
            room_code=self.room_code,
            room_name=self.config.room_name,
            edition=self.config.edition,
            status=self.status,  # type: ignore[arg-type]
            num_teams=self.config.num_teams,
            claimed_seats=sum(1 for p in self.participants.values() if p.claimed),
            online_seats=sum(1 for p in self.participants.values() if p.is_online),
            rounds=self.config.rounds,
            current_round=slot[1] if slot else self.config.rounds,
            current_pick_index=self.current_pick_index,
            total_picks=len(self.schedule),
            has_password=bool(self.config.password),
        )

    def public_state(self) -> PublicRoomState:
        slot = self.current_slot
        return PublicRoomState(
            room_code=self.room_code,
            room_name=self.config.room_name,
            edition=self.config.edition,
            status=self.status,  # type: ignore[arg-type]
            num_teams=self.config.num_teams,
            rounds=self.config.rounds,
            pick_clock_seconds=self.config.pick_clock_seconds,
            remaining_seconds=self.remaining_seconds,
            current_pick_index=self.current_pick_index,
            total_picks=len(self.schedule),
            current_round=slot[1] if slot else self.config.rounds,
            current_seat=slot[3] if slot else None,
            snake_order_preview=[s[3] for s in self.schedule],
            participants=[self.participants[s] for s in range(1, self.config.num_teams + 1)],
            picks_history=self.picks_history,
            drafted_player_ids=sorted(self.drafted_player_ids),
            available_players_count=len(self.players_by_id) - len(self.drafted_player_ids),
        )

    def verify_password(self, password: str) -> bool:
        if not self.config.password:
            return True
        return _hash_password(password) == self.password_hash

    def _validate_limits(self, seat_number: int, player: RoomPlayer) -> str | None:
        roster = self.participants[seat_number].roster
        if self.config.max_per_club is not None and player.club_name != "Free Agent":
            club_count = sum(1 for p in roster if p.club_name == player.club_name)
            if club_count >= self.config.max_per_club:
                return f"Limite de {self.config.max_per_club} jogador(es) do clube {player.club_name} atingido."

        if self.config.max_per_league is not None and player.league_name != "Unknown League":
            league_count = sum(1 for p in roster if p.league_name == player.league_name)
            if league_count >= self.config.max_per_league:
                return f"Limite de {self.config.max_per_league} jogador(es) da liga {player.league_name} atingido."

        if self.config.max_per_nationality is not None:
            nat_count = sum(1 for p in roster if p.nationality == player.nationality)
            if nat_count >= self.config.max_per_nationality:
                return f"Limite de {self.config.max_per_nationality} jogador(es) da nacionalidade {player.nationality} atingido."

        return None

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for ws in list(self.connections.keys()):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.connections.pop(ws, None)

    async def randomize_order(self, commissioner_token: str) -> tuple[bool, dict[str, Any]]:
        """
        Sorteia e aleatoriza a ordem dos assentos (1..N) antes do início das escolhas.
        Preserva os tokens e conexões de cada participante no seu novo assento sorteado.
        """
        async with self.lock:
            if commissioner_token != self.commissioner_token:
                return False, {
                    "event": "pick_error",
                    "code": "FORBIDDEN",
                    "message": "Apenas o Comissário pode sortear a ordem do draft.",
                }
            if len(self.picks_history) > 0:
                return False, {
                    "event": "pick_error",
                    "code": "DRAFT_ALREADY_STARTED",
                    "message": "Volte as escolhas feitas antes de sortear uma nova ordem.",
                }

            old_seats = list(range(1, self.config.num_teams + 1))
            shuffled_seats = old_seats.copy()
            if len(shuffled_seats) > 1:
                for _ in range(5):
                    random.shuffle(shuffled_seats)
                    if shuffled_seats != old_seats:
                        break

            new_participants: dict[int, ParticipantState] = {}
            new_seat_tokens: dict[int, str] = {}
            new_token_to_seat: dict[str, int] = {}
            old_to_new_seat: dict[int, int] = {}

            for new_seat, old_seat in enumerate(shuffled_seats, start=1):
                old_to_new_seat[old_seat] = new_seat
                pt = self.participants[old_seat]
                new_participants[new_seat] = ParticipantState(
                    seat_number=new_seat,
                    display_name=pt.display_name if pt.claimed else f"Time {new_seat}",
                    claimed=pt.claimed,
                    is_online=pt.is_online,
                    roster=list(pt.roster),
                    queue=list(pt.queue),
                )
                tok = self.seat_tokens.get(old_seat)
                if tok:
                    new_seat_tokens[new_seat] = tok
                    new_token_to_seat[tok] = new_seat

            self.participants = new_participants
            self.seat_tokens = new_seat_tokens
            self.token_to_seat = new_token_to_seat

            for ws, old_s in list(self.connections.items()):
                if old_s in old_to_new_seat:
                    self.connections[ws] = old_to_new_seat[old_s]

            self.remaining_seconds = self.config.pick_clock_seconds
            state_dump = self.public_state().model_dump()

        # Envia para cada conexão o seu novo seat_number atualizado + estado da sala
        for ws, updated_seat in list(self.connections.items()):
            try:
                await ws.send_json(
                    {
                        "event": "order_randomized",
                        "room_code": self.room_code,
                        "seat_number": updated_seat,
                        "state": state_dump,
                    }
                )
            except Exception:
                pass

        return True, {"event": "order_randomized", "room_code": self.room_code, "state": state_dump}

    async def undo_last_pick(self, commissioner_token: str) -> tuple[bool, dict[str, Any]]:
        """
        Poder de Comissário: desfaz a última escolha realizada, devolve o jogador ao pool
        e retorna o turno e o relógio para o assento anterior.
        """
        async with self.lock:
            if commissioner_token != self.commissioner_token:
                return False, {
                    "event": "pick_error",
                    "code": "FORBIDDEN",
                    "message": "Apenas o Comissário pode desfazer escolhas.",
                }
            if not self.picks_history:
                return False, {
                    "event": "pick_error",
                    "code": "NO_PICKS_TO_UNDO",
                    "message": "Nenhuma escolha foi realizada ainda para desfazer.",
                }

            last_record = self.picks_history.pop()
            pid = last_record.player.player_id
            self.drafted_player_ids.discard(pid)

            participant = self.participants.get(last_record.seat_number)
            if participant:
                participant.roster = [p for p in participant.roster if p.player_id != pid]

            self.current_pick_index = max(0, self.current_pick_index - 1)
            self.remaining_seconds = self.config.pick_clock_seconds
            if self.status == "completed":
                self.status = "active"

            payload = {
                "event": "pick_undone",
                "room_code": self.room_code,
                "undone_pick": last_record.model_dump(),
                "state": self.public_state().model_dump(),
            }

        await self.broadcast(payload)
        return True, payload

    async def toggle_pause(self, commissioner_token: str) -> tuple[bool, dict[str, Any]]:
        """
        Poder de Comissário: pausa ou retoma a contagem regressiva do relógio.
        """
        async with self.lock:
            if commissioner_token != self.commissioner_token:
                return False, {
                    "event": "pick_error",
                    "code": "FORBIDDEN",
                    "message": "Apenas o Comissário pode pausar ou retomar o draft.",
                }
            if self.status == "active":
                self.status = "paused"
            elif self.status == "paused":
                self.status = "active"
            else:
                return False, {
                    "event": "pick_error",
                    "code": "INVALID_STATE",
                    "message": f"Não é possível pausar/retomar no status '{self.status}'.",
                }
            payload = {
                "event": "room_state",
                "room_code": self.room_code,
                "state": self.public_state().model_dump(),
            }

        await self.broadcast(payload)
        return True, payload

    async def make_pick(
        self,
        seat_token: str,
        player_id: int,
        request_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Executa uma escolha protegida por asyncio.Lock.
        Permite tanto a escolha do dono do turno quanto a escolha forçada pelo Comissário
        em nome do manager atual.
        """
        async with self.lock:
            if self.status not in ("active", "paused"):
                return False, {
                    "event": "pick_error",
                    "code": "DRAFT_NOT_ACTIVE",
                    "message": f"O draft não está ativo (status atual: {self.status}).",
                    "request_id": request_id,
                }

            slot = self.current_slot
            if slot is None:
                return False, {
                    "event": "pick_error",
                    "code": "DRAFT_FINISHED",
                    "message": "Todas as escolhas do draft já foram concluídas.",
                    "request_id": request_id,
                }

            overall_pick, rnd, pick_in_rnd, active_seat = slot
            caller_seat = self.token_to_seat.get(seat_token)
            is_commissioner = seat_token == self.commissioner_token

            if self.status == "paused" and not is_commissioner:
                return False, {
                    "event": "pick_error",
                    "code": "DRAFT_PAUSED",
                    "message": "O draft está pausado pelo Comissário.",
                    "request_id": request_id,
                }

            if caller_seat != active_seat and not is_commissioner:
                return False, {
                    "event": "pick_error",
                    "code": "OUT_OF_TURN",
                    "message": f"Não é a sua vez. Turno atual: Assento #{active_seat}.",
                    "active_seat": active_seat,
                    "request_id": request_id,
                }

            if player_id in self.drafted_player_ids:
                return False, {
                    "event": "pick_error",
                    "code": "PLAYER_ALREADY_DRAFTED",
                    "message": f"O jogador {player_id} já foi escolhido.",
                    "player_id": player_id,
                    "request_id": request_id,
                }

            player = self.players_by_id.get(player_id)
            if player is None:
                return False, {
                    "event": "pick_error",
                    "code": "PLAYER_NOT_FOUND",
                    "message": f"Jogador {player_id} não existe no pool da sala.",
                    "player_id": player_id,
                    "request_id": request_id,
                }

            rule_violation = self._validate_limits(active_seat, player)
            if rule_violation:
                return False, {
                    "event": "pick_error",
                    "code": "RULE_VIOLATION",
                    "message": rule_violation,
                    "player_id": player_id,
                    "request_id": request_id,
                }

            pick_source = "commissioner" if (is_commissioner and caller_seat != active_seat) else "manual"
            record = self._commit_pick_unlocked(
                overall_pick=overall_pick,
                rnd=rnd,
                pick_in_rnd=pick_in_rnd,
                seat_number=active_seat,
                player=player,
                source=pick_source,
            )

            next_slot = self.current_slot
            event_payload = {
                "event": "player_drafted",
                "room_code": self.room_code,
                "pick": record.model_dump(),
                "remaining_seconds": self.remaining_seconds,
                "next_overall_pick": next_slot[0] if next_slot else None,
                "next_round": next_slot[1] if next_slot else None,
                "next_seat": next_slot[3] if next_slot else None,
                "status": self.status,
                "state": self.public_state().model_dump(),
                "request_id": request_id,
            }

        await self.broadcast(event_payload)
        return True, event_payload

    def _commit_pick_unlocked(
        self,
        overall_pick: int,
        rnd: int,
        pick_in_rnd: int,
        seat_number: int,
        player: RoomPlayer,
        source: str,
    ) -> DraftPickRecord:
        self.drafted_player_ids.add(player.player_id)
        participant = self.participants[seat_number]
        participant.roster.append(player)

        for p in self.participants.values():
            if player.player_id in p.queue:
                p.queue.remove(player.player_id)

        record = DraftPickRecord(
            overall_pick=overall_pick,
            round_number=rnd,
            pick_in_round=pick_in_rnd,
            seat_number=seat_number,
            participant_name=participant.display_name,
            player=player,
            source=source,  # type: ignore[arg-type]
            timestamp_ms=int(time.time() * 1000),
        )
        self.picks_history.append(record)
        self.current_pick_index += 1
        self.remaining_seconds = self.config.pick_clock_seconds

        if self.current_pick_index >= len(self.schedule):
            self.status = "completed"

        return record

    def _select_autodraft_candidate_unlocked(self, seat_number: int) -> tuple[RoomPlayer, str]:
        participant = self.participants[seat_number]
        for q_pid in list(participant.queue):
            if q_pid not in self.drafted_player_ids and q_pid in self.players_by_id:
                candidate = self.players_by_id[q_pid]
                if self._validate_limits(seat_number, candidate) is None:
                    return candidate, "autodraft_queue"

        for pid in self.sorted_player_ids:
            if pid not in self.drafted_player_ids:
                candidate = self.players_by_id[pid]
                if self._validate_limits(seat_number, candidate) is None:
                    return candidate, "autodraft_best"

        raise RuntimeError("Não há jogadores válidos restantes para Auto-Draft.")

    def _is_commissioner(self, token: str) -> bool:
        return bool(token) and (
            token == self.commissioner_token or self.token_to_seat.get(token) == 1
        )

    async def randomize_order(self, commissioner_token: str) -> tuple[bool, dict[str, Any]]:
        async with self.lock:
            if not self._is_commissioner(commissioner_token):
                return False, {
                    "event": "pick_error",
                    "code": "FORBIDDEN",
                    "message": "Apenas o Comissário pode sortear a ordem do draft.",
                }
            if len(self.picks_history) > 0:
                return False, {
                    "event": "pick_error",
                    "code": "DRAFT_ALREADY_STARTED",
                    "message": "Volte as escolhas feitas antes de sortear uma nova ordem.",
                }

            old_seats = list(range(1, self.config.num_teams + 1))
            shuffled_seats = old_seats.copy()
            if len(shuffled_seats) > 1:
                for _ in range(5):
                    random.shuffle(shuffled_seats)
                    if shuffled_seats != old_seats:
                        break

            new_participants: dict[int, ParticipantState] = {}
            new_seat_tokens: dict[int, str] = {}
            new_token_to_seat: dict[str, int] = {}
            old_to_new_seat: dict[int, int] = {}

            for new_seat, old_seat in enumerate(shuffled_seats, start=1):
                old_to_new_seat[old_seat] = new_seat
                pt = self.participants[old_seat]
                new_participants[new_seat] = ParticipantState(
                    seat_number=new_seat,
                    display_name=pt.display_name if pt.claimed else f"Time {new_seat}",
                    claimed=pt.claimed,
                    is_online=pt.is_online,
                    roster=list(pt.roster),
                    queue=list(pt.queue),
                )
                tok = self.seat_tokens.get(old_seat)
                if tok:
                    new_seat_tokens[new_seat] = tok
                    new_token_to_seat[tok] = new_seat

            self.participants = new_participants
            self.seat_tokens = new_seat_tokens
            self.token_to_seat = new_token_to_seat

            for ws, old_s in list(self.connections.items()):
                if old_s in old_to_new_seat:
                    self.connections[ws] = old_to_new_seat[old_s]

            self.remaining_seconds = self.config.pick_clock_seconds
            state_dump = self.public_state().model_dump()

        for ws, updated_seat in list(self.connections.items()):
            try:
                await ws.send_json(
                    {
                        "event": "order_randomized",
                        "room_code": self.room_code,
                        "seat_number": updated_seat,
                        "state": state_dump,
                    }
                )
            except Exception:
                pass

        return True, {"event": "order_randomized", "room_code": self.room_code, "state": state_dump}

    async def undo_last_pick(self, commissioner_token: str) -> tuple[bool, dict[str, Any]]:
        async with self.lock:
            if not self._is_commissioner(commissioner_token):
                return False, {
                    "event": "pick_error",
                    "code": "FORBIDDEN",
                    "message": "Apenas o Comissário pode desfazer escolhas.",
                }
            if not self.picks_history:
                return False, {
                    "event": "pick_error",
                    "code": "NO_PICKS_TO_UNDO",
                    "message": "Nenhuma escolha foi realizada ainda para desfazer.",
                }

            last_record = self.picks_history.pop()
            pid = last_record.player.player_id
            self.drafted_player_ids.discard(pid)

            participant = self.participants.get(last_record.seat_number)
            if participant:
                participant.roster = [p for p in participant.roster if p.player_id != pid]

            self.current_pick_index = max(0, self.current_pick_index - 1)
            self.remaining_seconds = self.config.pick_clock_seconds
            if self.status == "completed":
                self.status = "active"
            self.ensure_clock_running()

            payload = {
                "event": "pick_undone",
                "room_code": self.room_code,
                "undone_pick": last_record.model_dump(),
                "state": self.public_state().model_dump(),
            }

        await self.broadcast(payload)
        return True, payload

    async def toggle_pause(self, commissioner_token: str) -> tuple[bool, dict[str, Any]]:
        async with self.lock:
            if not self._is_commissioner(commissioner_token):
                return False, {
                    "event": "pick_error",
                    "code": "FORBIDDEN",
                    "message": "Apenas o Comissário pode pausar ou retomar o draft.",
                }
            if self.status == "active":
                self.status = "paused"
            elif self.status == "paused":
                self.status = "active"
                self.ensure_clock_running()
            else:
                return False, {
                    "event": "pick_error",
                    "code": "INVALID_STATE",
                    "message": f"Inicie o draft antes de pausar (status atual: {self.status}).",
                }
            payload = {
                "event": "room_state",
                "room_code": self.room_code,
                "state": self.public_state().model_dump(),
            }

        await self.broadcast(payload)
        return True, payload

    def export_csv(self) -> str:
        output = io.StringIO()
        output.write("\ufeff")  # BOM UTF-8 para abrir com acentos perfeitos no Excel
        writer = csv.writer(output)
        writer.writerow(
            [
                "overall_pick",
                "round_number",
                "pick_in_round",
                "seat_number",
                "participant_name",
                "player_id",
                "short_name",
                "primary_position",
                "sector",
                "overall",
                "club_name",
                "league_name",
                "nationality",
                "value_eur",
                "source",
            ]
        )
        for rec in self.picks_history:
            writer.writerow(
                [
                    rec.overall_pick,
                    rec.round_number,
                    rec.pick_in_round,
                    rec.seat_number,
                    rec.participant_name,
                    rec.player.player_id,
                    rec.player.short_name,
                    rec.player.primary_position,
                    rec.player.sector,
                    rec.player.overall,
                    rec.player.club_name,
                    rec.player.league_name,
                    rec.player.nationality,
                    rec.player.value_eur,
                    rec.source,
                ]
            )
        return output.getvalue()


class MultiplayerRoomManager:
    def __init__(self, db_path: Path = Path("data/draftfc_local.db")) -> None:
        self.db_path = db_path
        self.rooms: dict[str, DraftRoom] = {}

    def _load_players_from_db(self, edition: str) -> list[RoomPlayer]:
        project_root = Path(__file__).resolve().parents[2]
        candidates = [self.db_path, project_root / "data" / "draftfc_local.db"]
        db_file = next((p for p in candidates if p.exists()), None)

        if db_file is not None:
            try:
                with sqlite3.connect(db_file) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT * FROM players WHERE LOWER(edition) = LOWER(?) ORDER BY overall DESC",
                        (edition,),
                    ).fetchall()
                    if not rows:
                        rows = conn.execute("SELECT * FROM players ORDER BY overall DESC").fetchall()

                    if rows:
                        loaded: list[RoomPlayer] = []
                        for r in rows:
                            d = dict(r)
                            pos, sec = normalize_position_and_sector(
                                str(d.get("primary_position") or ""),
                                str(d.get("positions") or ""),
                            )
                            loaded.append(
                                RoomPlayer(
                                    player_id=int(d.get("player_id") or 0),
                                    short_name=str(
                                        d.get("short_name")
                                        or d.get("full_name")
                                        or f"Player {d.get('player_id')}"
                                    ),
                                    overall=int(d.get("overall") or 75),
                                    primary_position=pos,
                                    sector=sec,
                                    club_id=int(d.get("club_id") or 0),
                                    club_name=str(d.get("club_name") or "Free Agent"),
                                    league_name=str(d.get("league_name") or "Unknown League"),
                                    nationality=str(
                                        d.get("nationality")
                                        or d.get("nationality_name")
                                        or "Unknown"
                                    ),
                                    value_eur=int(float(d.get("value_eur") or 0)),
                                )
                            )
                        return loaded
            except Exception as exc:
                print(f"[AVISO] Erro ao ler banco SQLite: {exc}")

        positions = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
        fallback_pool: list[RoomPlayer] = []
        for i in range(1, 601):
            raw_p = positions[i % len(positions)]
            pos, sec = normalize_position_and_sector(raw_p)
            fallback_pool.append(
                RoomPlayer(
                    player_id=900000 + i,
                    short_name=f"Player {i}",
                    overall=92 - (i // 30),
                    primary_position=pos,
                    sector=sec,
                    club_id=(i % 40) + 1,
                    club_name=f"Club {(i % 40) + 1}",
                    league_name=f"League {(i % 8) + 1}",
                    nationality=f"Nation {(i % 20) + 1}",
                    value_eur=max(1_000_000, (100 - (i // 10)) * 1_000_000),
                )
            )
        return fallback_pool

    def list_rooms(self) -> list[RoomSummary]:
        return [r.summary() for r in self.rooms.values()]

    def create_room(self, req: CreateRoomRequest) -> tuple[DraftRoom, str]:
        room_code = secrets.token_hex(3).upper()
        while room_code in self.rooms:
            room_code = secrets.token_hex(3).upper()

        pool = req.custom_players if req.custom_players else self._load_players_from_db(req.config.edition)
        room = DraftRoom(
            room_code=room_code,
            config=req.config,
            players_pool=pool,
            commissioner_name=req.commissioner_name,
        )
        self.rooms[room_code] = room
        return room, room.commissioner_token

    def get_room(self, room_code: str) -> DraftRoom:
        room = self.rooms.get(room_code.upper())
        if room is None:
            raise KeyError(f"Sala {room_code} não encontrada.")
        return room

    async def delete_room(self, room_code: str) -> None:
        code = room_code.upper()
        room = self.rooms.pop(code, None)
        if room is None:
            raise KeyError(f"Sala {room_code} não encontrada.")
        room.status = "completed"
        if room.clock_task and not room.clock_task.done():
            room.clock_task.cancel()
        await room.broadcast({"event": "room_deleted", "room_code": code})

    def join_room(self, room_code: str, req: JoinRoomRequest) -> tuple[DraftRoom, int, str]:
        room = self.get_room(room_code)
        clean_name = req.display_name.strip()

        if req.seat_token and req.seat_token in room.token_to_seat:
            seat = room.token_to_seat[req.seat_token]
            if clean_name:
                room.participants[seat].display_name = clean_name
            return room, seat, req.seat_token

        if not room.verify_password(req.password):
            raise PermissionError("Senha da sala incorreta.")

        existing_seat = next(
            (
                s
                for s, p in room.participants.items()
                if p.claimed and p.display_name.strip().lower() == clean_name.lower()
            ),
            None,
        )
        if existing_seat is not None:
            if req.seat_number is not None and req.seat_number != existing_seat:
                raise ValueError(f"O manager '{clean_name}' já pertence ao Assento #{existing_seat}.")
            token = room.seat_tokens.get(existing_seat)
            if not token:
                token = secrets.token_urlsafe(16)
                room.seat_tokens[existing_seat] = token
                room.token_to_seat[token] = existing_seat
            return room, existing_seat, token

        target_seat = req.seat_number
        if target_seat is not None:
            if target_seat < 1 or target_seat > room.config.num_teams:
                raise ValueError(f"Assento inválido: {target_seat}.")
            if room.participants[target_seat].claimed:
                owner = room.participants[target_seat].display_name
                raise ValueError(
                    f"Assento #{target_seat} já está ocupado por '{owner}'. "
                    f"Use o nome '{owner}' se quiser reconectar nele."
                )
        else:
            free_seats = [s for s, p in room.participants.items() if not p.claimed]
            if not free_seats:
                raise ValueError("Todos os assentos da sala já estão ocupados.")
            target_seat = free_seats[0]

        token = secrets.token_urlsafe(16)
        room.participants[target_seat].claimed = True
        room.participants[target_seat].display_name = clean_name
        room.seat_tokens[target_seat] = token
        room.token_to_seat[token] = target_seat
        return room, target_seat, token

    async def start_room(
        self, room_code: str, commissioner_token: str, start_background_clock: bool = True
    ) -> DraftRoom:
        room = self.get_room(room_code)
        if not room._is_commissioner(commissioner_token):
            raise PermissionError("Apenas o comissário pode iniciar o draft.")

        async with room.lock:
            room.status = "active"
            room.remaining_seconds = room.config.pick_clock_seconds
            room.auto_clock_enabled = start_background_clock
            room.ensure_clock_running()

        await room.broadcast({"event": "room_state", "state": room.public_state().model_dump()})
        return room

    async def _clock_loop(self, room: DraftRoom) -> None:
        await room._room_clock_loop()


room_manager = MultiplayerRoomManager()