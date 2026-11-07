from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from backend.exporter.router import router as export_router
from backend.multiplayer.router import router as multiplayer_router
from backend.app.api.drafts import router as drafts_router
from backend.app.api.players import router as players_router
from backend.app.core.config import CORS_ORIGINS
from backend.app.engine.draft_state import (
    ConcurrencyConflictError,
    DraftEngineError,
    IneligiblePickError,
)
from backend.app.engine.fallback_autodraft import NoEligiblePlayerError

app = FastAPI(title="DraftFC Local API", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(KeyError)
async def key_error_handler(_: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})


@app.exception_handler(ConcurrencyConflictError)
async def conflict_error_handler(_: Request, exc: ConcurrencyConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(IneligiblePickError)
async def ineligible_pick_handler(_: Request, exc: IneligiblePickError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "detail": str(exc),
            "violations": [v.model_dump() for v in exc.validation.violations],
        },
    )


@app.exception_handler(NoEligiblePlayerError)
async def no_eligible_handler(_: Request, exc: NoEligiblePlayerError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "rejection_counts": exc.rejection_counts},
    )


@app.exception_handler(DraftEngineError)
async def engine_error_handler(_: Request, exc: DraftEngineError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def global_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"Erro interno: {str(exc)}"})


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(players_router)
app.include_router(drafts_router)
app.include_router(export_router, prefix="/api/export")
app.include_router(multiplayer_router)