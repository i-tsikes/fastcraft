"""pyqtgraph curves for two result columns, with an optional second run."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fastcraft.results import PLOT_COLUMNS, RunResult

pg.setConfigOptions(antialias=True)


class PlotPanel(QWidget):
    """Pick X and Y. The cursor time is shared with the mission view."""

    cursor_time_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: RunResult | None = None
        self._overlay: RunResult | None = None
        self._guard = False
        self.x_box = QComboBox()
        self.y_box = QComboBox()
        for name in PLOT_COLUMNS:
            self.x_box.addItem(name)
            self.y_box.addItem(name)
        self.x_box.setCurrentText("time")
        self.y_box.setCurrentText("speed")
        self.x_box.currentTextChanged.connect(self._redraw)
        self.y_box.currentTextChanged.connect(self._redraw)
        overlay = QPushButton("Overlay run…")
        overlay.clicked.connect(self._open_overlay)
        clear = QPushButton("Clear overlay")
        clear.clicked.connect(self._clear_overlay)
        row = QHBoxLayout()
        row.addWidget(QLabel("X"))
        row.addWidget(self.x_box)
        row.addWidget(QLabel("Y"))
        row.addWidget(self.y_box)
        row.addWidget(overlay)
        row.addWidget(clear)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#10141c")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self._curve = self.plot.plot(pen=pg.mkPen("#8ec8ff", width=2))
        self._overlay_curve = self.plot.plot(pen=pg.mkPen("#e0a060", width=1))
        self._marker = self.plot.plot(
            symbol="o",
            symbolSize=8,
            symbolBrush="#ffffff",
            pen=None,
        )
        self._line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#ffffff", width=1))
        self._line.sigPositionChanged.connect(self._line_moved)
        self.plot.addItem(self._line)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.plot)
        self._cursor_t = 0.0

    def set_result(self, result: RunResult | None) -> None:
        self._result = result
        self._redraw()

    def set_cursor_time(self, time_s: float) -> None:
        self._cursor_t = time_s
        self._place_cursor()

    def _redraw(self) -> None:
        self._draw(self._curve, self._result)
        self._draw(self._overlay_curve, self._overlay)
        self._place_cursor()
        x_name = self.x_box.currentText()
        self._line.setVisible(x_name == "time")

    def _draw(self, curve: object, result: RunResult | None) -> None:
        if result is None or result.t.size == 0:
            curve.setData([], [])
            return
        x_values = result.column(self.x_box.currentText())
        y_values = result.column(self.y_box.currentText())
        curve.setData(x_values, y_values)

    def _place_cursor(self) -> None:
        result = self._result
        if result is None or result.t.size == 0:
            self._marker.setData([], [])
            return
        index = _index_at(result.t, self._cursor_t)
        x_value = float(result.column(self.x_box.currentText())[index])
        y_value = float(result.column(self.y_box.currentText())[index])
        self._marker.setData([x_value], [y_value])
        if self.x_box.currentText() == "time" and not self._guard:
            self._guard = True
            self._line.setValue(self._cursor_t)
            self._guard = False

    def _line_moved(self) -> None:
        if self._guard or self.x_box.currentText() != "time":
            return
        self.cursor_time_changed.emit(float(self._line.value()))

    def _open_overlay(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "Overlay a saved run", "", "Playback (*.npz)"
        )
        if not path:
            return
        self._overlay = RunResult.load(Path(path))
        self._redraw()

    def _clear_overlay(self) -> None:
        self._overlay = None
        self._redraw()


def _index_at(times: np.ndarray, time_s: float) -> int:
    index = int(np.searchsorted(times, time_s, side="right") - 1)
    return max(0, min(int(times.size) - 1, index))
