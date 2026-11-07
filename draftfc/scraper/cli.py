import argparse
import logging
from typing import Dict, List
from scraper.config import (
    DB_PATH,
    EDITION_CONFIG,
    PAGE_SIZE,
    PROCESSED_DIR,
    RAW_DIR,
    SOFIFA_BASE_URL,
    build_players_url,
    build_teams_url,
)
from scraper.http_client import SoFifaHttpClient
from scraper.normalizer import ClubRecord, PlayerRecord, normalize_player_row
from scraper.parser import discover_latest_roster_id, parse_players_page, parse_teams_page
from scraper.storage import DraftStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("draftfc.cli")


def run_edition_scrape(
    edition: str,
    gender: str,
    client: SoFifaHttpClient,
    storage: DraftStorage,
    explicit_roster: str | None = None,
    max_pages: int | None = None,
    resume: bool = False,
    refresh_clubs: bool = False,
) -> None:
    checkpoint = storage.load_checkpoint(edition, gender) if resume else None

    if explicit_roster:
        roster_id = explicit_roster
    elif checkpoint:
        roster_id = checkpoint["roster_id"]
    else:
        logger.info("Descobrindo roster mais recente para %s...", EDITION_CONFIG[edition]["label"])
        landing_html = client.get_html(f"{SOFIFA_BASE_URL}/players")
        roster_id = discover_latest_roster_id(landing_html, edition)

    logger.info("Edição: %s | Roster ID: %s | Gênero: %s", edition, roster_id, gender)

    clubs_map: Dict[int, ClubRecord] = storage.load_clubs_map(edition)
    has_valid_clubs = len(clubs_map) >= 300 and any(
        c.league_name != "Unknown League" for c in clubs_map.values()
    )

    if refresh_clubs or not has_valid_clubs:
        logger.info("Coletando tabela completa de clubes e ligas (%s)...", edition)
        t_offset = 0
        t_page = 0
        while True:
            t_url = build_teams_url(roster_id=roster_id, offset=t_offset, gender=gender)
            t_html = client.get_html(t_url)
            raw_teams, next_t_offset = parse_teams_page(t_html, current_offset=t_offset)
            if not raw_teams:
                break

            batch_clubs = [ClubRecord(edition=edition, roster_id=roster_id, **t) for t in raw_teams]
            storage.upsert_clubs(batch_clubs)
            for c in batch_clubs:
                clubs_map[c.club_id] = c

            t_page += 1
            logger.info("Clubes página %d concluída (%d clubes acumulados).", t_page, len(clubs_map))
            if next_t_offset is None:
                break
            t_offset = next_t_offset
    else:
        logger.info("Usando %d clubes já mapeados em cache no SQLite.", len(clubs_map))

    p_offset = checkpoint["next_offset"] if checkpoint else 0
    p_page = 0
    total_saved = 0

    while True:
        p_url = build_players_url(roster_id=roster_id, offset=p_offset, gender=gender)
        p_html = client.get_html(p_url)
        raw_players, next_p_offset = parse_players_page(p_html, current_offset=p_offset)
        if not raw_players:
            break

        normalized_batch: List[PlayerRecord] = [
            normalize_player_row(
                raw=raw_p,
                edition=edition,
                roster_id=roster_id,
                gender=gender,
                clubs_map=clubs_map,
            )
            for raw_p in raw_players
        ]

        saved = storage.upsert_players(normalized_batch)
        total_saved += saved
        p_page += 1

        checkpoint_offset = next_p_offset if next_p_offset is not None else (p_offset + PAGE_SIZE)
        storage.save_checkpoint(edition=edition, gender=gender, roster_id=roster_id, next_offset=checkpoint_offset)

        logger.info(
            "[%s] Página %d salva (%d jogadores nesta página | %d acumulados | próximo offset=%s).",
            edition.upper(),
            p_page,
            saved,
            total_saved,
            next_p_offset,
        )

        if next_p_offset is None or (max_pages and p_page >= max_pages):
            break
        p_offset = next_p_offset

    if not max_pages:
        storage.clear_checkpoint(edition, gender)

    csv_path = storage.export_edition_csv(edition)
    logger.info("Extração %s finalizada! CSV exportado em: %s", edition.upper(), csv_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper SoFIFA - DraftFC (EA FC 26 & 27)")
    parser.add_argument("--edition", choices=["fc26", "fc27", "both"], default="fc27")
    parser.add_argument("--gender", choices=["male", "female", "all"], default="male")
    parser.add_argument("--roster-id", type=str, default=None, help="Ex: 270006 ou 260042")
    parser.add_argument("--max-pages", type=int, default=None, help="Limita páginas de jogadores para teste")
    parser.add_argument("--resume", action="store_true", help="Retoma a partir do último checkpoint")
    parser.add_argument("--refresh-clubs", action="store_true", help="Força recoleta da tabela de clubes")
    args = parser.parse_args()

    editions = ["fc26", "fc27"] if args.edition == "both" else [args.edition]
    storage = DraftStorage(db_path=DB_PATH, raw_dir=RAW_DIR, processed_dir=PROCESSED_DIR)
    client = SoFifaHttpClient()

    try:
        for ed in editions:
            run_edition_scrape(
                edition=ed,
                gender=args.gender,
                client=client,
                storage=storage,
                explicit_roster=args.roster_id,
                max_pages=args.max_pages,
                resume=args.resume,
                refresh_clubs=args.refresh_clubs,
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()