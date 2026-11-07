from collections import Counter
from typing import Dict, List, Sequence, Set
from backend.app.engine.models import (
    DraftPlayer,
    DraftRulesConfig,
    RuleViolation,
    RuleViolationCode,
    ValidationResult,
)
from backend.app.engine.positions import PitchSector


class FastRosterContext:
    """Pré-calcula contadores do elenco atual uma vez por turno para validação O(1) em 19k+ atletas."""

    __slots__ = (
        "roster_len",
        "rules",
        "globally_picked_ids",
        "spent_budget",
        "club_counts",
        "club_name_counts",
        "league_counts",
        "league_name_counts",
        "nat_counts",
        "pos_counts",
        "sector_counts",
        "tier_counts",
        "total_missing_mandatory",
        "has_unfilled_mandatory_sector",
        "slots_after_pick",
    )

    def __init__(
        self,
        current_roster: Sequence[DraftPlayer],
        globally_picked_ids: Set[int],
        rules: DraftRulesConfig,
    ) -> None:
        self.roster_len = len(current_roster)
        self.rules = rules
        self.globally_picked_ids = globally_picked_ids
        self.spent_budget = sum(p.value_eur for p in current_roster)

        self.club_counts = Counter(p.club_id for p in current_roster if p.club_id is not None)
        self.club_name_counts = Counter(p.club_name for p in current_roster if p.club_id is None)
        self.league_counts = Counter(p.league_id for p in current_roster if p.league_id is not None)
        self.league_name_counts = Counter(p.league_name for p in current_roster if p.league_id is None)
        self.nat_counts = Counter(p.nationality_name.strip().lower() for p in current_roster)
        self.pos_counts = Counter(p.primary_position for p in current_roster)
        self.sector_counts = Counter(p.sector for p in current_roster)
        self.tier_counts = [
            sum(1 for p in current_roster if t.min_ovr <= p.overall <= t.max_ovr)
            for t in rules.ovr_tier_caps
        ]

        self.slots_after_pick = rules.roster_size - (self.roster_len + 1)
        self.total_missing_mandatory = sum(
            max(0, min_req - self.sector_counts.get(sec, 0))
            for sec, min_req in rules.min_by_sector.items()
        )
        self.has_unfilled_mandatory_sector = self.total_missing_mandatory > 0

    def is_eligible(self, candidate: DraftPlayer) -> bool:
        if candidate.player_id in self.globally_picked_ids:
            return False
        rules = self.rules
        if self.roster_len >= rules.roster_size:
            return False
        if not (rules.min_overall <= candidate.overall <= rules.max_overall):
            return False

        for idx, tier in enumerate(rules.ovr_tier_caps):
            if tier.min_ovr <= candidate.overall <= tier.max_ovr:
                if self.tier_counts[idx] >= tier.max_players:
                    return False

        if rules.max_budget_eur is not None:
            if self.spent_budget + candidate.value_eur > rules.max_budget_eur:
                return False

        if rules.max_per_club is not None:
            if not (rules.ignore_free_agents_in_group_limits and candidate.is_free_agent):
                same_club = (
                    self.club_counts[candidate.club_id]
                    if candidate.club_id is not None
                    else self.club_name_counts[candidate.club_name]
                )
                if same_club >= rules.max_per_club:
                    return False

        if rules.max_per_league is not None:
            is_free_lg = candidate.league_id is None or candidate.league_name.strip().lower() == "free agents"
            if not (rules.ignore_free_agents_in_group_limits and is_free_lg):
                same_lg = (
                    self.league_counts[candidate.league_id]
                    if candidate.league_id is not None
                    else self.league_name_counts[candidate.league_name]
                )
                if same_lg >= rules.max_per_league:
                    return False

        if rules.max_per_nationality is not None:
            if self.nat_counts[candidate.nationality_name.strip().lower()] >= rules.max_per_nationality:
                return False

        pos_cap = rules.max_by_position.get(candidate.primary_position)
        if pos_cap is not None and self.pos_counts[candidate.primary_position] >= pos_cap:
            return False

        sec_cap = rules.max_by_sector.get(candidate.sector)
        if sec_cap is not None and self.sector_counts[candidate.sector] >= sec_cap:
            return False

        reduces_missing = (
            1
            if self.sector_counts.get(candidate.sector, 0) < rules.min_by_sector.get(candidate.sector, 0)
            else 0
        )
        if (self.total_missing_mandatory - reduces_missing) > self.slots_after_pick:
            return False

        return True


