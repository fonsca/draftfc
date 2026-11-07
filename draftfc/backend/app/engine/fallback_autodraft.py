from collections import Counter
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set, Tuple
from pydantic import BaseModel, Field

from backend.app.engine.models import (
    DraftPlayer,
    DraftRulesConfig,
    RuleViolation,
    RuleViolationCode,
)
from backend.app.engine.positions import PitchSector
from backend.app.engine.rules_validator import FastRosterContext, validate_candidate_pick

MANDATORY_SECTOR_NEED_BONUS: float = 2.5
BACKUP_GK_EARLY_PENALTY: float = -8.0
EXCESS_GK_PENALTY: float = -20.0


class FallbackStrategy(str, Enum):
    SMART_BALANCED = "smart_balanced"
    STRICT_OVERALL = "strict_overall"


class FallbackReason(str, Enum):
    QUEUE_PRIORITY = "queue_priority"
    SMART_SECTOR_NEED = "smart_sector_need"
    BEST_AVAILABLE_OVR = "best_available_ovr"


class SkippedQueueItem(BaseModel):
    player_id: int
    short_name: str
    violations: List[RuleViolation] = Field(default_factory=list)


class FallbackDecision(BaseModel):
    player: DraftPlayer
    reason: FallbackReason
    strategy_used: FallbackStrategy
    effective_score: float
    inspected_candidates: int
    skipped_queue_items: List[SkippedQueueItem] = Field(default_factory=list)


class NoEligiblePlayerError(Exception):
    def __init__(
        self,
        participant_id: str,
        rejection_counts: Dict[str, int],
        message: Optional[str] = None,
    ) -> None:
        self.participant_id = participant_id
        self.rejection_counts = rejection_counts
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(rejection_counts.items()))
        default_msg = (
            f"Nenhum jogador elegível encontrado no pool para '{participant_id}'. "
            f"Motivos de bloqueio acumulados: [{summary or 'pool vazio'}]."
        )
        super().__init__(message or default_msg)


def player_deterministic_sort_key(p: DraftPlayer) -> Tuple[int, int, int, int, int]:
    """Desempate determinístico: Maior OVR -> Maior Potencial -> Maior Valor -> Menor Idade -> Menor ID."""
    return (-p.overall, -p.potential, -p.value_eur, p.age, p.player_id)


def build_sorted_pool(player_pool: Dict[int, DraftPlayer]) -> List[DraftPlayer]:
    return sorted(player_pool.values(), key=player_deterministic_sort_key)


def compute_smart_score(
    candidate: DraftPlayer,
    sector_counts: Dict[PitchSector, int],
    roster_len: int,
    rules: DraftRulesConfig,
) -> Tuple[float, bool]:
    score = float(candidate.overall)
    min_req = rules.min_by_sector.get(candidate.sector, 0)
    current_in_sector = sector_counts.get(candidate.sector, 0)
    fills_mandatory_need = current_in_sector < min_req

    if fills_mandatory_need:
        score += MANDATORY_SECTOR_NEED_BONUS
        if (
            candidate.sector == PitchSector.GK
            and current_in_sector >= 1
            and roster_len < max(1, rules.roster_size - 4)
        ):
            score -= 3.5
    elif candidate.sector == PitchSector.GK:
        if current_in_sector == 1 and roster_len < max(1, rules.roster_size - 3):
            score += BACKUP_GK_EARLY_PENALTY
        elif current_in_sector >= 2:
            score += EXCESS_GK_PENALTY

    return score, fills_mandatory_need


def _estimate_floor_player_cost(
    sorted_pool: Sequence[DraftPlayer],
    globally_picked_ids: Set[int],
    rules: DraftRulesConfig,
) -> int:
    min_cost: Optional[int] = None
    for p in sorted_pool:
        if p.player_id in globally_picked_ids:
            continue
        if rules.min_overall <= p.overall <= rules.max_overall:
            if min_cost is None or p.value_eur < min_cost:
                min_cost = p.value_eur
                if min_cost == 0:
                    return 0
    return min_cost or 0


