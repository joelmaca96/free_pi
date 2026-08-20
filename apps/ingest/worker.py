"""Worker de la cola de scouting bajo demanda (`ingest_jobs`).

Consume en bucle secuencial los trabajos que la API encola (`apps/api/routers/jobs.py`)
cuando la SPA pide scoutear un rival sin datos. Un solo job a la vez — no se
paraleliza scraping, coherente con el rate-limit de Basketball-Reference
(`config.REQUEST_DELAY`). Es el único proceso, junto con el resto de
`apps.ingest`, que importa `requests`/`beautifulsoup4`; la API nunca lo hace
(ver `doc/arquitectura/01_design.md` §2).

Uso:
    python -m apps.ingest.worker
"""
import logging
import time
from datetime import datetime, timezone

from packages.baskonia_core import config
from packages.baskonia_core.db import models
from packages.baskonia_core.db.session import create_session_factory

from .pipeline import fetch_opponent_scouting
from .scraper.client import BBRClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

POLL_SECONDS = 3


def _claim_next_job(session) -> "models.IngestJob | None":
    """Reclama el job `queued` más antiguo con un UPDATE condicional.

    El `UPDATE ... WHERE status='queued'` evita que dos workers reclamen el
    mismo job si alguna vez se llega a ejecutar más de una instancia.
    """
    job = (
        session.query(models.IngestJob)
        .filter_by(status="queued")
        .order_by(models.IngestJob.id)
        .first()
    )
    if job is None:
        return None

    updated = (
        session.query(models.IngestJob)
        .filter_by(id=job.id, status="queued")
        .update({"status": "running", "started_at": datetime.now(timezone.utc)})
    )
    session.commit()
    if not updated:
        return None
    session.refresh(job)
    return job


def _process_job(session, client: BBRClient, job: "models.IngestJob") -> None:
    logger.info(
        "Procesando job %s: scouting de %s (last_n=%s)", job.id, job.team.slug, job.last_n
    )
    try:
        fetch_opponent_scouting(session, client, job.team, job.last_n)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo debe quedar registrado en el job
        logger.exception("Job %s falló", job.id)
        job.status = "failed"
        job.error = str(exc)
    else:
        job.status = "done"
        logger.info("Job %s completado", job.id)
    finally:
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


def run_forever(poll_seconds: int = POLL_SECONDS) -> None:
    """Bucle principal: reclama y procesa jobs uno a uno, indefinidamente."""
    session_factory = create_session_factory(config.DATABASE_URL)
    client = BBRClient()
    logger.info("Worker de scouting arrancado (poll cada %ss)", poll_seconds)

    while True:
        session = session_factory()
        try:
            job = _claim_next_job(session)
            if job is None:
                time.sleep(poll_seconds)
                continue
            _process_job(session, client, job)
        finally:
            session.close()


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
