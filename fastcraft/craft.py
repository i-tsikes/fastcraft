"""Load and validate a `*.craft.yaml` document.

The reactor, Brayton cycle, radiators, and electrolysis are scalar limits.
v1 does not integrate a thermal network, and dry mass is one number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastcraft.units import CRAFT_SCHEMA
from fastcraft.yamlio import DocumentError, load_yaml

_CRAFT_FIELDS = {
    "schema",
    "name",
    "dry_mass_kg",
    "tanks",
    "starship",
    "power",
    "electrolysis",
    "engines",
    "geometry",
    "notes",
}
_TANK_FIELDS = {"water_kg", "lh2_kg", "lox_kg"}
_STARSHIP_FIELDS = {"dry_mass_kg", "landing_propellant_kg"}
_POWER_FIELDS = {"reactor_thermal_w", "electric_w", "radiator_reject_w"}
_ELECTROLYSIS_FIELDS = {"max_kg_per_s"}
_ENGINE_FIELDS = {"count", "thrust_n", "isp_s"}
_ENGINES_FIELDS = {"hydrolox", "mhd", "oxygen"}
_GEOMETRY_FIELDS = {"tank_length_m", "tank_radius_m", "radiator_span_m"}


@dataclass(frozen=True)
class Tanks:
    """Propellant capacities, kg."""

    water_kg: float
    lh2_kg: float
    lox_kg: float


@dataclass(frozen=True)
class Starship:
    """Docked crew stage. Landing propellant is reserved and never burned."""

    dry_mass_kg: float
    landing_propellant_kg: float


@dataclass(frozen=True)
class Power:
    """Scalar plant limits, watts."""

    reactor_thermal_w: float
    electric_w: float
    radiator_reject_w: float

    @property
    def waste_heat_w(self) -> float:
        return self.reactor_thermal_w - self.electric_w


@dataclass(frozen=True)
class Electrolysis:
    """Maximum water conversion rate, kg/s. Products split 1/9 LH2 and 8/9 LOX."""

    max_kg_per_s: float


@dataclass(frozen=True)
class Engine:
    """One thruster type. `thrust_n` and `isp_s` are per engine."""

    count: int
    thrust_n: float
    isp_s: float


@dataclass(frozen=True)
class Engines:
    hydrolox: Engine
    mhd: Engine
    oxygen: Engine | None


@dataclass(frozen=True)
class Geometry:
    """Schematic dimensions, meters. Not a CAD model."""

    tank_length_m: float
    tank_radius_m: float
    radiator_span_m: float


@dataclass(frozen=True)
class Craft:
    name: str
    dry_mass_kg: float
    tanks: Tanks
    starship: Starship
    power: Power
    electrolysis: Electrolysis
    engines: Engines
    geometry: Geometry
    notes: str
    source_path: Path | None = None

    @property
    def has_oxygen(self) -> bool:
        oxygen = self.engines.oxygen
        return oxygen is not None and oxygen.count > 0 and oxygen.thrust_n > 0.0


def load_craft(path: Path) -> Craft:
    """Read and validate a craft file."""
    craft = parse_craft(load_yaml(path))
    return Craft(
        name=craft.name,
        dry_mass_kg=craft.dry_mass_kg,
        tanks=craft.tanks,
        starship=craft.starship,
        power=craft.power,
        electrolysis=craft.electrolysis,
        engines=craft.engines,
        geometry=craft.geometry,
        notes=craft.notes,
        source_path=path,
    )


def parse_craft(data: Any) -> Craft:
    """Validate a parsed document. Unknown fields are rejected."""
    errors: list[str] = []
    if not isinstance(data, Mapping):
        raise DocumentError(["craft document must be a mapping"])
    _reject_unknown(data, _CRAFT_FIELDS, "craft", errors)
    if data.get("schema") != CRAFT_SCHEMA:
        errors.append(f"schema: expected {CRAFT_SCHEMA!r}")
    name = _string(data.get("name"), "name", errors)
    dry = _number(data.get("dry_mass_kg"), "dry_mass_kg", errors)
    tanks = _tanks(data.get("tanks"), errors)
    starship = _starship(data.get("starship"), errors)
    power = _power(data.get("power"), errors)
    electrolysis = _electrolysis(data.get("electrolysis"), errors)
    engines = _engines(data.get("engines"), errors)
    geometry = _geometry(data.get("geometry"), errors)
    notes = _string(data.get("notes"), "notes", errors)
    if power is not None:
        if power.electric_w >= power.reactor_thermal_w:
            errors.append("power.electric_w must be below power.reactor_thermal_w")
        if power.waste_heat_w > power.radiator_reject_w:
            errors.append(
                "waste heat reactor_thermal_w - electric_w exceeds power.radiator_reject_w"
            )
    if errors or tanks is None or starship is None or power is None or electrolysis is None:
        raise DocumentError(errors or ["craft document is incomplete"])
    if engines is None or geometry is None:
        raise DocumentError(errors or ["craft document is incomplete"])
    return Craft(
        name=name,
        dry_mass_kg=dry,
        tanks=tanks,
        starship=starship,
        power=power,
        electrolysis=electrolysis,
        engines=engines,
        geometry=geometry,
        notes=notes,
    )


def _tanks(value: Any, errors: list[str]) -> Tanks | None:
    mapping = _mapping(value, "tanks", _TANK_FIELDS, errors)
    if mapping is None:
        return None
    return Tanks(
        water_kg=_number(mapping.get("water_kg"), "tanks.water_kg", errors),
        lh2_kg=_number(mapping.get("lh2_kg"), "tanks.lh2_kg", errors),
        lox_kg=_number(mapping.get("lox_kg"), "tanks.lox_kg", errors),
    )


def _starship(value: Any, errors: list[str]) -> Starship | None:
    mapping = _mapping(value, "starship", _STARSHIP_FIELDS, errors)
    if mapping is None:
        return None
    return Starship(
        dry_mass_kg=_number(mapping.get("dry_mass_kg"), "starship.dry_mass_kg", errors),
        landing_propellant_kg=_number(
            mapping.get("landing_propellant_kg"),
            "starship.landing_propellant_kg",
            errors,
        ),
    )


def _power(value: Any, errors: list[str]) -> Power | None:
    mapping = _mapping(value, "power", _POWER_FIELDS, errors)
    if mapping is None:
        return None
    return Power(
        reactor_thermal_w=_number(
            mapping.get("reactor_thermal_w"), "power.reactor_thermal_w", errors
        ),
        electric_w=_number(mapping.get("electric_w"), "power.electric_w", errors),
        radiator_reject_w=_number(
            mapping.get("radiator_reject_w"),
            "power.radiator_reject_w",
            errors,
        ),
    )


def _electrolysis(value: Any, errors: list[str]) -> Electrolysis | None:
    mapping = _mapping(value, "electrolysis", _ELECTROLYSIS_FIELDS, errors)
    if mapping is None:
        return None
    return Electrolysis(
        max_kg_per_s=_number(mapping.get("max_kg_per_s"), "electrolysis.max_kg_per_s", errors)
    )


def _engines(value: Any, errors: list[str]) -> Engines | None:
    mapping = _mapping(value, "engines", _ENGINES_FIELDS, errors)
    if mapping is None:
        return None
    hydrolox = _engine(mapping.get("hydrolox"), "engines.hydrolox", errors)
    mhd = _engine(mapping.get("mhd"), "engines.mhd", errors)
    oxygen: Engine | None = None
    if "oxygen" in mapping:
        oxygen = _engine(mapping.get("oxygen"), "engines.oxygen", errors)
    if hydrolox is None or mhd is None:
        return None
    return Engines(hydrolox=hydrolox, mhd=mhd, oxygen=oxygen)


def _engine(value: Any, where: str, errors: list[str]) -> Engine | None:
    mapping = _mapping(value, where, _ENGINE_FIELDS, errors)
    if mapping is None:
        return None
    count = _integer(mapping.get("count"), f"{where}.count", errors)
    thrust = _number(mapping.get("thrust_n"), f"{where}.thrust_n", errors)
    isp = _number(mapping.get("isp_s"), f"{where}.isp_s", errors)
    if count > 0 and thrust > 0.0 and isp <= 0.0:
        errors.append(f"{where}.isp_s must be positive when the engine can produce thrust")
    return Engine(count=count, thrust_n=thrust, isp_s=isp)


def _geometry(value: Any, errors: list[str]) -> Geometry | None:
    mapping = _mapping(value, "geometry", _GEOMETRY_FIELDS, errors)
    if mapping is None:
        return None
    return Geometry(
        tank_length_m=_number(mapping.get("tank_length_m"), "geometry.tank_length_m", errors),
        tank_radius_m=_number(mapping.get("tank_radius_m"), "geometry.tank_radius_m", errors),
        radiator_span_m=_number(mapping.get("radiator_span_m"), "geometry.radiator_span_m", errors),
    )


def _mapping(
    value: Any,
    where: str,
    allowed: set[str],
    errors: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{where}: expected a mapping")
        return None
    _reject_unknown(value, allowed, where, errors)
    return value


def _reject_unknown(
    data: Mapping[str, Any],
    allowed: set[str],
    where: str,
    errors: list[str],
) -> None:
    for key in data:
        if str(key) not in allowed:
            errors.append(f"{where}: unknown field {key!r}")


def _number(value: Any, where: str, errors: list[str]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: expected a number")
        return 0.0
    number = float(value)
    if number < 0.0:
        errors.append(f"{where}: must be >= 0")
    return number


def _integer(value: Any, where: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: expected an integer")
        return 0
    if isinstance(value, float) and not value.is_integer():
        errors.append(f"{where}: expected an integer")
        return 0
    number = int(value)
    if number < 0:
        errors.append(f"{where}: must be >= 0")
    return number


def _string(value: Any, where: str, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{where}: expected a string")
        return ""
    return value
