"""Schemas de los trabajos de scouting bajo demanda (`ingest_jobs`)."""
from pydantic import BaseModel

from .teams import TeamRef


class JobResponse(BaseModel):
    """Estado de un trabajo de scouting encolado desde la SPA."""

    id: int
    team: TeamRef
    last_n: int
    status: str  # "queued" | "running" | "done" | "failed"
    error: str | None = None
    created_at: str  # ISO-8601
    started_at: str | None = None
    finished_at: str | None = None
