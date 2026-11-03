"""Craft and scenario editors. Widgets write back into the ruamel tree."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fastcraft.craft import Craft
from fastcraft.phasing import DateChoice
from fastcraft.presets import PresetPlan
from fastcraft.units import circular_speed_m_per_s, format_elapsed
from fastcraft.yamlio import loads_yaml

NEW_CRAFT = """\
schema: fastcraft.craft/v1
name: Hauler
# Dry mass is one number. It excludes tanks and the docked Starship.
dry_mass_kg: 150000
tanks:
  water_kg: 400000
  lh2_kg: 30000
  lox_kg: 240000
starship:
  dry_mass_kg: 85000
  # Reserved for landing. The Hauler never burns this.
  landing_propellant_kg: 120000
power:
  reactor_thermal_w: 1.0e9
  electric_w: 2.0e8
  radiator_reject_w: 8.0e8
electrolysis:
  max_kg_per_s: 0.5
engines:
  hydrolox:
    count: 4
    thrust_n: 2.2e6
    isp_s: 380
  mhd:
    count: 9
    thrust_n: 250
    isp_s: 3000
geometry:
  tank_length_m: 50
  tank_radius_m: 4.5
  radiator_span_m: 40
notes: Water-fueled nuclear hauler. Starship docks on the forward face.
"""

NEW_SCENARIO = """\
schema: fastcraft.scenario/v1
name: Earth to Mars
craft: ""
epoch: 2035-04-01T00:00:00Z
frame: heliocentric_ecliptic_j2000
bodies:
  - sun
  - earth
  - mars
initial:
  mode: earth_circular_equatorial
  agl_m: 400000
  longitude_deg: 0
  propellant:
    water_kg: 400000
    lh2_kg: 30000
    lox_kg: 240000
arrival:
  body: mars
  agl_m: 400000
ship: docked
burns:
  - role: departure
    duration_s: 180
    throttle: 1
  - role: accelerate
    duration_s: 3600
    throttle: 1
    oxygen: "off"
  - role: decelerate
    duration_s: 3600
    throttle: 1
    oxygen: "off"
  - role: insertion
    duration_s: 180
    throttle: 1
