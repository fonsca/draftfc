import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = Path(os.getenv("DRAFTFC_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = Path(os.getenv("DRAFTFC_DB_PATH", str(DATA_DIR / "draftfc_local.db")))
EXPORTS_DIR = DATA_DIR / "exports"

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]