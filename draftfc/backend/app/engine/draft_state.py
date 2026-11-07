import threading
import time
from typing import Dict, List, Optional, Sequence, Set
from pydantic import BaseModel, Field

from backend.app.engine.draft_lottery import (
    LotteryReceipt,
    PickSlot,
    build_pick_schedule,
    draw_draft_order,
)
from backend.app.engine.fallback_autodraft import (
    FallbackDecision,
    FallbackReason,
    FallbackStrategy,
    SkippedQueueItem,
    build_sorted_pool,
    select_fallback_player,
)
from backend.app.engine.models import (
    DraftPlayer,
    DraftRulesConfig,
    DraftStatus,
    RuleViolation,
    ValidationResult,
)
from backend.app.engine.positions import PitchSector
from backend.app.engine.rules_validator import validate_candidate_pick


class DraftEngineError(Exception):
    pass


class ConcurrencyConflictError(DraftEngineError):
    pass


class OutOfTurnError(DraftEngineError):
    pass


class IneligiblePickError(DraftEngineError):
    def __init__(self, validation: ValidationResult) -> None:
        self.validation = validation
        reasons = "; ".join(v.message for v in validation.violations)
        super().__init__(f"Escolha inválida pelas regras: {reasons}")


class RecordedPick(BaseModel):
    overall_pick: int
    round_number: int
    pick_in_round: int
    participant_id: str
    player_id: int
    player_short_name: str
    club_name: str
    league_name: str
    overall: int
    sector: PitchSector
    made_by_commissioner: bool = False
    auto_drafted: bool = False
    fallback_reason: Optional[FallbackReason] = None
    skipped_queue_items: List[SkippedQueueItem] = Field(default_factory=list)


class QueueEntryStatus(BaseModel):
    priority: int
    player_id: int
    short_name: str
    overall: int
    primary_position: str
    club_name: str
    eligible_now: bool
    violations: List[RuleViolation] = Field(default_factory=list)


class ViabilitySimulationReport(BaseModel):
    viable: bool
    total_picks_simulated: int
    elapsed_ms: float
    error_message: Optional[str] = None
    final_roster_counts: Dict[str, int] = Field(default_factory=dict)


