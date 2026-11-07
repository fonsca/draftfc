import re
import zlib
from typing import Any, Dict, List, Optional, Set, Tuple
from lxml import html as lxml_html
from scraper.config import EDITION_CONFIG, INTERNATIONAL_LEAGUE_IDS

ROSTER_RE = re.compile(r"[?&]r=(\d{6})\b")
ID_IN_PATH_RE = re.compile(r"/(?:player|team)/(\d+)(?:[/?#]|$)")
LEAGUE_ID_RE = re.compile(r"(?:/league/|[?&]lg=)(\d+)(?:[/?#&]|$)")
NATION_ID_RE = re.compile(r"(?:/nation/|[?&]na=)(\d+)(?:[/?#&]|$)")
OFFSET_RE = re.compile(r"[?&]offset=(\d+)\b")

INTERNATIONAL_LEAGUE_NAMES = {
    "international",
    "internacional",
    "international women",
    "internacional feminino",
    "friendly international",
    "amistosos internacionais",
}
FREE_AGENT_MARKERS = ("free", "grátis", "gratis", "livre", "sem clube")


def _extract_first_int(text: str) -> Optional[int]:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def _select_main_table_rows(doc: lxml_html.HtmlElement) -> List[lxml_html.HtmlElement]:
    tables = doc.xpath("//article//table[.//td[@data-col='oa']]")
    if not tables:
        tables = doc.xpath("//table[not(ancestor::aside) and .//td[@data-col='oa']]")
    if not tables:
        return []
    return tables[0].xpath(".//tbody/tr[.//td[@data-col='oa']]")


def extract_next_offset(doc: lxml_html.HtmlElement, current_offset: int) -> Optional[int]:
    higher_offsets: Set[int] = set()
    for href in doc.xpath("//a[not(ancestor::aside) and contains(@href, 'offset=')]/@href"):
        m = OFFSET_RE.search(href)
        if m:
            val = int(m.group(1))
            if val > current_offset:
                higher_offsets.add(val)
    return min(higher_offsets) if higher_offsets else None


def discover_latest_roster_id(html_text: str, edition: str) -> str:
    cfg = EDITION_CONFIG[edition]
    prefix = cfg["roster_prefix"]
    doc = lxml_html.fromstring(html_text)
    candidates = set()

    for opt in doc.xpath("//select//option"):
        for attr in ("value", "href"):
            val = (opt.get(attr) or "").strip()
            m_url = ROSTER_RE.search(val)
            if m_url and m_url.group(1).startswith(prefix):
                candidates.add(m_url.group(1))
            elif re.fullmatch(rf"{prefix}\d{{4}}", val):
                candidates.add(val)

    for link in doc.xpath("//a[contains(@href, 'r=')]"):
        m_link = ROSTER_RE.search(link.get("href", ""))
        if m_link and m_link.group(1).startswith(prefix):
            candidates.add(m_link.group(1))

    return max(candidates) if candidates else cfg["default_roster"]


def parse_teams_page(html_text: str, current_offset: int = 0) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    doc = lxml_html.fromstring(html_text)
    rows = _select_main_table_rows(doc)
    teams: List[Dict[str, Any]] = []

    for row in rows:
        team_links = row.xpath(".//a[contains(@href, '/team/')]")
        if not team_links:
            continue
        team_a = team_links[0]
        m_team = ID_IN_PATH_RE.search(team_a.get("href", ""))
        if not m_team:
            continue

        club_id = int(m_team.group(1))
        club_name = (team_a.text_content() or "").strip()

        league_id = 0
        league_name = "Unknown League"

        league_links = row.xpath(".//a[contains(@href, '/league/') or contains(@href, 'lg=')]")
        for lg_a in league_links:
            txt = (lg_a.text_content() or "").strip()
            m_lg = LEAGUE_ID_RE.search(lg_a.get("href", ""))
            if m_lg and league_id == 0:
                league_id = int(m_lg.group(1))
            if txt:
                league_name = txt
                break

        if league_name == "Unknown League":
            team_tds = row.xpath(".//td[.//a[contains(@href, '/team/')]]")
            if team_tds:
                t_td = team_tds[0]
                for other_a in t_td.xpath(".//a[not(contains(@href, '/team/'))]"):
                    txt = (other_a.text_content() or "").strip()
                    if txt:
                        league_name = txt
                        break
                if league_name == "Unknown League":
                    for sub in t_td.xpath(".//*[contains(@class, 'sub')]"):
                        txt = (sub.text_content() or "").strip()
                        if txt:
                            league_name = txt
                            break

        if league_name != "Unknown League" and league_id == 0:
            league_id = zlib.crc32(league_name.encode("utf-8"))

        if (
            league_id in INTERNATIONAL_LEAGUE_IDS
            or league_name.strip().lower() in INTERNATIONAL_LEAGUE_NAMES
        ):
            continue

        oa_cells = row.xpath(".//td[@data-col='oa']")
        overall = _extract_first_int(oa_cells[0].text_content()) if oa_cells else None

        imgs = row.xpath(".//img")
        logo_url = (imgs[0].get("data-src") or imgs[0].get("src")) if imgs else None

        teams.append(
            {
                "club_id": club_id,
                "club_name": club_name,
                "league_id": league_id,
                "league_name": league_name,
                "overall": overall,
                "logo_url": logo_url,
            }
        )

    next_offset = extract_next_offset(doc, current_offset=current_offset)
    return teams, next_offset


