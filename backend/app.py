import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import assignments, grade, ocr, overrides, uploads
from .config import CORS_ALLOW_ORIGINS, summary

logger = logging.getLogger("backend")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

app = FastAPI()

origins = CORS_ALLOW_ORIGINS or ["http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
    max_age=600,
)

app.include_router(ocr.router)
app.include_router(grade.router)
app.include_router(uploads.router)
app.include_router(overrides.router)
app.include_router(assignments.router)


@app.get("/health")
@app.get("/api/health")
def health():
    return {"ok": True, "service": "graderai"}


@app.get("/api/config")
def config_probe():
    return summary(safe=True)
