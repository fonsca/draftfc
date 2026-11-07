import random
import pytest

from backend.app.engine.draft_state import (
    ConcurrencyConflictError,
    DraftSessionEngine,
    simulate_full_draft_viability,
)
from backend.app.engine.fallback_autodraft import FallbackReason
from backend.app.engine.models import DraftPlayer, DraftRulesConfig, DraftStatus
from backend.app.engine.positions import PitchSector


@pytest.fixture
def fallback_pool() -> dict[int, DraftPlayer]:
    return {
        231747: DraftPlayer.from_raw(231747, "K. Mbappé", "ATA", 91, "França", "Real Madrid", "La Liga", club_id=243, league_id=53, value_eur=142_000_000),
        239085: DraftPlayer.from_raw(239085, "E. Haaland", "ATA", 91, "Noruega", "Manchester City", "Premier League", club_id=10, league_id=13, value_eur=148_000_000),
        252371: DraftPlayer.from_raw(252371, "J. Bellingham", "MEI", 90, "Inglaterra", "Real Madrid", "La Liga", club_id=243, league_id=53, value_eur=137_500_000),
        212831: DraftPlayer.from_raw(212831, "Alisson", "GOL", 89, "Brasil", "Liverpool", "Premier League", club_id=9, league_id=13, value_eur=55_000_000),
        192119: DraftPlayer.from_raw(192119, "T. Courtois", "GOL", 89, "Bélgica", "Real Madrid", "La Liga", club_id=243, league_id=53, value_eur=50_000_000),
        230621: DraftPlayer.from_raw(230621, "G. Donnarumma", "GOL", 89, "Itália", "PSG", "Ligue 1", club_id=73, league_id=16, value_eur=60_000_000),
        239818: DraftPlayer.from_raw(239818, "Rúben Dias", "ZAG", 88, "Portugal", "Manchester City", "Premier League", club_id=10, league_id=13, value_eur=80_000_000),
        203376: DraftPlayer.from_raw(203376, "V. van Dijk", "ZAG", 88, "Holanda", "Liverpool", "Premier League", club_id=9, league_id=13, value_eur=45_000_000),
        255253: DraftPlayer.from_raw(255253, "Vitinha", "MC", 88, "Portugal", "PSG", "Ligue 1", club_id=73, league_id=16, value_eur=90_000_000),
        200104: DraftPlayer.from_raw(200104, "H. Son", "PE", 87, "Coreia do Sul", "Tottenham", "Premier League", club_id=18, league_id=13, value_eur=40_000_000),
        235212: DraftPlayer.from_raw(235212, "A. Hakimi", "LD", 87, "Marrocos", "PSG", "Ligue 1", club_id=73, league_id=16, value_eur=70_000_000),
        246669: DraftPlayer.from_raw(246669, "B. Saka", "PD", 88, "Inglaterra", "Arsenal", "Premier League", club_id=1, league_id=13, value_eur=120_000_000),
    }


def test_queue_priority_skips_rule_blocked_and_blocks_double_timeout(
    fallback_pool: dict[int, DraftPlayer],
) -> None:
    rules = DraftRulesConfig(
        roster_size=4,
        max_per_club=1,
        min_by_sector={PitchSector.GK: 1, PitchSector.DEF: 1, PitchSector.MID: 1, PitchSector.FWD: 1},
    )
    eng = DraftSessionEngine(
        room_id="t1",
        edition="fc27",
        rules=rules,
        player_pool=fallback_pool,
        participants=["Pedro", "Mateus"],
        manual_order=["Pedro", "Mateus"],
    )
    eng.make_pick("Pedro", 231747)   # Pick #1: Mbappé (Real Madrid)
    eng.make_pick("Mateus", 239085)  # Pick #2: Haaland
    eng.make_pick("Mateus", 212831)  # Pick #3 (Snake): Alisson

    # Pedro enfileira Bellingham (bloqueado: Real Madrid 1/1) e depois Vitinha (elegível)
    eng.enqueue_player("Pedro", 252371)
    eng.enqueue_player("Pedro", 255253)

    auto_pick = eng.auto_pick_current_turn(expected_overall_pick=4)
    assert auto_pick.auto_drafted is True
    assert auto_pick.player_id == 255253
    assert auto_pick.fallback_reason == FallbackReason.QUEUE_PRIORITY
    assert len(auto_pick.skipped_queue_items) == 1
    assert auto_pick.skipped_queue_items[0].player_id == 252371

    with pytest.raises(ConcurrencyConflictError):
        eng.auto_pick_current_turn(expected_overall_pick=4)


def test_smart_balanced_prevents_gk_hoarding_and_completes_valid_squads(
    fallback_pool: dict[int, DraftPlayer],
) -> None:
    rules = DraftRulesConfig(
        roster_size=6,
        max_per_club=2,
        min_by_sector={PitchSector.GK: 1, PitchSector.DEF: 1, PitchSector.MID: 1, PitchSector.FWD: 1},
    )
    eng = DraftSessionEngine(
        room_id="t2",
        edition="fc27",
        rules=rules,
        player_pool=fallback_pool,
        participants=["Pedro", "Mateus"],
        manual_order=["Pedro", "Mateus"],
    )
    eng.set_auto_pick("Pedro", True)
    eng.set_auto_pick("Mateus", True)
    picks = eng.advance_consecutive_auto_picks()

    assert len(picks) == 12
    assert eng.status == DraftStatus.COMPLETED
    for roster in eng.rosters.values():
        sectors = [p.sector for p in roster]
        assert sectors.count(PitchSector.GK) >= 1
        assert sectors.count(PitchSector.DEF) >= 1
        assert sectors.count(PitchSector.MID) >= 1
        assert sectors.count(PitchSector.FWD) >= 1


def test_budget_guard_and_19k_pool_latency() -> None:
    rng = random.Random(42)
    positions = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    large_pool = {
        pid: DraftPlayer.from_raw(
            player_id=pid,
            short_name=f"Player {pid}",
            primary_position=positions[pid % len(positions)],
            overall=rng.randint(55, 91),
            nationality_name=f"Nation {pid % 80}",
            club_name=f"Club {(pid % 650) + 1}",
            league_name=f"League {(((pid % 650) + 1) % 30) + 1}",
            club_id=(pid % 650) + 1,
            league_id=(((pid % 650) + 1) % 30) + 1,
            value_eur=10_000_000,
        )
        for pid in range(1, 19_001)
    }
    rules = DraftRulesConfig(
        roster_size=18,
        max_per_club=1,
        max_per_league=4,
        max_per_nationality=3,
        min_by_sector={PitchSector.GK: 2, PitchSector.DEF: 5, PitchSector.MID: 5, PitchSector.FWD: 3},
    )
    report = simulate_full_draft_viability(
        rules=rules,
        player_pool=large_pool,
        participants=[f"Amigo_{i}" for i in range(1, 9)],
    )
    assert report.viable is True
    assert report.total_picks_simulated == 144
    assert (report.elapsed_ms / 144.0) < 50.0  # Meta de latência < 50ms por escolha