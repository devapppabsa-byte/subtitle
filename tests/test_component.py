"""El ciclo de vida de ``PipelineComponent`` debe ser idempotente y seguro."""

from __future__ import annotations

from app.core.component import PipelineComponent


class Dummy(PipelineComponent):
    def __init__(self) -> None:
        super().__init__()
        self.starts = 0
        self.stops = 0

    def _on_start(self) -> None:
        self.starts += 1

    def _on_stop(self, timeout: float | None) -> None:
        self.stops += 1


def test_starts_stopped_and_counts_hooks():
    component = Dummy()
    assert component.running is False

    component.start()
    assert component.running is True
    assert component.starts == 1

    component.stop()
    assert component.running is False
    assert component.stops == 1


def test_start_is_idempotent():
    component = Dummy()
    component.start()
    component.start()
    assert component.starts == 1


def test_stop_is_safe_to_repeat_and_always_releases():
    # El hook se ejecuta aunque el componente ya esté inactivo: una fuente que
    # llegó al final de su audio igual necesita cerrar su archivo o dispositivo.
    # Por eso las implementaciones de ``_on_stop`` deben ser idempotentes.
    component = Dummy()
    component.start()
    component.stop()
    component.stop()
    assert component.running is False
    assert component.stops == 2


def test_stop_after_natural_end_still_releases_resources():
    component = Dummy()
    component.start()
    component._mark_inactive()
    component.stop()
    assert component.stops == 1


def test_context_manager_starts_and_stops():
    with Dummy() as component:
        assert component.running is True
    assert component.running is False
    assert component.starts == 1
    assert component.stops == 1


def test_mark_inactive_flags_natural_end_without_releasing():
    component = Dummy()
    component.start()
    component._mark_inactive()
    assert component.running is False
    assert component.stops == 0
