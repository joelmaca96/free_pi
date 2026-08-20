"""Tests del worker de la cola de scouting bajo demanda (`apps/ingest/worker.py`).

`fetch_opponent_scouting` se mockea siempre: ningún test de la suite hace
peticiones de red (principio del plan de migración).
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from apps.ingest import worker
from packages.baskonia_core.db import models


def _make_job(session, team, status="queued", last_n=5):
    job = models.IngestJob(
        team_id=team.id,
        last_n=last_n,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.flush()
    return job


def test_claim_next_job_marks_running(session, teams):
    """Reclamar un job lo pasa de queued a running y le pone started_at."""
    job = _make_job(session, teams["bilbao"])

    claimed = worker._claim_next_job(session)

    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.started_at is not None


def test_claim_next_job_ignores_non_queued(session, teams):
    """Un job que no está en queued no se reclama."""
    _make_job(session, teams["bilbao"], status="done")

    assert worker._claim_next_job(session) is None


def test_claim_next_job_picks_oldest_first(session, teams):
    """Con varios jobs en cola, se reclama el más antiguo (menor id)."""
    first = _make_job(session, teams["bilbao"])
    _make_job(session, teams["vitoria"])

    claimed = worker._claim_next_job(session)

    assert claimed.id == first.id


def test_process_job_success_marks_done(session, teams, monkeypatch):
    """Si fetch_opponent_scouting no lanza, el job queda done sin error."""
    job = _make_job(session, teams["bilbao"], status="running")
    mock_fetch = MagicMock()
    monkeypatch.setattr(worker, "fetch_opponent_scouting", mock_fetch)

    worker._process_job(session, client=MagicMock(), job=job)

    assert job.status == "done"
    assert job.error is None
    assert job.finished_at is not None
    mock_fetch.assert_called_once()


def test_process_job_failure_marks_failed_with_error(session, teams, monkeypatch):
    """Si fetch_opponent_scouting lanza, el job queda failed con el mensaje."""
    job = _make_job(session, teams["bilbao"], status="running")
    monkeypatch.setattr(
        worker, "fetch_opponent_scouting", MagicMock(side_effect=RuntimeError("slug no resuelto"))
    )

    worker._process_job(session, client=MagicMock(), job=job)

    assert job.status == "failed"
    assert job.error == "slug no resuelto"
    assert job.finished_at is not None
