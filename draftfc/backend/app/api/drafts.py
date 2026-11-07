from typing import List, Optional
from fastapi import APIRouter, Depends, Query

from backend.app.core.database import LocalDraftStore, draft_store
from backend.app.engine.draft_state import ViabilitySimulationReport, simulate_full_draft_viability
from backend.app.engine.positions import PitchSector
from backend.app.schemas.draft_schemas import (
    AutoPickModeRequest,
    AutoPickRequest,
    CreateRoomRequest,
    FilterOptionsResponse,
    MakePickRequest,
    PlayerPoolPageResponse,
    QueueMutateRequest,
    QueueReorderRequest,
    RoomStateSnapshot,
    RoomSummaryItem,
    ViabilityCheckRequest,
)
from backend.app.api.players import get_store

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


@router.get("", response_model=List[RoomSummaryItem])
def list_saved_rooms(store: LocalDraftStore = Depends(get_store)) -> List[RoomSummaryItem]:
    return store.list_rooms()


@router.post("/simulate-viability", response_model=ViabilitySimulationReport)
def simulate_viability(
    req: ViabilityCheckRequest,
    store: LocalDraftStore = Depends(get_store),
) -> ViabilitySimulationReport:
    bundle = store.get_pool_bundle(req.edition, req.gender)
    return simulate_full_draft_viability(
        rules=req.rules,
        player_pool=bundle.pool,
        participants=req.participants,
        edition=req.edition,
        strategy=req.fallback_strategy,
    )


@router.post("", response_model=RoomStateSnapshot)
def create_draft_room(
    req: CreateRoomRequest,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.create_room(req)
    return store.build_room_snapshot(managed)


@router.get("/{room_id}", response_model=RoomStateSnapshot)
def get_draft_room(room_id: str, store: LocalDraftStore = Depends(get_store)) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    return store.build_room_snapshot(managed)


@router.get("/{room_id}/filter-options", response_model=FilterOptionsResponse)
def get_room_filter_options(
    room_id: str,
    store: LocalDraftStore = Depends(get_store),
) -> FilterOptionsResponse:
    managed = store.get_room(room_id)
    return store.get_pool_bundle(managed.engine.edition, managed.gender).filter_options


@router.get("/{room_id}/players", response_model=PlayerPoolPageResponse)
def search_room_players(
    room_id: str,
    q: Optional[str] = None,
    sector: Optional[PitchSector] = None,
    position: Optional[str] = None,
    league_name: Optional[str] = None,
    club_name: Optional[str] = None,
    nationality_name: Optional[str] = None,
    min_ovr: Optional[int] = Query(default=None, ge=1, le=99),
    max_ovr: Optional[int] = Query(default=None, ge=1, le=99),
    hide_picked: bool = True,
    only_eligible: bool = False,
    for_participant: Optional[str] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    store: LocalDraftStore = Depends(get_store),
) -> PlayerPoolPageResponse:
    managed = store.get_room(room_id)
    return store.query_room_players(
        managed=managed,
        q=q,
        sector=sector,
        position=position,
        league_name=league_name,
        club_name=club_name,
        nationality_name=nationality_name,
        min_ovr=min_ovr,
        max_ovr=max_ovr,
        hide_picked=hide_picked,
        only_eligible=only_eligible,
        for_participant=for_participant,
        offset=offset,
        limit=limit,
    )


@router.post("/{room_id}/pick", response_model=RoomStateSnapshot)
def make_room_pick(
    room_id: str,
    req: MakePickRequest,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    eng = managed.engine
    actor = req.actor_participant_id or (eng.current_slot.participant_id if eng.current_slot else "")
    eng.make_pick(
        actor_participant_id=actor,
        player_id=req.player_id,
        expected_overall_pick=req.expected_overall_pick,
        expected_state_version=req.expected_state_version,
        as_commissioner=req.as_commissioner,
    )
    eng.advance_consecutive_auto_picks()
    managed.reset_turn_clock()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/auto-pick", response_model=RoomStateSnapshot)
def trigger_room_auto_pick(
    room_id: str,
    req: AutoPickRequest,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    eng = managed.engine
    eng.auto_pick_current_turn(
        expected_overall_pick=req.expected_overall_pick,
        strategy_override=req.strategy_override,
    )
    eng.advance_consecutive_auto_picks()
    managed.reset_turn_clock()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/undo", response_model=RoomStateSnapshot)
def undo_room_last_pick(
    room_id: str,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.engine.undo_last_pick()
    managed.reset_turn_clock()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/pause", response_model=RoomStateSnapshot)
def pause_room(room_id: str, store: LocalDraftStore = Depends(get_store)) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.freeze_clock_on_pause()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/resume", response_model=RoomStateSnapshot)
def resume_room(room_id: str, store: LocalDraftStore = Depends(get_store)) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.unfreeze_clock_on_resume()
    if managed.engine.advance_consecutive_auto_picks():
        managed.reset_turn_clock()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/reset-clock", response_model=RoomStateSnapshot)
def reset_room_clock(
    room_id: str,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.reset_turn_clock()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/participants/{participant_id}/auto-pick-mode", response_model=RoomStateSnapshot)
def set_participant_auto_pick_mode(
    room_id: str,
    participant_id: str,
    req: AutoPickModeRequest,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.engine.set_auto_pick(participant_id, req.enabled)
    if req.enabled and managed.engine.advance_consecutive_auto_picks():
        managed.reset_turn_clock()
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.post("/{room_id}/participants/{participant_id}/queue", response_model=RoomStateSnapshot)
def enqueue_for_participant(
    room_id: str,
    participant_id: str,
    req: QueueMutateRequest,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.engine.enqueue_player(participant_id, req.player_id)
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.delete(
    "/{room_id}/participants/{participant_id}/queue/{player_id}",
    response_model=RoomStateSnapshot,
)
def remove_from_participant_queue(
    room_id: str,
    participant_id: str,
    player_id: int,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.engine.remove_from_queue(participant_id, player_id)
    store.persist_room(managed)
    return store.build_room_snapshot(managed)


@router.put("/{room_id}/participants/{participant_id}/queue/reorder", response_model=RoomStateSnapshot)
def reorder_participant_queue(
    room_id: str,
    participant_id: str,
    req: QueueReorderRequest,
    store: LocalDraftStore = Depends(get_store),
) -> RoomStateSnapshot:
    managed = store.get_room(room_id)
    managed.engine.reorder_queue(participant_id, req.ordered_player_ids)
    store.persist_room(managed)
    return store.build_room_snapshot(managed)