def select_fallback_player(
    participant_id: str,
    current_roster: Sequence[DraftPlayer],
    participant_queue_ids: Sequence[int],
    player_pool: Dict[int, DraftPlayer],
    sorted_pool: Sequence[DraftPlayer],
    globally_picked_ids: Set[int],
    rules: DraftRulesConfig,
    strategy: FallbackStrategy = FallbackStrategy.SMART_BALANCED,
) -> FallbackDecision:
    skipped_queue: List[SkippedQueueItem] = []

    # Camada 1: Fila de prioridade pessoal do participante
    for q_pid in participant_queue_ids:
        if q_pid in globally_picked_ids:
            continue
        candidate = player_pool.get(q_pid)
        if candidate is None:
            continue
        val = validate_candidate_pick(
            candidate=candidate,
            current_roster=current_roster,
            globally_picked_ids=globally_picked_ids,
            rules=rules,
        )
        if val.eligible:
            return FallbackDecision(
                player=candidate,
                reason=FallbackReason.QUEUE_PRIORITY,
                strategy_used=strategy,
                effective_score=float(candidate.overall),
                inspected_candidates=len(skipped_queue) + 1,
                skipped_queue_items=skipped_queue,
            )
        skipped_queue.append(
            SkippedQueueItem(
                player_id=candidate.player_id,
                short_name=candidate.short_name,
                violations=val.violations,
            )
        )

    # Camada 2: Varredura O(1) no pool pré-ordenado com early-exit matemático
    fast_ctx = FastRosterContext(
        current_roster=current_roster,
        globally_picked_ids=globally_picked_ids,
        rules=rules,
    )
    slots_remaining_after_pick = max(0, fast_ctx.slots_after_pick)
    floor_unit_cost = (
        _estimate_floor_player_cost(sorted_pool, globally_picked_ids, rules)
        if rules.max_budget_eur is not None and slots_remaining_after_pick > 0
        else 0
    )

    max_possible_bonus = (
        MANDATORY_SECTOR_NEED_BONUS
        if (strategy == FallbackStrategy.SMART_BALANCED and fast_ctx.has_unfilled_mandatory_sector)
        else 0.0
    )

    def scan_pool(enforce_budget_reserve: bool) -> Tuple[Optional[DraftPlayer], float, bool, int]:
        best_candidate: Optional[DraftPlayer] = None
        best_score: float = -1e9
        best_fills_need: bool = False
        best_tie_key: Tuple[int, int, int, int, int] = (999, 999, 999, 999, 999999999)
        inspected = 0

        for candidate in sorted_pool:
            if candidate.player_id in globally_picked_ids:
                continue

            if best_candidate is not None:
                upper_bound = candidate.overall + max_possible_bonus
                if upper_bound < best_score:
                    break
                if max_possible_bonus == 0.0 and upper_bound == best_score:
                    break

            inspected += 1
            if not fast_ctx.is_eligible(candidate):
                continue

            if enforce_budget_reserve and rules.max_budget_eur is not None and slots_remaining_after_pick > 0:
                budget_left = rules.max_budget_eur - (fast_ctx.spent_budget + candidate.value_eur)
                if budget_left < slots_remaining_after_pick * floor_unit_cost:
                    continue

            if strategy == FallbackStrategy.STRICT_OVERALL:
                return candidate, float(candidate.overall), False, inspected

            cand_score, fills_need = compute_smart_score(
                candidate=candidate,
                sector_counts=fast_ctx.sector_counts,
                roster_len=fast_ctx.roster_len,
                rules=rules,
            )
            cand_tie_key = player_deterministic_sort_key(candidate)

            if (cand_score > best_score) or (
                cand_score == best_score and cand_tie_key < best_tie_key
            ):
                best_candidate = candidate
                best_score = cand_score
                best_fills_need = fills_need
                best_tie_key = cand_tie_key

        return best_candidate, best_score, best_fills_need, inspected

    use_budget_guard = rules.max_budget_eur is not None and slots_remaining_after_pick > 0
    chosen, score, fills_need, inspected = scan_pool(enforce_budget_reserve=use_budget_guard)

    if chosen is None and use_budget_guard:
        chosen, score, fills_need, inspected = scan_pool(enforce_budget_reserve=False)

    if chosen is None:
        rejection_counter: Counter[str] = Counter()
        for c in sorted_pool:
            if c.player_id in globally_picked_ids:
                rejection_counter[RuleViolationCode.ALREADY_PICKED.value] += 1
            else:
                v_res = validate_candidate_pick(c, current_roster, globally_picked_ids, rules)
                for v in v_res.violations:
                    rejection_counter[v.code.value] += 1
        raise NoEligiblePlayerError(
            participant_id=participant_id,
            rejection_counts=dict(rejection_counter),
        )

    reason = (
        FallbackReason.SMART_SECTOR_NEED
        if (strategy == FallbackStrategy.SMART_BALANCED and fills_need)
        else FallbackReason.BEST_AVAILABLE_OVR
    )

    return FallbackDecision(
        player=chosen,
        reason=reason,
        strategy_used=strategy,
        effective_score=score,
        inspected_candidates=inspected,
        skipped_queue_items=skipped_queue,
    )