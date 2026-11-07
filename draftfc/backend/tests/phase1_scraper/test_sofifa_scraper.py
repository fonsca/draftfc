from pathlib import Path
from scraper.normalizer import ClubRecord, normalize_player_row, parse_currency_eur
from scraper.parser import discover_latest_roster_id, parse_players_page, parse_teams_page
from scraper.storage import DraftStorage

SAMPLE_ROSTER_HTML = """
<html><body>
<select name="roster">
  <option value="/players?r=270008&set=true">FC 27 Oct 29, 2026</option>
  <option value="/players?r=270001&set=true">FC 27 Sep 20, 2026</option>
  <option value="/players?r=260042&set=true">FC 26 Aug 15, 2026</option>
</select>
</body></html>
"""

SAMPLE_TEAMS_HTML = """
<html><body>
<table><tbody>
  <tr>
    <td><img data-src="https://cdn.sofifa.net/meta/team/3468/60.png" /></td>
    <td>
      <a href="/team/243/real-madrid/">Real Madrid</a>
      <a href="/teams?lg=53">La Liga</a>
    </td>
    <td data-col="oa"><span>86</span></td>
  </tr>
</tbody></table>
</body></html>
"""

SAMPLE_PLAYERS_HTML = """
<html><body>
<table><tbody>
  <tr>
    <td><img class="player-check" data-src="https://cdn.sofifa.net/players/231/747/27_120.png" /></td>
    <td class="col-name">
      <a href="/players?na=18"><img class="flag" title="France" /></a>
      <a href="/player/231747/kylian-mbappe/270008/" aria-label="Kylian Mbappé Lottin">K. Mbappé</a>
      <div><span class="pos">ST</span> <span class="pos">LW</span></div>
    </td>
    <td data-col="ae">27</td>
    <td data-col="oa"><span>91</span></td>
    <td data-col="pt"><span>92</span></td>
    <td><a href="/team/243/real-madrid/">Real Madrid</a><div class="sub">2024 ~ 2029</div></td>
    <td data-col="vl">€185.5M</td>
    <td data-col="wg">€380K</td>
    <td data-col="pf">Right</td>
    <td data-col="pi">231747</td>
  </tr>
  <tr>
    <td><img class="player-check" data-src="https://cdn.sofifa.net/players/192/985/27_120.png" /></td>
    <td class="col-name">
      <a href="/players?na=7"><img class="flag" title="Belgium" /></a>
      <a href="/player/192985/kevin-de-bruyne/270008/" aria-label="Kevin De Bruyne">K. De Bruyne</a>
      <div><span class="pos">CM</span> <span class="pos">CAM</span></div>
    </td>
    <td data-col="ae">35</td>
    <td data-col="oa"><span>87</span></td>
    <td data-col="pt"><span>87</span></td>
    <td><div class="sub">Free</div></td>
    <td data-col="vl">€0</td>
    <td data-col="wg">€0</td>
    <td data-col="pf">Right</td>
    <td data-col="pi">192985</td>
  </tr>
</tbody></table>
</body></html>
"""


def test_discover_roster_fc26_and_fc27() -> None:
    assert discover_latest_roster_id(SAMPLE_ROSTER_HTML, "fc27") == "270008"
    assert discover_latest_roster_id(SAMPLE_ROSTER_HTML, "fc26") == "260042"


def test_currency_parser() -> None:
    assert parse_currency_eur("€185.5M") == 185_500_000
    assert parse_currency_eur("€380K") == 380_000
    assert parse_currency_eur("€0") == 0


def test_end_to_end_parse_normalize_and_sqlite_upsert(tmp_path: Path) -> None:
    storage = DraftStorage(
        db_path=tmp_path / "test.db",
        raw_dir=tmp_path / "raw",
        processed_dir=tmp_path / "processed",
    )

    teams_raw, _ = parse_teams_page(SAMPLE_TEAMS_HTML)
    clubs = [ClubRecord(edition="fc27", roster_id="270008", **t) for t in teams_raw]
    storage.upsert_clubs(clubs)
    clubs_map = storage.load_clubs_map("fc27")

    players_raw, _ = parse_players_page(SAMPLE_PLAYERS_HTML)
    normalized = [
        normalize_player_row(p, edition="fc27", roster_id="270008", gender="male", clubs_map=clubs_map)
        for p in players_raw
    ]
    storage.upsert_players(normalized)
    # Reexecução idempotente não pode duplicar registros
    storage.upsert_players(normalized)

    csv_file = storage.export_edition_csv("fc27")
    lines = csv_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3  # Header + 2 jogadores únicos
    assert normalized[0].league_name == "La Liga"
    assert normalized[1].club_name == "Free Agents"