class DraftSessionEngine:
    MAX_QUEUE_SIZE_PER_USER = 100

    def __init__(
        self,
        room_id: str,
        edition: str,
        rules: DraftRulesConfig,
        player_pool: Dict[int, DraftPlayer],
        participants: Sequence[str],
        lottery_seed: Optional[str] = None,
        manual_order: Optional[Sequence[str]] = None,
        fallback_strategy: FallbackStrategy = FallbackStrategy.SMART_BALANCED,
    ) -> None:
        self._lock = threading.RLock()
        self.room_id = room_id
        self.edition = edition
        self.rules = rules
        self.fallback_strategy = fallback_strategy
        self.player_pool: Dict[int, DraftPlayer] = dict(player_pool)
        self._sorted_pool: List[DraftPlayer] = build_sorted_pool(self.player_pool)

        if manual_order is not None:
            unique_manual = [p.strip() for p in dict.fromkeys(manual_order) if p.strip()]
            if len(unique_manual) < 2:
                raise DraftEngineError("Ordem manual exige ao menos 2 participantes únicos.")
            self.lottery_receipt = LotteryReceipt(
                ordered_participant_ids=unique_manual,
                seed_used="MANUAL_ORDER",
                verification_hash="MANUAL_ORDER",
            )
        else:
            self.lottery_receipt = draw_draft_order(participants, seed=lottery_seed)

        self.ordered_participants: List[str] = self.lottery_receipt.ordered_participant_ids
        self.pick_schedule: List[PickSlot] = build_pick_schedule(
            ordered_participant_ids=self.ordered_participants,
            roster_size=self.rules.roster_size,
            draft_format=self.rules.draft_format,
        )

        self.status: DraftStatus = DraftStatus.ACTIVE
        self.state_version: int = 1
        self.picks_history: List[RecordedPick] = []
        self._queue_undo_snapshots: List[Dict[str, int]] = []
        self.picked_player_ids: Set[int] = set()
        self.rosters: Dict[str, List[DraftPlayer]] = {p: [] for p in self.ordered_participants}
        self._user_queues: Dict[str, List[int]] = {p: [] for p in self.ordered_participants}
        self._auto_pick_participants: Set[str] = set()

    @property
    def current_slot(self) -> Optional[PickSlot]:
        with self._lock:
            idx = len(self.picks_history)
            if idx >= len(self.pick_schedule):
                return None
            return self.pick_schedule[idx]

    def pause(self) -> None:
        with self._lock:
            if self.status == DraftStatus.ACTIVE:
                self.status = DraftStatus.PAUSED
                self.state_version += 1

    def resume(self) -> None:
        with self._lock:
            if self.status == DraftStatus.PAUSED:
                self.status = DraftStatus.ACTIVE
                self.state_version += 1

    def _assert_valid_participant(self, participant_id: str) -> None:
        if participant_id not in self._user_queues:
            raise DraftEngineError(f"Participante '{participant_id}' não pertence a esta sala.")

    def set_auto_pick(self, participant_id: str, enabled: bool) -> bool:
        with self._lock:
            self._assert_valid_participant(participant_id)
            if enabled:
                self._auto_pick_participants.add(participant_id)
            else:
                self._auto_pick_participants.discard(participant_id)
            self.state_version += 1
            return enabled

    def is_auto_pick_enabled(self, participant_id: str) -> bool:
        with self._lock:
            self._assert_valid_participant(participant_id)
            return participant_id in self._auto_pick_participants

    def enqueue_player(self, participant_id: str, player_id: int) -> List[int]:
        with self._lock:
            self._assert_valid_participant(participant_id)
            if player_id not in self.player_pool:
                raise DraftEngineError(f"Jogador ID {player_id} não existe no pool ({self.edition}).")
            if player_id in self.picked_player_ids:
                raise DraftEngineError("Jogador já foi draftado e não pode entrar na fila.")

            queue = self._user_queues[participant_id]
            if player_id in queue:
                return list(queue)
            if len(queue) >= self.MAX_QUEUE_SIZE_PER_USER:
                raise DraftEngineError(
                    f"Fila atingiu o limite máximo de {self.MAX_QUEUE_SIZE_PER_USER} jogadores."
                )
            queue.append(player_id)
            self.state_version += 1
            return list(queue)

    def remove_from_queue(self, participant_id: str, player_id: int) -> List[int]:
        with self._lock:
            self._assert_valid_participant(participant_id)
            queue = self._user_queues[participant_id]
            if player_id in queue:
                queue.remove(player_id)
                self.state_version += 1
            return list(queue)

    def reorder_queue(self, participant_id: str, new_ordered_player_ids: Sequence[int]) -> List[int]:
        with self._lock:
            self._assert_valid_participant(participant_id)
            cleaned: List[int] = []
            for pid in new_ordered_player_ids:
                if pid not in self.player_pool:
                    raise DraftEngineError(f"Jogador ID {pid} inexistente no pool.")
                if pid in self.picked_player_ids:
                    continue
                if pid not in cleaned:
                    cleaned.append(pid)
            if len(cleaned) > self.MAX_QUEUE_SIZE_PER_USER:
                raise DraftEngineError("Tamanho da fila excede o limite permitido.")
            self._user_queues[participant_id] = cleaned
            self.state_version += 1
            return list(cleaned)

    def inspect_participant_queue(self, participant_id: str) -> List[QueueEntryStatus]:
        with self._lock:
            self._assert_valid_participant(participant_id)
            roster = self.rosters[participant_id]
            statuses: List[QueueEntryStatus] = []
            for idx, pid in enumerate(self._user_queues[participant_id], start=1):
                player = self.player_pool[pid]
                val = validate_candidate_pick(
                    candidate=player,
                    current_roster=roster,
                    globally_picked_ids=self.picked_player_ids,
                    rules=self.rules,
                )
                statuses.append(
                    QueueEntryStatus(
                        priority=idx,
                        player_id=pid,
                        short_name=player.short_name,
                        overall=player.overall,
                        primary_position=player.primary_position,
                        club_name=player.club_name,
                        eligible_now=val.eligible,
                        violations=val.violations,
                    )
                )
            return statuses

    def peek_next_eligible_queued_player(self, participant_id: str) -> Optional[DraftPlayer]:
        with self._lock:
            self._assert_valid_participant(participant_id)
            roster = self.rosters[participant_id]
            for pid in self._user_queues[participant_id]:
                if pid in self.picked_player_ids:
                    continue
                candidate = self.player_pool[pid]
                res = validate_candidate_pick(
                    candidate=candidate,
                    current_roster=roster,
                    globally_picked_ids=self.picked_player_ids,
                    rules=self.rules,
                )
                if res.eligible:
                    return candidate
            return None

    def preview_fallback_for_current_turn(
        self,
        strategy_override: Optional[FallbackStrategy] = None,
    ) -> FallbackDecision:
        with self._lock:
            slot = self.current_slot
            if slot is None:
                raise DraftEngineError("Todos os turnos já foram concluídos.")
            pid = slot.participant_id
            return select_fallback_player(
                participant_id=pid,
                current_roster=self.rosters[pid],
                participant_queue_ids=self._user_queues[pid],
                player_pool=self.player_pool,
                sorted_pool=self._sorted_pool,
                globally_picked_ids=self.picked_player_ids,
                rules=self.rules,
                strategy=strategy_override or self.fallback_strategy,
            )

    def auto_pick_current_turn(
        self,
        expected_overall_pick: Optional[int] = None,
        strategy_override: Optional[FallbackStrategy] = None,
    ) -> RecordedPick:
        with self._lock:
            if self.status != DraftStatus.ACTIVE:
                raise DraftEngineError(f"Auto-Draft indisponível com status '{self.status}'.")

            slot = self.current_slot
            if slot is None:
                self.status = DraftStatus.COMPLETED
                raise DraftEngineError("Todos os turnos do draft já foram preenchidos.")

            if expected_overall_pick is not None and expected_overall_pick != slot.overall_pick:
                raise ConcurrencyConflictError(
                    f"Timeout duplicado ignorado: esperado pick #{expected_overall_pick}, "
                    f"turno atual #{slot.overall_pick}."
                )

            decision = self.preview_fallback_for_current_turn(strategy_override=strategy_override)
            return self.make_pick(
                actor_participant_id=slot.participant_id,
                player_id=decision.player.player_id,
                expected_overall_pick=slot.overall_pick,
                as_commissioner=True,
                auto_drafted=True,
                fallback_reason=decision.reason,
                skipped_queue_items=decision.skipped_queue_items,
            )

    def advance_consecutive_auto_picks(self) -> List[RecordedPick]:
        executed: List[RecordedPick] = []
        with self._lock:
            while self.status == DraftStatus.ACTIVE and self.current_slot is not None:
                if self.current_slot.participant_id not in self._auto_pick_participants:
                    break
                executed.append(
                    self.auto_pick_current_turn(expected_overall_pick=self.current_slot.overall_pick)
                )
        return executed

    def make_pick(
        self,
        actor_participant_id: str,
        player_id: int,
        expected_overall_pick: Optional[int] = None,
        expected_state_version: Optional[int] = None,
        as_commissioner: bool = False,
        auto_drafted: bool = False,
        fallback_reason: Optional[FallbackReason] = None,
        skipped_queue_items: Optional[List[SkippedQueueItem]] = None,
    ) -> RecordedPick:
        with self._lock:
            if self.status == DraftStatus.PAUSED:
                raise DraftEngineError("O draft está pausado pelo comissário.")
            if self.status == DraftStatus.COMPLETED:
                raise DraftEngineError("O draft já foi concluído.")
            if self.status != DraftStatus.ACTIVE:
                raise DraftEngineError(f"Estado inválido para escolhas: {self.status}.")

            if expected_state_version is not None and expected_state_version != self.state_version:
                raise ConcurrencyConflictError(
                    f"Conflito de versão de estado: recebido v{expected_state_version}, "
                    f"atual v{self.state_version}."
                )

            slot = self.current_slot
            if slot is None:
                self.status = DraftStatus.COMPLETED
                raise DraftEngineError("Todos os turnos do draft já foram preenchidos.")

            if expected_overall_pick is not None and expected_overall_pick != slot.overall_pick:
                raise ConcurrencyConflictError(
                    f"Conflito de turno (duplo envio bloqueado): esperado pick #{expected_overall_pick}, "
                    f"mas o turno atual é #{slot.overall_pick}."
                )

            if not as_commissioner and actor_participant_id != slot.participant_id:
                raise OutOfTurnError(
                    f"Fora de turno: vez de '{slot.participant_id}', tentativa por '{actor_participant_id}'."
                )

            if player_id not in self.player_pool:
                raise DraftEngineError(f"Jogador ID {player_id} não encontrado no banco ({self.edition}).")

            candidate = self.player_pool[player_id]
            target_participant = slot.participant_id
            current_roster = self.rosters[target_participant]

            validation = validate_candidate_pick(
                candidate=candidate,
                current_roster=current_roster,
                globally_picked_ids=self.picked_player_ids,
                rules=self.rules,
            )
            if not validation.eligible:
                raise IneligiblePickError(validation)

            current_roster.append(candidate)
            self.picked_player_ids.add(player_id)

            removed_queue_indices: Dict[str, int] = {}
            for uid, q in self._user_queues.items():
                if player_id in q:
                    removed_queue_indices[uid] = q.index(player_id)
                    q.remove(player_id)
            self._queue_undo_snapshots.append(removed_queue_indices)

            recorded = RecordedPick(
                overall_pick=slot.overall_pick,
                round_number=slot.round_number,
                pick_in_round=slot.pick_in_round,
                participant_id=target_participant,
                player_id=candidate.player_id,
                player_short_name=candidate.short_name,
                club_name=candidate.club_name,
                league_name=candidate.league_name,
                overall=candidate.overall,
                sector=candidate.sector,
                made_by_commissioner=as_commissioner,
                auto_drafted=auto_drafted,
                fallback_reason=fallback_reason,
                skipped_queue_items=skipped_queue_items or [],
            )
            self.picks_history.append(recorded)

            if len(self.picks_history) >= len(self.pick_schedule):
                self.status = DraftStatus.COMPLETED

            self.state_version += 1
            return recorded

    def undo_last_pick(self) -> RecordedPick:
        with self._lock:
            if not self.picks_history:
                raise DraftEngineError("Nenhuma escolha realizada para desfazer.")

            last = self.picks_history.pop()
            removed_indices = self._queue_undo_snapshots.pop()
            self.picked_player_ids.discard(last.player_id)

            self.rosters[last.participant_id] = [
                p for p in self.rosters[last.participant_id] if p.player_id != last.player_id
            ]

            for uid, orig_idx in removed_indices.items():
                q = self._user_queues[uid]
                if last.player_id not in q:
                    q.insert(min(orig_idx, len(q)), last.player_id)

            if self.status == DraftStatus.COMPLETED:
                self.status = DraftStatus.ACTIVE

            self.state_version += 1
            return last