electrolysis: "off"
notes: Outbound from a circular equatorial Earth orbit. Starship stays docked.
"""


def new_craft() -> Any:
    return loads_yaml(NEW_CRAFT)


def new_scenario() -> Any:
    return loads_yaml(NEW_SCENARIO)


class CraftForm(QWidget):
    """Editors for every craft field. Unknown keys are left untouched."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: Any = None
        self._filling = False
        self.name = QLineEdit()
        self.dry = QLineEdit()
        self.water = QLineEdit()
        self.lh2 = QLineEdit()
        self.lox = QLineEdit()
        self.ship_dry = QLineEdit()
        self.landing = QLineEdit()
        self.thermal = QLineEdit()
        self.electric = QLineEdit()
        self.radiator = QLineEdit()
        self.electrolysis = QLineEdit()
        self.h_count = QLineEdit()
        self.h_thrust = QLineEdit()
        self.h_isp = QLineEdit()
        self.m_count = QLineEdit()
        self.m_thrust = QLineEdit()
        self.m_isp = QLineEdit()
        self.oxygen_on = QCheckBox("Oxygen thrusters")
        self.o_count = QLineEdit()
        self.o_thrust = QLineEdit()
        self.o_isp = QLineEdit()
        self.length = QLineEdit()
        self.radius = QLineEdit()
        self.span = QLineEdit()
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(72)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Dry mass kg", self.dry)
        form.addRow("Water tank kg", self.water)
        form.addRow("LH2 tank kg", self.lh2)
        form.addRow("LOX tank kg", self.lox)
        form.addRow("Starship dry kg", self.ship_dry)
        form.addRow("Landing propellant kg", self.landing)
        form.addRow("Reactor thermal W", self.thermal)
        form.addRow("Electric W", self.electric)
        form.addRow("Radiator reject W", self.radiator)
        form.addRow("Electrolysis kg/s", self.electrolysis)
        form.addRow("Hydrolox count", self.h_count)
        form.addRow("Hydrolox thrust N", self.h_thrust)
        form.addRow("Hydrolox Isp s", self.h_isp)
        form.addRow("MHD count", self.m_count)
        form.addRow("MHD thrust N", self.m_thrust)
        form.addRow("MHD Isp s", self.m_isp)
        form.addRow(self.oxygen_on)
        form.addRow("Oxygen count", self.o_count)
        form.addRow("Oxygen thrust N", self.o_thrust)
        form.addRow("Oxygen Isp s", self.o_isp)
        form.addRow("Tank length m", self.length)
        form.addRow("Tank radius m", self.radius)
        form.addRow("Radiator span m", self.span)
        form.addRow("Notes", self.notes)
        for widget in self._edits():
            widget.textChanged.connect(self._emit)
        self.notes.textChanged.connect(self._emit)
        self.oxygen_on.toggled.connect(self._emit)
        self.oxygen_on.toggled.connect(self._sync_oxygen)
        inner = QWidget()
        inner.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        self._sync_oxygen()

    def document(self) -> Any:
        return self._data

    def load_data(self, data: Any) -> None:
        self._data = data
        self._filling = True
        self.name.setText(str(data.get("name", "")))
        self.dry.setText(_fmt(data.get("dry_mass_kg", 0)))
        tanks = data.get("tanks", {})
        self.water.setText(_fmt(_get(tanks, "water_kg")))
        self.lh2.setText(_fmt(_get(tanks, "lh2_kg")))
        self.lox.setText(_fmt(_get(tanks, "lox_kg")))
        ship = data.get("starship", {})
        self.ship_dry.setText(_fmt(_get(ship, "dry_mass_kg")))
        self.landing.setText(_fmt(_get(ship, "landing_propellant_kg")))
        power = data.get("power", {})
        self.thermal.setText(_fmt(_get(power, "reactor_thermal_w")))
        self.electric.setText(_fmt(_get(power, "electric_w")))
        self.radiator.setText(_fmt(_get(power, "radiator_reject_w")))
        electrolysis = data.get("electrolysis", {})
        self.electrolysis.setText(_fmt(_get(electrolysis, "max_kg_per_s")))
        engines = data.get("engines", {})
        self._load_engine(engines.get("hydrolox", {}), self.h_count, self.h_thrust, self.h_isp)
        self._load_engine(engines.get("mhd", {}), self.m_count, self.m_thrust, self.m_isp)
        oxygen = engines.get("oxygen") if isinstance(engines, Mapping) else None
        self.oxygen_on.setChecked(isinstance(oxygen, Mapping))
        self._load_engine(oxygen or {}, self.o_count, self.o_thrust, self.o_isp)
        geometry = data.get("geometry", {})
        self.length.setText(_fmt(_get(geometry, "tank_length_m")))
        self.radius.setText(_fmt(_get(geometry, "tank_radius_m")))
        self.span.setText(_fmt(_get(geometry, "radiator_span_m")))
        self.notes.setPlainText(str(data.get("notes", "")))
        self._sync_oxygen()
        self._filling = False

    def apply(self) -> list[str]:
        data = self._data
        if not isinstance(data, Mapping):
            return ["craft: document is missing"]
        errors: list[str] = []
        data["name"] = self.name.text()
        errors.extend(_put_float(data, "dry_mass_kg", self.dry, "dry_mass_kg"))
        errors.extend(_put_float(data["tanks"], "water_kg", self.water, "tanks.water_kg"))
        errors.extend(_put_float(data["tanks"], "lh2_kg", self.lh2, "tanks.lh2_kg"))
        errors.extend(_put_float(data["tanks"], "lox_kg", self.lox, "tanks.lox_kg"))
        errors.extend(
            _put_float(data["starship"], "dry_mass_kg", self.ship_dry, "starship.dry_mass_kg")
        )
        errors.extend(
            _put_float(
                data["starship"],
                "landing_propellant_kg",
                self.landing,
                "starship.landing_propellant_kg",
            )
        )
        errors.extend(
            _put_float(data["power"], "reactor_thermal_w", self.thermal, "power.reactor_thermal_w")
        )
        errors.extend(_put_float(data["power"], "electric_w", self.electric, "power.electric_w"))
        errors.extend(
            _put_float(data["power"], "radiator_reject_w", self.radiator, "power.radiator_reject_w")
        )
        errors.extend(
            _put_float(
                data["electrolysis"],
                "max_kg_per_s",
                self.electrolysis,
                "electrolysis.max_kg_per_s",
            )
        )
        errors.extend(
            self._store_engine(data["engines"], "hydrolox", self.h_count, self.h_thrust, self.h_isp)
        )
        errors.extend(
            self._store_engine(data["engines"], "mhd", self.m_count, self.m_thrust, self.m_isp)
        )
        engines = data["engines"]
        if self.oxygen_on.isChecked():
            if "oxygen" not in engines or not isinstance(engines["oxygen"], Mapping):
                engines["oxygen"] = {"count": 0, "thrust_n": 0, "isp_s": 0}
            errors.extend(
                self._store_engine(engines, "oxygen", self.o_count, self.o_thrust, self.o_isp)
            )
        elif "oxygen" in engines:
            del engines["oxygen"]
        errors.extend(
            _put_float(data["geometry"], "tank_length_m", self.length, "geometry.tank_length_m")
        )
        errors.extend(
            _put_float(data["geometry"], "tank_radius_m", self.radius, "geometry.tank_radius_m")
        )
        errors.extend(
            _put_float(data["geometry"], "radiator_span_m", self.span, "geometry.radiator_span_m")
        )
        data["notes"] = self.notes.toPlainText()
        return errors

    def _edits(self) -> tuple[QLineEdit, ...]:
        return (
            self.name,
            self.dry,
            self.water,
            self.lh2,
            self.lox,
            self.ship_dry,
            self.landing,
            self.thermal,
            self.electric,
            self.radiator,
            self.electrolysis,
            self.h_count,
            self.h_thrust,
            self.h_isp,
            self.m_count,
            self.m_thrust,
            self.m_isp,
            self.o_count,
            self.o_thrust,
            self.o_isp,
            self.length,
            self.radius,
            self.span,
        )

    def _emit(self, *_args: object) -> None:
        if not self._filling:
            self.changed.emit()

    def _sync_oxygen(self, *_args: object) -> None:
        enabled = self.oxygen_on.isChecked()
        for widget in (self.o_count, self.o_thrust, self.o_isp):
            widget.setEnabled(enabled)

    @staticmethod
    def _load_engine(
        engine: Mapping[str, Any] | dict[str, Any],
        count: QLineEdit,
        thrust: QLineEdit,
        isp: QLineEdit,
    ) -> None:
        count.setText(_fmt(_get(engine, "count")))
        thrust.setText(_fmt(_get(engine, "thrust_n")))
        isp.setText(_fmt(_get(engine, "isp_s")))

    @staticmethod
    def _store_engine(
        engines: Any,
        name: str,
        count: QLineEdit,
        thrust: QLineEdit,
        isp: QLineEdit,
    ) -> list[str]:
        engine = engines[name]
        return [
            *_put_int(engine, "count", count, f"engines.{name}.count"),
            *_put_float(engine, "thrust_n", thrust, f"engines.{name}.thrust_n"),
            *_put_float(engine, "isp_s", isp, f"engines.{name}.isp_s"),
        ]


