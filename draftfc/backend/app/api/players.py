from typing import List
from fastapi import APIRouter, Depends

from backend.app.core.database import LocalDraftStore, draft_store
from backend.app.schemas.draft_schemas import EditionSummary

router = APIRouter(prefix="/api", tags=["players"])


def get_store() -> LocalDraftStore:
    return draft_store


@router.get("/editions", response_model=List[EditionSummary])
def list_available_editions(store: LocalDraftStore = Depends(get_store)) -> List[EditionSummary]:
    return store.list_editions()