def validate_candidate_pick(
    candidate: DraftPlayer,
    current_roster: Sequence[DraftPlayer],
    globally_picked_ids: Set[int],
    rules: DraftRulesConfig,
) -> ValidationResult:
    violations: List[RuleViolation] = []

    if candidate.player_id in globally_picked_ids:
        violations.append(
            RuleViolation(
                code=RuleViolationCode.ALREADY_PICKED,
                message=f"{candidate.short_name} já foi escolhido neste draft.",
            )
        )

    if len(current_roster) >= rules.roster_size:
        violations.append(
            RuleViolation(
                code=RuleViolationCode.ROSTER_FULL,
                message=f"Elenco já atingiu o limite máximo de {rules.roster_size} jogadores.",
            )
        )

    if not (rules.min_overall <= candidate.overall <= rules.max_overall):
        violations.append(
            RuleViolation(
                code=RuleViolationCode.OVR_OUT_OF_RANGE,
                message=(
                    f"Overall {candidate.overall} fora da faixa permitida "
                    f"({rules.min_overall}–{rules.max_overall})."
                ),
            )
        )

    for tier in rules.ovr_tier_caps:
        if tier.min_ovr <= candidate.overall <= tier.max_ovr:
            count_in_tier = sum(1 for p in current_roster if tier.min_ovr <= p.overall <= t.max_ovr) if False else sum(
                1 for p in current_roster if tier.min_ovr <= p.overall <= tier.max_ovr
            )
            if count_in_tier >= tier.max_players:
                violations.append(
                    RuleViolation(
                        code=RuleViolationCode.OVR_TIER_LIMIT_REACHED,
                        message=(
                            f"Limite atingido para faixa OVR {tier.min_ovr}–{tier.max_ovr} "
                            f"({count_in_tier}/{tier.max_players})."
                        ),
                    )
                )

    if rules.max_budget_eur is not None:
        spent = sum(p.value_eur for p in current_roster)
        if spent + candidate.value_eur > rules.max_budget_eur:
            violations.append(
                RuleViolation(
                    code=RuleViolationCode.BUDGET_EXCEEDED,
                    message=(
                        f"Orçamento insuficiente: €{candidate.value_eur:,} excede o saldo "
                        f"restante de €{rules.max_budget_eur - spent:,}."
                    ),
                )
            )

    if rules.max_per_club is not None:
        if not (rules.ignore_free_agents_in_group_limits and candidate.is_free_agent):
            same_club = sum(
                1
                for p in current_roster
                if (
                    (candidate.club_id is not None and p.club_id == candidate.club_id)
                    or (candidate.club_id is None and p.club_name == candidate.club_name)
                )
            )
            if same_club >= rules.max_per_club:
                violations.append(
                    RuleViolation(
                        code=RuleViolationCode.CLUB_LIMIT_REACHED,
                        message=(
                            f"Limite por clube atingido para {candidate.club_name} "
                            f"({same_club}/{rules.max_per_club})."
                        ),
                    )
                )

    if rules.max_per_league is not None:
        is_free_league = candidate.league_id is None or candidate.league_name.strip().lower() == "free agents"
        if not (rules.ignore_free_agents_in_group_limits and is_free_league):
            same_league = sum(
                1
                for p in current_roster
                if (
                    (candidate.league_id is not None and p.league_id == candidate.league_id)
                    or (candidate.league_id is None and p.league_name == candidate.league_name)
                )
            )
            if same_league >= rules.max_per_league:
                violations.append(
                    RuleViolation(
                        code=RuleViolationCode.LEAGUE_LIMIT_REACHED,
                        message=(
                            f"Limite por liga atingido para {candidate.league_name} "
                            f"({same_league}/{rules.max_per_league})."
                        ),
                    )
                )

    if rules.max_per_nationality is not None:
        same_nat = sum(
            1
            for p in current_roster
            if p.nationality_name.strip().lower() == candidate.nationality_name.strip().lower()
        )
        if same_nat >= rules.max_per_nationality:
            violations.append(
                RuleViolation(
                    code=RuleViolationCode.NATIONALITY_LIMIT_REACHED,
                    message=(
                        f"Limite por nacionalidade atingido para {candidate.nationality_name} "
                        f"({same_nat}/{rules.max_per_nationality})."
                    ),
                )
            )

    pos_cap = rules.max_by_position.get(candidate.primary_position)
    if pos_cap is not None:
        same_pos = sum(1 for p in current_roster if p.primary_position == candidate.primary_position)
        if same_pos >= pos_cap:
            violations.append(
                RuleViolation(
                    code=RuleViolationCode.POSITION_MAX_REACHED,
                    message=f"Limite na posição {candidate.primary_position} atingido ({same_pos}/{pos_cap}).",
                )
            )

    sec_cap = rules.max_by_sector.get(candidate.sector)
    if sec_cap is not None:
        same_sec = sum(1 for p in current_roster if p.sector == candidate.sector)
        if same_sec >= sec_cap:
            violations.append(
                RuleViolation(
                    code=RuleViolationCode.SECTOR_MAX_REACHED,
                    message=f"Limite no setor {candidate.sector.value} atingido ({same_sec}/{sec_cap}).",
                )
            )

    if len(current_roster) < rules.roster_size:
        slots_after_this_pick = rules.roster_size - (len(current_roster) + 1)
        sector_counts: Dict[PitchSector, int] = {s: 0 for s in PitchSector}
        for p in current_roster:
            sector_counts[p.sector] += 1
        sector_counts[candidate.sector] += 1

        missing_mandatory = sum(
            max(0, min_req - sector_counts.get(sec, 0))
            for sec, min_req in rules.min_by_sector.items()
        )
        if missing_mandatory > slots_after_this_pick:
            missing_labels = [
                f"{sec.value} (faltam {min_req - (sector_counts.get(sec, 0) - (1 if sec == candidate.sector else 0))})"
                for sec, min_req in rules.min_by_sector.items()
                if (sector_counts.get(sec, 0) - (1 if sec == candidate.sector else 0)) < min_req
            ]
            violations.append(
                RuleViolation(
                    code=RuleViolationCode.REQUIRED_SECTOR_RESERVATION,
                    message=(
                        "Escolha bloqueada para evitar elenco incompleto: as vagas restantes "
                        f"precisam preencher {', '.join(missing_labels)}."
                    ),
                )
            )

    return ValidationResult(eligible=len(violations) == 0, violations=violations)