class ScenarioForm(QWidget):
    """Scenario fields, four burn rows, presets, and the departure-date list."""

    changed = Signal()
    preset_requested = Signal(str)
    find_dates_requested = Signal()
    refine_requested = Signal()
    accept_epoch_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: Any = None
        self._filling = False
        self._directory: Path | None = None
        self._refined: datetime | None = None
        self.name = QLineEdit()
        self.craft = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_craft)
        craft_row = QWidget()
        craft_layout = QHBoxLayout(craft_row)
        craft_layout.setContentsMargins(0, 0, 0, 0)
        craft_layout.addWidget(self.craft)
        craft_layout.addWidget(browse)
        self.epoch = QLineEdit()
        self.sun = QCheckBox("sun")
        self.earth = QCheckBox("earth")
        self.mars = QCheckBox("mars")
        bodies = QWidget()
        body_row = QHBoxLayout(bodies)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.addWidget(self.sun)
        body_row.addWidget(self.earth)
        body_row.addWidget(self.mars)
        self.ship = QComboBox()
        for label, value in (
            ("Docked", "docked"),
            ("No Ship", "none"),
            ("Empty Ship", "empty"),
            ("Loaded Ship", "loaded"),
        ):
            self.ship.addItem(label, value)
        self.cargo = QLineEdit()
        self.mode = QComboBox()
        self.agl = QLineEdit()
        self.longitude = QLineEdit()
        self.speed = QLabel("—")
        self.position = [QLineEdit(), QLineEdit(), QLineEdit()]
        self.velocity = [QLineEdit(), QLineEdit(), QLineEdit()]
        self.water = QLineEdit()
        self.lh2 = QLineEdit()
        self.lox = QLineEdit()
        self.arrival_body = QComboBox()
        self.arrival_body.addItems(["mars", "earth"])
        self.arrival_agl = QLineEdit()
        self.electrolysis = QComboBox()
        self.electrolysis.addItems(["off", "on"])
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(64)
        self.burns = (
            _BurnRow("departure", False),
            _BurnRow("accelerate", True),
            _BurnRow("decelerate", True),
            _BurnRow("insertion", False),
        )
        self.dates = QListWidget()
        self.dates.setFixedHeight(110)
        self.refined_label = QLabel("Pick a date, then refine. Accept writes the epoch only.")
        self.refined_label.setWordWrap(True)
        self.accept_button = QPushButton("Accept epoch")
        self.accept_button.setEnabled(False)
        self.accept_button.clicked.connect(self.accept_epoch_requested.emit)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Craft", craft_row)
        form.addRow("Epoch", self.epoch)
        form.addRow("Bodies", bodies)
        form.addRow("Ship", self.ship)
        form.addRow("Cargo kg", self.cargo)
        form.addRow("Initial", self.mode)
        form.addRow("Altitude agl m", self.agl)
        form.addRow("Longitude deg", self.longitude)
        form.addRow("Circular speed", self.speed)
        position_row = _triple(self.position)
        velocity_row = _triple(self.velocity)
        form.addRow("Position m", position_row)
        form.addRow("Velocity m/s", velocity_row)
        self._position_label = form.labelForField(position_row)
        self._velocity_label = form.labelForField(velocity_row)
        self._agl_label = form.labelForField(self.agl)
        self._longitude_label = form.labelForField(self.longitude)
        self._speed_label = form.labelForField(self.speed)
        self._cargo_label = form.labelForField(self.cargo)
        form.addRow("Water kg", self.water)
        form.addRow("LH2 kg", self.lh2)
        form.addRow("LOX kg", self.lox)
        form.addRow("Arrival", self.arrival_body)
        form.addRow("Arrival agl m", self.arrival_agl)
        form.addRow("Electrolysis", self.electrolysis)
        form.addRow("Notes", self.notes)
        buttons = QHBoxLayout()
        for label, kind in (
            ("Fastest", "fastest"),
            ("Return no Ship", "return_none"),
            ("Return empty Ship", "return_empty"),
            ("Return loaded Ship", "return_loaded"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, name=kind: self.preset_requested.emit(name)
            )
            buttons.addWidget(button)
        date_buttons = QHBoxLayout()
        find = QPushButton("Find dates")
        find.clicked.connect(self.find_dates_requested.emit)
        refine = QPushButton("Refine selected")
        refine.clicked.connect(self.refine_requested.emit)
        date_buttons.addWidget(find)
        date_buttons.addWidget(refine)
        date_buttons.addWidget(self.accept_button)
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.addLayout(form)
        for row in self.burns:
            column.addWidget(row)
            row.changed.connect(self._emit)
        column.addLayout(buttons)
        column.addLayout(date_buttons)
        column.addWidget(self.dates)
        column.addWidget(self.refined_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        for widget in (
            self.name,
            self.craft,
            self.epoch,
            self.cargo,
            self.agl,
            self.longitude,
            self.water,
            self.lh2,
            self.lox,
            self.arrival_agl,
            *self.position,
            *self.velocity,
        ):
            widget.textChanged.connect(self._emit)
        self.notes.textChanged.connect(self._emit)
        for box in (self.sun, self.earth, self.mars):
            box.toggled.connect(self._emit)
        self.ship.currentIndexChanged.connect(self._emit)
        self.ship.currentIndexChanged.connect(self._sync_cargo)
        self.mode.currentIndexChanged.connect(self._on_mode)
        self.arrival_body.currentIndexChanged.connect(self._emit)
        self.electrolysis.currentIndexChanged.connect(self._emit)
        self.agl.textChanged.connect(self._update_speed)
        self._fill_modes("earth_circular_equatorial")
        self._sync_mode_widgets()
        self._sync_cargo()

    def document(self) -> Any:
        return self._data

    def set_directory(self, path: Path | None) -> None:
        self._directory = path

    def load_data(self, data: Any) -> None:
        self._data = data
        self._filling = True
        self.name.setText(str(data.get("name", "")))
        self.craft.setText(str(data.get("craft", "")))
        self.epoch.setText(_epoch_text(data.get("epoch", "")))
        bodies = list(data.get("bodies", []))
        self.sun.setChecked("sun" in bodies)
        self.earth.setChecked("earth" in bodies)
        self.mars.setChecked("mars" in bodies)
        ship = str(data.get("ship", "docked"))
        index = self.ship.findData(ship)
        self.ship.setCurrentIndex(index if index >= 0 else 0)
        self.cargo.setText(_fmt(data.get("cargo_kg", 0)))
        initial = data.get("initial", {})
        mode = str(initial.get("mode", "earth_circular_equatorial"))
        self._fill_modes(mode)
        self.agl.setText(_fmt(initial.get("agl_m", 400000)))
        self.longitude.setText(_fmt(initial.get("longitude_deg", 0)))
        position = list(initial.get("position_m", [0, 0, 0]))
        velocity = list(initial.get("velocity_m_per_s", [0, 0, 0]))
        for widget, value in zip(self.position, position, strict=False):
            widget.setText(_fmt(value))
        for widget, value in zip(self.velocity, velocity, strict=False):
            widget.setText(_fmt(value))
        propellant = initial.get("propellant", {})
        self.water.setText(_fmt(_get(propellant, "water_kg")))
        self.lh2.setText(_fmt(_get(propellant, "lh2_kg")))
        self.lox.setText(_fmt(_get(propellant, "lox_kg")))
        arrival = data.get("arrival", {})
        body_index = self.arrival_body.findText(str(arrival.get("body", "mars")))
        self.arrival_body.setCurrentIndex(body_index if body_index >= 0 else 0)
        self.arrival_agl.setText(_fmt(arrival.get("agl_m", 400000)))
        electrolysis = str(data.get("electrolysis", "off"))
        self.electrolysis.setCurrentIndex(0 if electrolysis != "on" else 1)
        self.notes.setPlainText(str(data.get("notes", "")))
        burns = list(data.get("burns", []))
        for row, burn in zip(self.burns, burns, strict=False):
            row.load(burn if isinstance(burn, Mapping) else {})
        self._sync_mode_widgets()
        self._sync_cargo()
        self._update_speed()
        self._filling = False

    def apply(self) -> list[str]:
        data = self._data
        if not isinstance(data, Mapping):
            return ["scenario: document is missing"]
        errors: list[str] = []
        data["name"] = self.name.text()
        data["craft"] = self.craft.text().strip()
        data["epoch"] = self.epoch.text().strip()
        data["frame"] = "heliocentric_ecliptic_j2000"
        bodies = data.get("bodies")
        wanted = [
            name
            for name, box in (("sun", self.sun), ("earth", self.earth), ("mars", self.mars))
            if box.isChecked()
        ]
        if isinstance(bodies, list):
            del bodies[:]
            bodies.extend(wanted)
        else:
            data["bodies"] = wanted
        ship = str(self.ship.currentData())
        data["ship"] = ship
        if ship == "loaded":
            errors.extend(_put_float(data, "cargo_kg", self.cargo, "cargo_kg"))
        elif "cargo_kg" in data:
            del data["cargo_kg"]
        initial = data["initial"]
        mode = str(self.mode.currentData())
        initial["mode"] = mode
        propellant = initial["propellant"]
        errors.extend(_put_float(propellant, "water_kg", self.water, "initial.propellant.water_kg"))
        errors.extend(_put_float(propellant, "lh2_kg", self.lh2, "initial.propellant.lh2_kg"))
        errors.extend(_put_float(propellant, "lox_kg", self.lox, "initial.propellant.lox_kg"))
        if mode == "heliocentric":
            errors.extend(_put_vector(initial, "position_m", self.position, "initial.position_m"))
            errors.extend(
                _put_vector(initial, "velocity_m_per_s", self.velocity, "initial.velocity_m_per_s")
            )
            for key in ("agl_m", "longitude_deg"):
                if key in initial:
                    del initial[key]
        else:
            errors.extend(_put_float(initial, "agl_m", self.agl, "initial.agl_m"))
            errors.extend(
                _put_float(initial, "longitude_deg", self.longitude, "initial.longitude_deg")
            )
            for key in ("position_m", "velocity_m_per_s"):
                if key in initial:
                    del initial[key]
        data["arrival"]["body"] = self.arrival_body.currentText()
        errors.extend(_put_float(data["arrival"], "agl_m", self.arrival_agl, "arrival.agl_m"))
        data["electrolysis"] = self.electrolysis.currentText()
        data["notes"] = self.notes.toPlainText()
        burn_list = data["burns"]
        for row, item in zip(self.burns, burn_list, strict=False):
            errors.extend(row.store(item))
        return errors

    def apply_plan(self, plan: PresetPlan, craft: Craft) -> None:
        """Write preset fields. Called only when the plan says it applies."""
        data = self._data
        data["ship"] = plan.ship
        if plan.ship == "loaded":
            data["cargo_kg"] = float(plan.cargo_kg)
        elif "cargo_kg" in data:
            del data["cargo_kg"]
        data["arrival"]["body"] = plan.arrival_body
        if plan.initial_mode and plan.initial_mode != "heliocentric":
            initial = data["initial"]
            initial["mode"] = plan.initial_mode
            if "agl_m" not in initial:
                initial["agl_m"] = 400000.0
            if "longitude_deg" not in initial:
                initial["longitude_deg"] = 0.0
            for key in ("position_m", "velocity_m_per_s"):
                if key in initial:
                    del initial[key]
        if plan.fill_propellant:
            propellant = data["initial"]["propellant"]
            propellant["water_kg"] = craft.tanks.water_kg
            propellant["lh2_kg"] = craft.tanks.lh2_kg
            propellant["lox_kg"] = craft.tanks.lox_kg
        for item, burn in zip(data["burns"], plan.burns, strict=False):
            item["role"] = burn.role
            item["duration_s"] = burn.duration_s
            item["throttle"] = burn.throttle
            if burn.oxygen is None:
                if "oxygen" in item:
                    del item["oxygen"]
            else:
                item["oxygen"] = burn.oxygen
        self.load_data(data)
        self.changed.emit()

    def set_dates(self, choices: list[DateChoice]) -> None:
        self.dates.clear()
        for choice in choices:
            text = (
                f"{choice.epoch:%Y-%m-%d}   {choice.angle_deg:.1f}°   "
                f"flight {format_elapsed(choice.flight_s)}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, choice.epoch.isoformat())
            self.dates.addItem(item)
        if self.dates.count():
            self.dates.setCurrentRow(0)

    def selected_epoch(self) -> datetime | None:
        item = self.dates.currentItem()
        if item is None:
            return None
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw, str):
            return None
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def set_epoch(self, text: str) -> None:
        """Write the epoch and leave every other field alone."""
        self.epoch.setText(text)
        if isinstance(self._data, Mapping):
            self._data["epoch"] = text

    def set_refined(self, epoch: datetime, score: float) -> None:
        self._refined = epoch
        self.refined_label.setText(
            f"Refined {epoch:%Y-%m-%d %H:%M} UTC, score {score:.4g}. Accept writes the epoch only."
        )
        self.accept_button.setEnabled(True)

    def take_refined(self) -> datetime | None:
        return self._refined

    def _browse_craft(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Craft file",
            str(self._directory or Path.cwd()),
            "Craft (*.craft.yaml *.yaml)",
        )
        if not path:
            return
        chosen = Path(path)
        base = self._directory
        if base is not None:
            try:
                relative = chosen.resolve().relative_to(base.resolve())
                self.craft.setText(relative.as_posix())
                return
            except ValueError:
                pass
        self.craft.setText(str(chosen))

    def _fill_modes(self, current: str) -> None:
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem("Earth circular equatorial", "earth_circular_equatorial")
        self.mode.addItem("Mars circular equatorial", "mars_circular_equatorial")
        if current == "heliocentric":
            self.mode.addItem("Heliocentric", "heliocentric")
        index = self.mode.findData(current)
        self.mode.setCurrentIndex(index if index >= 0 else 0)
        self.mode.blockSignals(False)

    def _on_mode(self, *_args: object) -> None:
        self._sync_mode_widgets()
        self._update_speed()
        self._emit()

    def _sync_mode_widgets(self) -> None:
        helio = self.mode.currentData() == "heliocentric"
        for widget in (
            self.agl,
            self.longitude,
            self.speed,
            self._agl_label,
            self._longitude_label,
            self._speed_label,
        ):
            if widget is not None:
                widget.setVisible(not helio)
        for widget in (*self.position, *self.velocity, self._position_label, self._velocity_label):
            if widget is not None:
                widget.setVisible(helio)

    def _sync_cargo(self, *_args: object) -> None:
        loaded = self.ship.currentData() == "loaded"
        self.cargo.setVisible(loaded)
        if self._cargo_label is not None:
            self._cargo_label.setVisible(loaded)

    def _update_speed(self, *_args: object) -> None:
        mode = self.mode.currentData()
        if mode == "heliocentric":
            self.speed.setText("heliocentric state")
            return
        body = "mars" if mode == "mars_circular_equatorial" else "earth"
        try:
            agl = float(self.agl.text())
        except ValueError:
            self.speed.setText("—")
            return
        self.speed.setText(f"{circular_speed_m_per_s(body, agl):,.1f} m/s")

    def _emit(self, *_args: object) -> None:
        if not self._filling:
            self.changed.emit()


class _BurnRow(QWidget):
    changed = Signal()

    def __init__(self, role: str, oxygen: bool) -> None:
        super().__init__()
        self.role = role
        self._uses_oxygen = oxygen
        self._filling = False
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.0, 1.0e12)
        self.duration.setDecimals(1)
        self.throttle = QDoubleSpinBox()
        self.throttle.setRange(0.0, 1.0)
        self.throttle.setDecimals(3)
        self.throttle.setSingleStep(0.05)
        self.oxygen = QComboBox()
        self.oxygen.addItems(["off", "auto"])
        self.oxygen.setVisible(oxygen)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(role))
        row.addWidget(QLabel("duration s"))
        row.addWidget(self.duration)
        row.addWidget(QLabel("throttle"))
        row.addWidget(self.throttle)
        if oxygen:
            row.addWidget(QLabel("oxygen"))
            row.addWidget(self.oxygen)
        self.duration.valueChanged.connect(self._emit)
        self.throttle.valueChanged.connect(self._emit)
        self.oxygen.currentIndexChanged.connect(self._emit)

    def load(self, burn: Mapping[str, Any]) -> None:
        self._filling = True
        self.duration.setValue(float(burn.get("duration_s", 0.0)))
        self.throttle.setValue(float(burn.get("throttle", 0.0)))
        oxygen = str(burn.get("oxygen", "off"))
        index = self.oxygen.findText(oxygen)
        self.oxygen.setCurrentIndex(index if index >= 0 else 0)
        self._filling = False

    def store(self, item: Any) -> list[str]:
        item["duration_s"] = float(self.duration.value())
        item["throttle"] = float(self.throttle.value())
        if self._uses_oxygen:
            item["oxygen"] = self.oxygen.currentText()
        elif "oxygen" in item:
            del item["oxygen"]
        return []

    def _emit(self, *_args: object) -> None:
        if not self._filling:
            self.changed.emit()


