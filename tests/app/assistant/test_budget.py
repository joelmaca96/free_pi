"""Tests de los topes de uso (§11.3) y del registro de votos.

La app está publicada por el túnel de Cloudflare: cualquiera que dé con la URL
puede consumir. Con capa gratuita el riesgo no es la factura, es agotar la
cuota del proveedor y quedarte sin asistente — así que estos topes existen
desde el primer día aunque hoy no se pague nada.

Lo que más se prueba aquí es la degradación: si no hay directorio escribible
(en la Raspberry Pi `data/` está montado de solo lectura), el contador debe
seguir funcionando en memoria en vez de tumbar la página.
"""
import datetime as dt
import json

import pytest

from app.assistant import budget, feedback


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_STATE_DIR", str(tmp_path))
    budget.reset_memory_counter()
    yield
    budget.reset_memory_counter()


TODAY = dt.date(2026, 8, 24)


def test_a_fresh_session_is_allowed(monkeypatch):
    status = budget.check(0, today=TODAY)
    assert status.allowed
    assert status.daily_used == 0


def test_the_session_cap_stops_at_the_limit(monkeypatch):
    monkeypatch.setenv("ASSISTANT_SESSION_LIMIT", "3")
    assert budget.check(2, today=TODAY).allowed
    blocked = budget.check(3, today=TODAY)
    assert not blocked.allowed
    assert "3 preguntas de esta sesión" in blocked.reason


def test_the_daily_cap_counts_across_sessions(monkeypatch):
    monkeypatch.setenv("ASSISTANT_DAILY_LIMIT", "2")
    budget.record(today=TODAY)
    budget.record(today=TODAY)

    blocked = budget.check(0, today=TODAY)
    assert not blocked.allowed
    assert "tope diario" in blocked.reason


def test_the_daily_counter_resets_the_next_day(monkeypatch):
    monkeypatch.setenv("ASSISTANT_DAILY_LIMIT", "1")
    budget.record(today=TODAY)
    assert not budget.check(0, today=TODAY).allowed
    assert budget.check(0, today=TODAY + dt.timedelta(days=1)).allowed


def test_the_counter_persists_to_disk(tmp_path):
    budget.record(today=TODAY)
    budget.record(today=TODAY)

    saved = json.loads((tmp_path / "assistant_usage.json").read_text(encoding="utf-8"))
    assert saved == {"date": TODAY.isoformat(), "count": 2}


def test_without_a_writable_directory_the_counter_degrades_to_memory(monkeypatch):
    """En la Pi, `data/` está montado de solo lectura a propósito. Un contador
    que se pierde al reiniciar es molesto; una pestaña que no arranca, no."""
    monkeypatch.delenv("ASSISTANT_STATE_DIR", raising=False)
    monkeypatch.setenv("ASSISTANT_DAILY_LIMIT", "2")

    assert budget.record(today=TODAY) == 1
    assert budget.record(today=TODAY) == 2
    assert not budget.check(0, today=TODAY).allowed


def test_a_corrupt_counter_file_does_not_break_anything(tmp_path):
    (tmp_path / "assistant_usage.json").write_text("esto no es json", encoding="utf-8")
    assert budget.check(0, today=TODAY).allowed


# ------------------------------------------------------------------ feedback --


def test_a_vote_is_appended_as_one_json_line(tmp_path):
    assert feedback.record(
        question="¿qué tal jugó Howard?",
        answer="19 puntos.",
        helpful=False,
        tools=["resolve_entity", "player_game"],
        unverified_numbers=["47"],
        provider="groq · llama",
    )
    assert feedback.record(question="otra", answer="x", helpful=True, tools=[])

    lines = (tmp_path / "assistant_feedback.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    # Lo importante no es el voto, es con QUÉ herramientas se construyó la
    # respuesta: eso es lo que convierte un pulgar abajo en un caso de prueba.
    assert first["tools"] == ["resolve_entity", "player_game"]
    assert first["helpful"] is False
    assert first["provider"] == "groq · llama"


def test_a_vote_without_a_writable_directory_is_dropped_not_raised(monkeypatch):
    monkeypatch.delenv("ASSISTANT_STATE_DIR", raising=False)
    assert feedback.record(question="x", answer="y", helpful=True, tools=[]) is False