def simulate_full_draft_viability(
    rules: DraftRulesConfig,
    player_pool: Dict[int, DraftPlayer],
    participants: Sequence[str],
    edition: str = "fc27",
    strategy: FallbackStrategy = FallbackStrategy.SMART_BALANCED,
) -> ViabilitySimulationReport:
    t0 = time.perf_counter()
    sim_engine = DraftSessionEngine(
        room_id="viability-sim",
        edition=edition,
        rules=rules,
        player_pool=player_pool,
        participants=participants,
        lottery_seed="viability-check",
        fallback_strategy=strategy,
    )
    try:
        while sim_engine.status == DraftStatus.ACTIVE and sim_engine.current_slot is not None:
            sim_engine.auto_pick_current_turn()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return ViabilitySimulationReport(
            viable=True,
            total_picks_simulated=len(sim_engine.picks_history),
            elapsed_ms=round(elapsed_ms, 2),
            final_roster_counts={u: len(r) for u, r in sim_engine.rosters.items()},
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return ViabilitySimulationReport(
            viable=False,
            total_picks_simulated=len(sim_engine.picks_history),
            elapsed_ms=round(elapsed_ms, 2),
            error_message=str(exc),
            final_roster_counts={u: len(r) for u, r in sim_engine.rosters.items()},
        )