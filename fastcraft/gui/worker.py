"""Background integration. The solver stays off the UI thread."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal

from fastcraft.craft import Craft
from fastcraft.ephemeris import Ephemeris
from fastcraft.phasing import refine_epoch
from fastcraft.propagate import propagate
from fastcraft.scenario import Scenario


class PropagateThread(QThread):
    """Run one scenario. Cancel is checked between segments and chunks."""

    progressed = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, scenario: Scenario, craft: Craft, ephemeris: Ephemeris) -> None:
        super().__init__()
        self._scenario = scenario
        self._craft = craft
        self._ephemeris = ephemeris
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = propagate(
                self._scenario,
                self._craft,
                self._ephemeris,
                cancel=lambda: self._cancel,
                on_update=self.progressed.emit,
                chunk_s=3_600.0,
            )
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class RefineThread(QThread):
    """One-dimensional epoch search. The result is not written until accepted."""

    finished_ok = Signal(object, float)
    failed = Signal(str)

    def __init__(
        self,
        scenario: Scenario,
        craft: Craft,
        ephemeris: Ephemeris,
        center: datetime,
    ) -> None:
        super().__init__()
        self._scenario = scenario
        self._craft = craft
        self._ephemeris = ephemeris
        self._center = center
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            epoch, score = refine_epoch(
                self._scenario,
                self._craft,
                self._ephemeris,
                self._center,
                cancel=lambda: self._cancel,
            )
            self.finished_ok.emit(epoch, score)
        except Exception as exc:
            self.failed.emit(str(exc))
