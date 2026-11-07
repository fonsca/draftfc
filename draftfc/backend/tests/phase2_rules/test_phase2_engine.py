import pytest
from backend.app.engine.draft_lottery import build_pick_schedule, draw_draft_order
from backend.app.engine.draft_state import (
    ConcurrencyConflictError,
    DraftSessionEngine,
    OutOfTurnError,
)
from backend.app.engine.models import (
    DraftFormat,
    DraftPlayer,
    DraftRulesConfig,
    OvrTierCap,
    RuleViolationCode,
)
from backend.app.engine.positions import PitchSector
from backend.app.engine.rules_validator import validate_candidate_pick


@pytest.fixture
def sample_pool() -> dict[int, DraftPlayer]:
    return {
        239085: DraftPlayer.from_raw(239085, "E. Haaland", "ATA", 91, "Noruega", "Manchester City", "Premier League", club_id=10, league_id=13),
        231747: DraftPlayer.from_raw(231747, "K. Mbappé", "ATA", 91, "França", "Real Madrid", "La Liga", club_id=243, league_id=53),
        252371: DraftPlayer.from_raw(252371, "J. Bellingham", "MEI", 90, "Inglaterra", "Real Madrid", "La Liga", club_id=243, league_id=53),
        212831: DraftPlayer.from_raw(212831, "Alisson", "GOL", 89, "Brasil", "Liverpool", "Premier League", club_id=9, league_id=13),
        239818: DraftPlayer.from_raw(239818, "Rúben Dias", "ZAG", 88, "Portugal", "Manchester City", "Premier League", club_id=10, league_id=13),
        192985: DraftPlayer.from_raw(192985, "K. De Bruyne", "MC", 87, "Bélgica", "Free Agents", "Free Agents", club_id=None, league_id=None),
        158023: DraftPlayer.from_raw(158023, "L. Messi", "PD", 86, "Argentina", "Free Agents", "Free Agents", club_id=None, league_id=None),
        200104: DraftPlayer.from_raw(200104, "H. Son", "LW", 86, "Coreia do Sul", "Tottenham", "Premier League", club_id=18, league_id=13),
    }


def test_auditable_lottery_and_schedules() -> None:
    friends = ["mateus", "lucas", "pedro", "gabriel"]
    r1 = draw_draft_order(friends, seed="liga-sexta-2026")
    r2 = draw_draft_order(list(reversed(friends)), seed="liga-sexta-2026")
    assert r1.ordered_participant_ids == r2.ordered_participant_ids
    assert r1.verification_hash == r2.verification_hash

    snake = build_pick_schedule(["A", "B", "C"], roster_size=2, draft_format=DraftFormat.SNAKE)
    assert [s.participant_id for s in snake] == ["A", "B", "C", "C", "B", "A"]

    trr = build_pick_schedule(["A", "B", "C"], roster_size=4, draft_format=DraftFormat.THIRD_ROUND_REVERSAL)
    assert [s.participant_id for s in trr] == ["A", "B", "C", "C", "B", "A", "C", "B", "A", "A", "B", "C"]


def test_club_tier_and_free_agent_rules(sample_pool: dict[int, DraftPlayer]) -> None:
    rules = DraftRulesConfig(
        roster_size=4,
        max_per_club=1,
        min_by_sector={PitchSector.GK: 1, PitchSector.DEF: 1, PitchSector.MID: 1, PitchSector.FWD: 1},
        ovr_tier_caps=[OvrTierCap(min_ovr=90, max_ovr=99, max_players=1)],
    )
    res_bell = validate_candidate_pick(sample_pool[252371], [sample_pool[231747]], {231747}, rules)
    codes = {v.code for v in res_bell.violations}
    assert not res_bell.eligible
    assert RuleViolationCode.CLUB_LIMIT_REACHED in codes
    assert RuleViolationCode.OVR_TIER_LIMIT_REACHED in codes

    res_fa = validate_candidate_pick(sample_pool[158023], [sample_pool[192985]], {192985}, rules)
    assert res_fa.eligible


def test_sector_reservation_blocks_broken_roster(sample_pool: dict[int, DraftPlayer]) -> None:
    rules = DraftRulesConfig(
        roster_size=3,
        max_per_club=2,
        min_by_sector={PitchSector.GK: 1, PitchSector.DEF: 1, PitchSector.FWD: 1},
    )
    res_son = validate_candidate_pick(sample_pool[200104], [sample_pool[239085]], {239085}, rules)
    assert not res_son.eligible
    assert RuleViolationCode.REQUIRED_SECTOR_RESERVATION in {v.code for v in res_son.violations}

    res_gk = validate_candidate_pick(sample_pool[212831], [sample_pool[239085]], {239085}, rules)
    assert res_gk.eligible


def test_turn_security_queues_and_lossless_undo(sample_pool: dict[int, DraftPlayer]) -> None:
    rules = DraftRulesConfig(
        roster_size=3,
        max_per_club=1,
        min_by_sector={PitchSector.GK: 0, PitchSector.DEF: 0, PitchSector.MID: 0, PitchSector.FWD: 0},
    )
    engine = DraftSessionEngine(
        room_id="sala-local",
        edition="fc27",
        rules=rules,
        player_pool=sample_pool,
        participants=["mateus", "lucas"],
        manual_order=["mateus", "lucas"],
    )

    engine.enqueue_player("mateus", 231747)
    engine.enqueue_player("mateus", 252371)
    engine.enqueue_player("lucas", 231747)
    engine.enqueue_player("lucas", 239085)

    with pytest.raises(OutOfTurnError):
        engine.make_pick("lucas", 239085)

    engine.make_pick("mateus", 231747, expected_overall_pick=1)

    with pytest.raises(ConcurrencyConflictError):
        engine.make_pick("mateus", 239085, expected_overall_pick=1, as_commissioner=True)

    # Mbappé saiu automaticamente da fila de Lucas; próximo elegível passa a ser Haaland
    assert engine.peek_next_eligible_queued_player("lucas").player_id == 239085

    # Bellingham permanece na fila de Mateus, porém sinalizado como bloqueado (Real Madrid 1/1)
    mateus_status = engine.inspect_participant_queue("mateus")
    assert len(mateus_status) == 1 and not mateus_status[0].eligible_now

    # Desfazer restaura Mbappé na posição #1 da fila de ambos
    engine.undo_last_pick()
    assert engine.peek_next_eligible_queued_player("lucas").player_id == 231747
    assert engine.current_slot.overall_pick == 1