def parse_players_page(html_text: str, current_offset: int = 0) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    doc = lxml_html.fromstring(html_text)
    rows = _select_main_table_rows(doc)
    players: List[Dict[str, Any]] = []

    for row in rows:
        player_links = row.xpath(".//a[contains(@href, '/player/')]")
        if not player_links:
            continue
        p_a = player_links[0]
        p_href = p_a.get("href", "")

        pi_cells = row.xpath(".//td[@data-col='pi']")
        player_id = _extract_first_int(pi_cells[0].text_content()) if pi_cells else None
        if not player_id:
            m_pid = ID_IN_PATH_RE.search(p_href)
            if m_pid:
                player_id = int(m_pid.group(1))
        if not player_id:
            continue

        short_name = (p_a.text_content() or "").strip()
        full_name = (
            p_a.get("aria-label")
            or p_a.get("data-tippy-content")
            or p_a.get("title")
            or short_name
        ).strip()

        pos_nodes = row.xpath(".//span[contains(@class, 'pos')]")
        positions = [n.text_content().strip() for n in pos_nodes if n.text_content().strip()]

        def col_text(code: str) -> str:
            nodes = row.xpath(f".//td[@data-col='{code}']")
            return nodes[0].text_content().strip() if nodes else ""

        age = _extract_first_int(col_text("ae")) or 20
        overall = _extract_first_int(col_text("oa")) or 50
        potential = _extract_first_int(col_text("pt")) or overall

        nat_imgs = row.xpath(".//img[contains(@class, 'flag')]")
        nationality_name = "Unknown"
        if nat_imgs:
            nationality_name = (nat_imgs[0].get("title") or nat_imgs[0].get("alt") or "Unknown").strip()

        nationality_id = None
        nat_links = row.xpath(".//a[contains(@href, 'na=') or contains(@href, '/nation/')]")
        if nat_links:
            m_na = NATION_ID_RE.search(nat_links[0].get("href", ""))
            if m_na:
                nationality_id = int(m_na.group(1))

        candidate_club_ids: List[int] = []
        primary_club_id: Optional[int] = None
        primary_club_name = "Free Agents"

        for t_a in row.xpath(".//a[contains(@href, '/team/')]"):
            parent_text = (t_a.getparent().text_content() or "").strip().lower()
            if any(marker in parent_text for marker in FREE_AGENT_MARKERS):
                continue
            m_cid = ID_IN_PATH_RE.search(t_a.get("href", ""))
            if m_cid:
                cid = int(m_cid.group(1))
                candidate_club_ids.append(cid)
                if primary_club_id is None:
                    primary_club_id = cid
                    primary_club_name = (t_a.text_content() or "").strip()

        face_imgs = row.xpath(".//img[contains(@class, 'player-check') or contains(@data-src, '/players/')]")
        face_url = (face_imgs[0].get("data-src") or face_imgs[0].get("src")) if face_imgs else None

        players.append(
            {
                "player_id": player_id,
                "short_name": short_name,
                "full_name": full_name,
                "positions": positions,
                "age": age,
                "overall": overall,
                "potential": potential,
                "nationality_id": nationality_id,
                "nationality_name": nationality_name,
                "club_id": primary_club_id,
                "club_name": primary_club_name,
                "candidate_club_ids": candidate_club_ids,
                "value_raw": col_text("vl"),
                "wage_raw": col_text("wg"),
                "preferred_foot": col_text("pf") or None,
                "face_url": face_url,
            }
        )

    next_offset = extract_next_offset(doc, current_offset=current_offset)
    return players, next_offset