def _triple(edits: list[QLineEdit]) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    for edit in edits:
        layout.addWidget(edit)
    return row


def _get(mapping: object, key: str) -> object:
    if isinstance(mapping, Mapping):
        return mapping.get(key, 0)
    return 0


def _fmt(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "" if value is None else str(value)
    number = float(value)
    if number.is_integer() and abs(number) < 1.0e15:
        return str(int(number))
    return f"{number:.12g}"


def _epoch_text(value: object) -> str:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _put_float(mapping: Any, key: str, edit: QLineEdit, where: str) -> list[str]:
    try:
        mapping[key] = float(edit.text().strip())
    except ValueError:
        return [f"{where}: expected a number"]
    return []


def _put_int(mapping: Any, key: str, edit: QLineEdit, where: str) -> list[str]:
    try:
        number = float(edit.text().strip())
    except ValueError:
        return [f"{where}: expected an integer"]
    if not number.is_integer():
        return [f"{where}: expected an integer"]
    mapping[key] = int(number)
    return []


def _put_vector(mapping: Any, key: str, edits: list[QLineEdit], where: str) -> list[str]:
    values: list[float] = []
    errors: list[str] = []
    for index, edit in enumerate(edits):
        try:
            values.append(float(edit.text().strip()))
        except ValueError:
            errors.append(f"{where}[{index}]: expected a number")
    if errors:
        return errors
    current = mapping.get(key)
    if isinstance(current, list) and len(current) == 3:
        for index, value in enumerate(values):
            current[index] = value
    else:
        mapping[key] = values
    return []
