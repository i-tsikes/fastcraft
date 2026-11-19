"""Top-down heliocentric mission view and the playback window."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QWidget,
)

from fastcraft.craft import Craft
from fastcraft.ephemeris import Ephemeris
from fastcraft.gui.plots import PlotPanel
from fastcraft.gui.view3d import View3D
from fastcraft.results import RunResult, playback_path
from fastcraft.scenario import Scenario
from fastcraft.units import (
    AU_M,
    MARS_EQUATORIAL_RADIUS_M,
    PHASE_ACCELERATE,
    PHASE_DECELERATE,
    PHASE_DEPARTURE,
    PHASE_INSERTION,
    WGS84_EQUATORIAL_RADIUS_M,
    format_elapsed,
)


class MissionView(QWidget):
    """Earth, Mars, the Sun, orbit tracks, and the Hauler glyph."""

    def __init__(self, ephemeris: Ephemeris, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ephemeris = ephemeris
        self.result: RunResult | None = None
        self.time_s = 0.0
        self.system_view = False
        self.setMinimumSize(640, 480)
        self._orbits = _sample_orbits(ephemeris)

    def set_result(self, result: RunResult) -> None:
        self.result = result
        self.update()

    def set_time(self, time_s: float) -> None:
        self.time_s = time_s
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(6, 8, 14))
        index = _index(self.result, self.time_s)
        hauler = None if self.result is None else self.result.position_m[index, :2]
        earth = self.ephemeris.state("earth", self.time_s)[0][:2]
        mars = self.ephemeris.state("mars", self.time_s)[0][:2]
        center, span = self._frame(hauler, earth, mars, index)
        width = max(self.width(), 1)
        height = max(self.height(), 1)
        scale = min((width - 48) / span, (height - 48) / span)

        def project(xy: np.ndarray) -> tuple[float, float]:
            return (
                width / 2 + (float(xy[0]) - center[0]) * scale,
                height / 2 - (float(xy[1]) - center[1]) * scale,
            )

        pen = QPen(QColor(90, 96, 110))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        for body in ("earth", "mars"):
            points = self._orbits[body]
            path = QPainterPath()
            sx, sy = project(points[0])
            path.moveTo(sx, sy)
            for point in points[1:]:
                sx, sy = project(point)
                path.lineTo(sx, sy)
            painter.drawPath(path)
        if self.result is not None and index >= 0:
            trail = QPen(QColor(235, 235, 235))
            trail.setWidthF(1.0)
            painter.setPen(trail)
            path = QPainterPath()
            sx, sy = project(self.result.position_m[0, :2])
            path.moveTo(sx, sy)
            for point in self.result.position_m[1 : index + 1, :2]:
                sx, sy = project(point)
                path.lineTo(sx, sy)
            painter.drawPath(path)
        earth_r = max(12.0, 0.018 * min(width, height))
        mars_r = earth_r * (MARS_EQUATORIAL_RADIUS_M / WGS84_EQUATORIAL_RADIUS_M)
        sun_r = 2.0 * earth_r
        _disk(painter, project(np.zeros(2)), sun_r, QColor(240, 200, 40))
        _disk(painter, project(earth), earth_r, QColor(70, 130, 220))
        _disk(painter, project(mars), mars_r, QColor(190, 60, 50))
        if hauler is not None and self.result is not None:
            thrust = self.result.thrust_m[index]
            velocity = self.result.velocity_m_per_s[index]
            phase = int(self.result.phase[index])
            _hauler(painter, project(hauler), thrust, velocity, phase, earth_r * 0.85)
        painter.end()

    def _frame(
        self,
        hauler: np.ndarray | None,
        earth: np.ndarray,
        mars: np.ndarray,
        index: int,
    ) -> tuple[np.ndarray, float]:
        phase = 0 if self.result is None else int(self.result.phase[index])
        if not self.system_view and hauler is not None and phase == PHASE_DEPARTURE:
            return earth, 0.08 * AU_M
        if not self.system_view and hauler is not None and phase == PHASE_INSERTION:
            return mars, 0.08 * AU_M
        points = [np.zeros(2), earth, mars, *self._orbits["earth"], *self._orbits["mars"]]
        if self.result is not None:
            points.extend(self.result.position_m[:, :2])
        stack = np.vstack(points)
        low = stack.min(axis=0)
        high = stack.max(axis=0)
        center = (low + high) / 2.0
        span = float(max(high[0] - low[0], high[1] - low[1], AU_M))
        return center, span * 1.08


def _disk(painter: QPainter, center: tuple[float, float], radius: float, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(
        int(center[0] - radius), int(center[1] - radius), int(radius * 2), int(radius * 2)
    )


def _hauler(
    painter: QPainter,
    center: tuple[float, float],
    thrust: np.ndarray,
    velocity: np.ndarray,
    phase: int,
    size: float,
) -> None:
    if float(np.linalg.norm(thrust)) > 1.0:
        direction = thrust
    elif float(np.linalg.norm(velocity)) > 0.0:
        direction = velocity
    else:
        direction = np.array([1.0, 0.0, 0.0])
    angle = -math.degrees(math.atan2(float(direction[1]), float(direction[0])))
    painter.save()
    painter.translate(center[0], center[1])
    painter.rotate(angle)
    firing = float(np.linalg.norm(thrust)) > 1.0
    if firing and phase in (PHASE_DEPARTURE, PHASE_INSERTION):
        plume = QPainterPath()
        plume.moveTo(-size * 0.2, 0)
        plume.lineTo(-size * 1.3, size * 0.28)
        plume.lineTo(-size * 1.3, -size * 0.28)
        plume.closeSubpath()
        painter.setBrush(QColor(80, 160, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(plume)
    elif firing and phase in (PHASE_ACCELERATE, PHASE_DECELERATE):
        painter.setPen(QPen(QColor(90, 170, 255), max(1.0, size * 0.08)))
        for offset in (-0.22, 0.0, 0.22):
            painter.drawLine(
                int(-size * 0.15), int(size * offset), int(-size * 0.7), int(size * offset)
            )
    path = QPainterPath()
    path.moveTo(size, 0)
    path.lineTo(-size * 0.7, size * 0.5)
    path.lineTo(-size * 0.3, 0)
    path.lineTo(-size * 0.7, -size * 0.5)
    path.closeSubpath()
    painter.setPen(QPen(QColor(255, 255, 255), 1))
    painter.setBrush(QColor(245, 245, 245))
    painter.drawPath(path)
    painter.restore()


def _sample_orbits(ephemeris: Ephemeris) -> dict[str, np.ndarray]:
    orbits: dict[str, np.ndarray] = {}
    for body, days in (("earth", 365.25), ("mars", 687.0)):
        times = np.linspace(0.0, days * 86_400.0, 361)
        orbits[body] = np.vstack([ephemeris.state(body, float(time))[0][:2] for time in times])
    return orbits


def _index(result: RunResult | None, time_s: float) -> int:
    if result is None or result.t.size == 0:
        return 0
    index = int(np.searchsorted(result.t, time_s, side="right") - 1)
    return max(0, min(result.t.size - 1, index))


class MissionWindow(QMainWindow):
    """2D mission view, readout, plots, schematic 3D, and playback."""

    def __init__(
        self,
        scenario: Scenario,
        craft: Craft,
        ephemeris: Ephemeris,
        scenario_path: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.scenario = scenario
        self.scenario_path = scenario_path
        self.ephemeris = ephemeris
        self.result: RunResult | None = None
        self._thread: object | None = None
        self._guard = False
        self.setWindowTitle(f"Mission — {scenario.name}")
        self.resize(1200, 800)
        self.view = MissionView(ephemeris)
        self.setCentralWidget(self.view)
        self._readout = _Readout()
        dock = QDockWidget("Hauler", self)
        dock.setWidget(self._readout)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.plots = PlotPanel()
        plot_dock = QDockWidget("Plots", self)
        plot_dock.setWidget(self.plots)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, plot_dock)
        try:
            self.view3d: QWidget = View3D(craft, scenario)
        except Exception as exc:
            self.view3d = QLabel(str(exc))
        view_dock = QDockWidget("3D", self)
        view_dock.setWidget(self.view3d)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, view_dock)
        self.plots.cursor_time_changed.connect(self._from_plot)
        self._build_controls()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._playing = False

    def start(self, thread: object) -> None:
        self._thread = thread
        thread.progressed.connect(self._on_progress)  # type: ignore[attr-defined]
        thread.finished_ok.connect(self._on_finished)  # type: ignore[attr-defined]
        thread.failed.connect(self._on_failed)  # type: ignore[attr-defined]
        self.statusBar().showMessage("Integrating")
        thread.start()  # type: ignore[attr-defined]

    def show_playback(self, result: RunResult) -> None:
        self._apply_result(result, follow_end=False)
        self._prime_rate()
        self._show(float(result.t[0]))

    def _build_controls(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        self._play = QPushButton("Play")
        self._play.clicked.connect(self._toggle_play)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._cancel_run)
        self._rate = QDoubleSpinBox()
        self._rate.setRange(1.0, 1.0e12)
        self._rate.setDecimals(0)
        self._rate.setValue(288_000.0)
        self._system = QCheckBox("Whole system")
        self._system.toggled.connect(self._set_system)
        row.addWidget(self._play)
        row.addWidget(self._cancel)
        row.addWidget(QLabel("sim s / wall s"))
        row.addWidget(self._rate)
        row.addWidget(self._system)
        row.addStretch(1)
        self.statusBar().addPermanentWidget(bar)

    def _on_progress(self, result: RunResult) -> None:
        self._apply_result(result, follow_end=True)

    def _on_finished(self, result: RunResult) -> None:
        self._apply_result(result, follow_end=False)
        self._cancel.setEnabled(False)
        self._prime_rate()
        if result.cancelled:
            self.statusBar().showMessage("Cancelled. The partial run was not saved.")
            return
        if self.scenario_path is not None:
            path = playback_path(self.scenario_path)
            result.save(path)
            self.statusBar().showMessage(f"Saved {path.name}")
        else:
            self.statusBar().showMessage("Run finished. Save the scenario to keep a playback file.")
        self._show(float(result.t[0]))

    def _on_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._cancel.setEnabled(False)

    def _apply_result(self, result: RunResult, *, follow_end: bool) -> None:
        self.result = result
        self.view.set_result(result)
        self.plots.set_result(result)
        if isinstance(self.view3d, View3D):
            self.view3d.set_result(result)
        if follow_end and result.t.size:
            self._show(float(result.t[-1]))

    def _show(self, time_s: float) -> None:
        if self._guard:
            return
        self._guard = True
        self.view.set_time(time_s)
        self.plots.set_cursor_time(time_s)
        if isinstance(self.view3d, View3D):
            self.view3d.set_cursor_time(time_s)
        self._readout.update_state(self.result, self.ephemeris, time_s)
        self._guard = False

    def _from_plot(self, time_s: float) -> None:
        self._show(time_s)

    def _toggle_play(self) -> None:
        if self.result is None:
            return
        self._playing = not self._playing
        self._play.setText("Pause" if self._playing else "Play")
        if self._playing:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        result = self.result
        if result is None or result.t.size == 0:
            return
        nxt = self.view.time_s + float(self._rate.value()) * 0.033
        end = float(result.t[-1])
        if nxt >= end:
            nxt = end
            self._playing = False
            self._play.setText("Play")
            self._timer.stop()
        self._show(nxt)

    def _cancel_run(self) -> None:
        thread = self._thread
        if thread is not None and hasattr(thread, "cancel"):
            thread.cancel()

    def _set_system(self, checked: bool) -> None:
        self.view.system_view = checked
        self.view.update()

    def _prime_rate(self) -> None:
        result = self.result
        if result is None or result.t.size == 0:
            return
        duration = float(result.t[-1] - result.t[0])
        self._rate.setValue(max(duration / 30.0, 1.0))

    def closeEvent(self, event: object) -> None:  # noqa: N802
        self._cancel_run()
        super().closeEvent(event)  # type: ignore[arg-type]


class _Readout(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._elapsed = QLabel("0d 0h 0m 0s")
        self._speed = QLabel("—")
        self._accel = QLabel("—")
        self._earth = QLabel("—")
        self._mars = QLabel("—")
        form = QFormLayout(self)
        form.addRow("Elapsed", self._elapsed)
        form.addRow("Speed", self._speed)
        form.addRow("Acceleration", self._accel)
        form.addRow("Range from Earth", self._earth)
        form.addRow("Range from Mars", self._mars)

    def update_state(self, result: RunResult | None, ephemeris: Ephemeris, time_s: float) -> None:
        if result is None or result.t.size == 0:
            return
        index = _index(result, time_s)
        self._elapsed.setText(format_elapsed(float(result.t[index] - result.t[0])))
        speed = float(result.speed_m_per_s[index])
        self._speed.setText(f"{speed:,.1f} m/s")
        self._accel.setText(f"{_acceleration(result, index):.4g} m/s²")
        position = result.position_m[index]
        earth = ephemeris.state("earth", float(result.t[index]))[0]
        mars = ephemeris.state("mars", float(result.t[index]))[0]
        self._earth.setText(_range_text(float(np.linalg.norm(position - earth))))
        self._mars.setText(_range_text(float(np.linalg.norm(position - mars))))


def _acceleration(result: RunResult, index: int) -> float:
    if result.t.size < 2:
        return 0.0
    left = max(index - 1, 0)
    right = min(index + 1, result.t.size - 1)
    dt = float(result.t[right] - result.t[left])
    if dt <= 0.0:
        return 0.0
    delta = result.velocity_m_per_s[right] - result.velocity_m_per_s[left]
    return float(np.linalg.norm(delta) / dt)


def _range_text(meters: float) -> str:
    return f"{meters / 1000.0:,.0f} km ({meters / AU_M:.3f} AU)"
