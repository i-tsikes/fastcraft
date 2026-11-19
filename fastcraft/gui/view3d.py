"""Schematic PyVista vehicle and heliocentric paths. No mesh import."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from fastcraft.craft import Craft
from fastcraft.results import RunResult
from fastcraft.scenario import Scenario
from fastcraft.units import AU_M


class View3D(QWidget):
    """Body paths, the spacecraft path, a cursor marker, and a schematic Hauler."""

    def __init__(self, craft: Craft, scenario: Scenario, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._craft = craft
        self._ship = scenario.ship != "none"
        self._result: RunResult | None = None
        self._time = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        try:
            self._build(layout)
            self._failed = False
        except Exception as exc:
            layout.addWidget(QLabel(f"3D view unavailable:\n{exc}"))
            self._failed = True

    def set_result(self, result: RunResult) -> None:
        self._result = result
        if self._failed:
            return
        self._rebuild_paths()
        self.set_cursor_time(float(result.t[-1]) if result.t.size else 0.0)

    def set_cursor_time(self, time_s: float) -> None:
        self._time = time_s
        if self._failed or self._result is None or self._result.t.size == 0:
            return
        index = int(np.searchsorted(self._result.t, time_s, side="right") - 1)
        index = max(0, min(self._result.t.size - 1, index))
        position = self._result.position_m[index] / AU_M
        thrust = self._result.thrust_m[index]
        velocity = self._result.velocity_m_per_s[index]
        direction = thrust if float(np.linalg.norm(thrust)) > 0.0 else velocity
        self._place(self._marker, position, None)
        self._place(self._vehicle, position, direction)
        self._widget.GetRenderWindow().Render()

    def _build(self, layout: QVBoxLayout) -> None:
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance()
        platform = "" if application is None else application.platformName()
        # VTK opens a native window. The offscreen Qt platform aborts that call.
        if platform == "offscreen":
            raise RuntimeError("3D view needs a display platform")
        import pyvista as pv
        import vtkmodules.vtkInteractionStyle  # noqa: F401
        import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
        from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
        from vtkmodules.vtkRenderingCore import vtkRenderer

        self._pv = pv
        self._widget = QVTKRenderWindowInteractor(self)
        layout.addWidget(self._widget)
        self._renderer = vtkRenderer()
        self._renderer.SetBackground(0.03, 0.04, 0.07)
        self._widget.GetRenderWindow().AddRenderer(self._renderer)
        self._vehicle_mesh = _vehicle_mesh(pv, self._craft, self._ship)
        self._vehicle = _actor(self._vehicle_mesh, (0.75, 0.78, 0.82))
        self._marker = _actor(pv.Sphere(radius=0.02, center=(0, 0, 0)), (1.0, 1.0, 1.0))
        self._renderer.AddActor(self._vehicle)
        self._renderer.AddActor(self._marker)
        self._path_actors: list[object] = []
        self._widget.Initialize()
        self._widget.Start()

    def _rebuild_paths(self) -> None:
        for actor in self._path_actors:
            self._renderer.RemoveActor(actor)
        self._path_actors = []
        result = self._result
        if result is None or result.t.size < 2:
            return
        points = result.position_m / AU_M
        mesh = self._pv.lines_from_points(points, close=False)
        actor = _actor(mesh, (0.9, 0.9, 0.95), line=True)
        self._renderer.AddActor(actor)
        self._path_actors.append(actor)
        self._renderer.ResetCamera()

    def _place(self, actor: object, position: np.ndarray, direction: np.ndarray | None) -> None:
        from vtkmodules.vtkCommonTransforms import vtkTransform

        transform = vtkTransform()
        transform.PostMultiply()
        if direction is not None and float(np.linalg.norm(direction)) > 0.0:
            rotation = vtkTransform()
            rotation.SetMatrix(_aim_z(direction))
            transform.Concatenate(rotation)
            length = max(self._craft.geometry.tank_length_m, 1.0)
            transform.Scale(0.04 / length, 0.04 / length, 0.04 / length)
        transform.Translate(float(position[0]), float(position[1]), float(position[2]))
        actor.SetUserTransform(transform)  # type: ignore[attr-defined]


def _vehicle_mesh(pv: object, craft: Craft, ship: bool) -> object:
    length = max(craft.geometry.tank_length_m, 1.0)
    radius = max(craft.geometry.tank_radius_m, 0.2)
    span = max(craft.geometry.radiator_span_m, 0.0)
    parts = [
        pv.Cylinder(center=(0, 0, length / 2), direction=(0, 0, 1), radius=radius, height=length),  # type: ignore[attr-defined]
        pv.Cylinder(
            center=(0, 0, -radius), direction=(0, 0, 1), radius=radius * 0.7, height=radius * 2
        ),  # type: ignore[attr-defined]
    ]
    if span > 0.0:
        wing = (-0.04 * length, 0.04 * length, radius, radius + span, length * 0.2, length * 0.8)
        parts.append(pv.Box(bounds=wing))  # type: ignore[attr-defined]
        other = (
            -0.04 * length,
            0.04 * length,
            -(radius + span),
            -radius,
            length * 0.2,
            length * 0.8,
        )
        parts.append(pv.Box(bounds=other))  # type: ignore[attr-defined]
    for index in range(4):
        angle = index * math.pi / 2.0 + math.pi / 4.0
        center = (1.3 * radius * math.cos(angle), 1.3 * radius * math.sin(angle), -2.0 * radius)
        parts.append(
            pv.Cone(center=center, direction=(0, 0, -1), height=radius, radius=radius * 0.22)
        )  # type: ignore[attr-defined]
    for index in range(6):
        angle = index * math.pi / 3.0
        center = (0.55 * radius * math.cos(angle), 0.55 * radius * math.sin(angle), -2.0 * radius)
        parts.append(
            pv.Cone(center=center, direction=(0, 0, -1), height=radius * 0.4, radius=radius * 0.07)
        )  # type: ignore[attr-defined]
    for index in range(3):
        angle = index * 2.0 * math.pi / 3.0
        center = (0.15 * radius * math.cos(angle), 0.15 * radius * math.sin(angle), -2.0 * radius)
        parts.append(
            pv.Cone(center=center, direction=(0, 0, -1), height=radius * 0.35, radius=radius * 0.06)
        )  # type: ignore[attr-defined]
    if ship:
        nose = (0.0, 0.0, length + radius)
        parts.append(
            pv.Cone(center=nose, direction=(0, 0, 1), height=2.2 * radius, radius=radius * 0.85)
        )  # type: ignore[attr-defined]
    return pv.merge(parts)  # type: ignore[attr-defined]


def _actor(mesh: object, color: tuple[float, float, float], *, line: bool = False) -> object:
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

    poly = mesh if isinstance(mesh, vtkPolyData) else mesh.extract_surface()  # type: ignore[attr-defined]
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(poly)
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*color)
    if line:
        actor.GetProperty().SetRepresentationToWireframe()
        actor.GetProperty().SetLineWidth(2)
    return actor


def _aim_z(direction: np.ndarray) -> object:
    """Rotation that maps local +Z onto `direction`."""
    from vtkmodules.vtkCommonMath import vtkMatrix4x4

    forward = np.asarray(direction, dtype=float)
    forward = forward / np.linalg.norm(forward)
    helper = (
        np.array([0.0, 0.0, 1.0]) if abs(float(forward[2])) < 0.9 else np.array([1.0, 0.0, 0.0])
    )
    x_axis = np.cross(helper, forward)
    x_axis = x_axis / np.linalg.norm(x_axis)
    y_axis = np.cross(forward, x_axis)
    matrix = vtkMatrix4x4()
    for row in range(3):
        matrix.SetElement(row, 0, float(x_axis[row]))
        matrix.SetElement(row, 1, float(y_axis[row]))
        matrix.SetElement(row, 2, float(forward[row]))
    return matrix
