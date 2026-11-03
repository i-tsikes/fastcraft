"""Load and validate a `*.scenario.yaml` document.

The loader checks the document before any integration. An Earth or Mars
circular initial state is converted to the heliocentric frame once, at
the epoch, by `initial_state`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fastcraft.craft import Craft, load_craft
from fastcraft.ephemeris import Ephemeris
from fastcraft.units import (
    FRAME,
    HYDROLOX_RATIO,
    KNOWN_BODIES,
    MARS_OBLIQUITY_RAD,
    MIN_AGL_M,
    OBLIQUITY_RAD,
    SCENARIO_SCHEMA,
    body_mu,
    body_radius_m,
)
from fastcraft.yamlio import DocumentError, load_yaml

_SCENARIO_FIELDS = {
    "schema",
    "name",
    "craft",
    "epoch",
    "frame",
    "bodies",
    "initial",
    "arrival",
    "ship",
    "cargo_kg",
    "burns",
    "electrolysis",
    "notes",
}
_INITIAL_COMMON = {"mode", "propellant"}
_CIRCULAR_FIELDS = {"mode", "agl_m", "longitude_deg", "propellant"}
_HELIO_FIELDS = {"mode", "position_m", "velocity_m_per_s", "propellant"}
_PROPELLANT_FIELDS = {"water_kg", "lh2_kg", "lox_kg"}
_ARRIVAL_FIELDS = {"body", "agl_m"}
_BURN_COMMON = {"role", "duration_s", "throttle"}
_BURN_OXYGEN = {"role", "duration_s", "throttle", "oxygen"}
_BURN_ROLES = ("departure", "accelerate", "decelerate", "insertion")
_SHIPS = ("docked", "none", "empty", "loaded")
_MODES = ("earth_circular_equatorial", "mars_circular_equatorial", "heliocentric")


@dataclass(frozen=True)
class Propellant:
    water_kg: float
    lh2_kg: float
    lox_kg: float


@dataclass(frozen=True)
class CircularInitial:
    mode: str
    agl_m: float
    longitude_deg: float
    propellant: Propellant


@dataclass(frozen=True)
class HeliocentricInitial:
    mode: str
    position_m: tuple[float, float, float]
    velocity_m_per_s: tuple[float, float, float]
    propellant: Propellant


type Initial = CircularInitial | HeliocentricInitial


@dataclass(frozen=True)
class Arrival:
    body: str
    agl_m: float


@dataclass(frozen=True)
class Burn:
    role: str
    duration_s: float
    throttle: float
    oxygen: str | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    craft_rel: str
    craft_path: Path | None
    epoch: datetime
    frame: str
    bodies: tuple[str, ...]
    initial: Initial
    arrival: Arrival
    ship: str
    cargo_kg: float
    burns: tuple[Burn, ...]
    electrolysis: str
    notes: str
    source_path: Path | None = None

    @property
    def departure_body(self) -> str | None:
        if isinstance(self.initial, CircularInitial):
            if self.initial.mode == "earth_circular_equatorial":
                return "earth"
            return "mars"
        return None


def load_scenario(path: Path) -> Scenario:
    """Read and validate a scenario file and the craft it points at."""
    return parse_scenario(load_yaml(path), source=path)


def parse_scenario(data: Any, *, source: Path | None, craft: Craft | None = None) -> Scenario:
    """Validate a parsed scenario. Propellant is checked against tank capacity."""
    errors: list[str] = []
    if not isinstance(data, Mapping):
        raise DocumentError(["scenario document must be a mapping"])
    _reject_unknown(data, _SCENARIO_FIELDS, "scenario", errors)
    if data.get("schema") != SCENARIO_SCHEMA:
        errors.append(f"schema: expected {SCENARIO_SCHEMA!r}")
    name = _string(data.get("name"), "name", errors)
    craft_rel = _string(data.get("craft"), "craft", errors)
    epoch = _epoch(data.get("epoch"), errors)
    frame = _string(data.get("frame"), "frame", errors)
    if frame and frame != FRAME:
        errors.append(f"frame: expected {FRAME!r}")
    bodies = _bodies(data.get("bodies"), errors)
    initial = _initial(data.get("initial"), errors)
    arrival = _arrival(data.get("arrival"), errors)
    ship = _string(data.get("ship"), "ship", errors)
    if ship and ship not in _SHIPS:
        errors.append("ship: expected 'docked', 'none', 'empty', or 'loaded'")
    cargo = _cargo(data, ship, errors)
    burns = _burns(data.get("burns"), errors)
    electrolysis = _string(data.get("electrolysis"), "electrolysis", errors)
    if electrolysis and electrolysis not in ("on", "off"):
        errors.append("electrolysis: expected 'on' or 'off'")
    notes = ""
    if "notes" in data:
        notes = _string(data.get("notes"), "notes", errors)
    craft_path = _resolve_craft(source, craft_rel) if craft_rel and source is not None else None
    loaded = craft
    if loaded is None and craft_path is not None:
        if not craft_path.is_file():
            errors.append(f"craft: file not found at {craft_path}")
        else:
            try:
                loaded = load_craft(craft_path)
            except DocumentError as exc:
                errors.extend(f"craft: {item}" for item in exc.errors)
    elif loaded is None and craft_rel:
        errors.append("craft: could not resolve the craft file")
    if loaded is not None and initial is not None:
        _check_propellant(initial.propellant, loaded, errors)
    if errors or epoch is None or initial is None or arrival is None or burns is None or not bodies:
        raise DocumentError(errors or ["scenario document is incomplete"])
    return Scenario(
        name=name,
        craft_rel=craft_rel,
        craft_path=craft_path,
        epoch=epoch,
        frame=frame,
        bodies=tuple(bodies),
        initial=initial,
        arrival=arrival,
        ship=ship,
        cargo_kg=cargo,
        burns=tuple(burns),
        electrolysis=electrolysis,
        notes=notes,
        source_path=source,
    )


def fixed_mass_kg(craft: Craft, scenario: Scenario) -> float:
    """Dry mass plus the attached Ship and its reserved landing propellant."""
    mass = craft.dry_mass_kg
    if scenario.ship == "none":
        return mass
    mass += craft.starship.dry_mass_kg
    mass += craft.starship.landing_propellant_kg
    if scenario.ship == "loaded":
        mass += scenario.cargo_kg
    return mass


def initial_state(
    scenario: Scenario,
    craft: Craft,
    ephemeris: Ephemeris,
) -> NDArray[np.float64]:
    """Heliocentric state vector of length 10 at the scenario epoch."""
    position, velocity = initial_rv(scenario, ephemeris)
    propellant = scenario.initial.propellant
    total = fixed_mass_kg(craft, scenario)
    total += propellant.water_kg + propellant.lh2_kg + propellant.lox_kg
    state = np.zeros(10, dtype=np.float64)
    state[0:3] = position
    state[3:6] = velocity
    state[6] = total
    state[7] = propellant.water_kg
    state[8] = propellant.lh2_kg
    state[9] = propellant.lox_kg
    return state


def initial_rv(
    scenario: Scenario,
    ephemeris: Ephemeris,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Heliocentric position and velocity at epoch, meters and m/s."""
    initial = scenario.initial
    if isinstance(initial, HeliocentricInitial):
        position = np.array(initial.position_m, dtype=np.float64)
        velocity = np.array(initial.velocity_m_per_s, dtype=np.float64)
        return position, velocity
    body = "earth" if initial.mode == "earth_circular_equatorial" else "mars"
    planet_r, planet_v = ephemeris.state(body, 0.0)
    relative_r, relative_v = _circular_equatorial(body, initial.agl_m, initial.longitude_deg)
    return planet_r + relative_r, planet_v + relative_v


def _circular_equatorial(
    body: str,
    agl_m: float,
    longitude_deg: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    radius = body_radius_m(body) + agl_m
    speed = float(np.sqrt(body_mu(body) / radius))
    obliquity = OBLIQUITY_RAD if body == "earth" else MARS_OBLIQUITY_RAD
    longitude = np.deg2rad(longitude_deg)
    x_eq = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    y_eq = np.array([0.0, np.cos(obliquity), np.sin(obliquity)], dtype=np.float64)
    radial = np.cos(longitude) * x_eq + np.sin(longitude) * y_eq
    east = -np.sin(longitude) * x_eq + np.cos(longitude) * y_eq
    return radius * radial, speed * east


def _check_propellant(propellant: Propellant, craft: Craft, errors: list[str]) -> None:
    pairs = (
        ("water_kg", propellant.water_kg, craft.tanks.water_kg),
        ("lh2_kg", propellant.lh2_kg, craft.tanks.lh2_kg),
        ("lox_kg", propellant.lox_kg, craft.tanks.lox_kg),
    )
    for name, loaded, capacity in pairs:
        if loaded > capacity:
            errors.append(
                f"initial.propellant.{name} ({loaded}) exceeds tank capacity ({capacity})"
            )


def _resolve_craft(source: Path, craft_rel: str) -> Path:
    path = Path(craft_rel)
    if path.is_absolute():
        return path
    return (source.parent / path).resolve()


def _initial(value: Any, errors: list[str]) -> Initial | None:
    if not isinstance(value, Mapping):
        errors.append("initial: expected a mapping")
        return None
    mode = value.get("mode")
    if mode not in _MODES:
        errors.append(
            "initial.mode: expected 'earth_circular_equatorial', "
            "'mars_circular_equatorial', or 'heliocentric'"
        )
        mode = "heliocentric"
    allowed = _HELIO_FIELDS if mode == "heliocentric" else _CIRCULAR_FIELDS
    _reject_unknown(value, allowed, "initial", errors)
    propellant = _propellant(value.get("propellant"), errors)
    if propellant is None:
        return None
    if mode == "heliocentric":
        position = _vector(value.get("position_m"), "initial.position_m", errors)
        velocity = _vector(value.get("velocity_m_per_s"), "initial.velocity_m_per_s", errors)
        if position is None or velocity is None:
            return None
        return HeliocentricInitial(
            mode="heliocentric",
            position_m=position,
            velocity_m_per_s=velocity,
            propellant=propellant,
        )
    agl = _number(value.get("agl_m"), "initial.agl_m", errors, minimum=None)
    if agl < MIN_AGL_M:
        errors.append(f"initial.agl_m must be at least {MIN_AGL_M:g} m")
    longitude = 0.0
    if "longitude_deg" in value:
        longitude = _number(
            value.get("longitude_deg"), "initial.longitude_deg", errors, minimum=None
        )
    return CircularInitial(
        mode=str(mode),
        agl_m=agl,
        longitude_deg=longitude,
        propellant=propellant,
    )


def _arrival(value: Any, errors: list[str]) -> Arrival | None:
    if not isinstance(value, Mapping):
        errors.append("arrival: expected a mapping")
        return None
    _reject_unknown(value, _ARRIVAL_FIELDS, "arrival", errors)
    body = _string(value.get("body"), "arrival.body", errors)
    if body and body not in ("earth", "mars"):
        errors.append("arrival.body: expected 'earth' or 'mars'")
    agl = _number(value.get("agl_m"), "arrival.agl_m", errors, minimum=None)
    if agl < MIN_AGL_M:
        errors.append(f"arrival.agl_m must be at least {MIN_AGL_M:g} m")
    if not body:
        return None
    return Arrival(body=body, agl_m=agl)


def _propellant(value: Any, errors: list[str]) -> Propellant | None:
    if not isinstance(value, Mapping):
        errors.append("initial.propellant: expected a mapping")
        return None
    _reject_unknown(value, _PROPELLANT_FIELDS, "initial.propellant", errors)
    return Propellant(
        water_kg=_number(value.get("water_kg"), "initial.propellant.water_kg", errors),
        lh2_kg=_number(value.get("lh2_kg"), "initial.propellant.lh2_kg", errors),
        lox_kg=_number(value.get("lox_kg"), "initial.propellant.lox_kg", errors),
    )


def _burns(value: Any, errors: list[str]) -> list[Burn] | None:
    if not isinstance(value, list):
        errors.append("burns: expected a list of four burns")
        return None
    if len(value) != 4:
        errors.append("burns: expected exactly four burns, in role order")
    parsed: list[Burn] = []
    for index, role in enumerate(_BURN_ROLES):
        if index >= len(value):
            break
        item = value[index]
        burn = _burn(item, index, role, errors)
        if burn is not None:
            parsed.append(burn)
    if len(parsed) != 4:
        return None
    return parsed


def _burn(value: Any, index: int, expected: str, errors: list[str]) -> Burn | None:
    where = f"burns[{index}]"
    if not isinstance(value, Mapping):
        errors.append(f"{where}: expected a mapping")
        return None
    role = value.get("role")
    mhd = expected in ("accelerate", "decelerate")
    allowed = _BURN_OXYGEN if mhd else _BURN_COMMON
    _reject_unknown(value, allowed, where, errors)
    if role != expected:
        errors.append(f"{where}.role: expected {expected!r}")
    duration = _number(value.get("duration_s"), f"{where}.duration_s", errors)
    throttle = _number(value.get("throttle"), f"{where}.throttle", errors, minimum=None)
    if throttle < 0.0 or throttle > 1.0:
        errors.append(f"{where}.throttle must be from 0 to 1")
    oxygen: str | None = None
    if mhd:
        oxygen = _string(value.get("oxygen"), f"{where}.oxygen", errors)
        if oxygen and oxygen not in ("auto", "off"):
            errors.append(f"{where}.oxygen: expected 'auto' or 'off'")
    elif "oxygen" in value:
        errors.append(f"{where}: oxygen is only valid on MHD burns")
    return Burn(
        role=expected,
        duration_s=duration,
        throttle=throttle,
        oxygen=oxygen,
    )


def _bodies(value: Any, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append("bodies: expected a non-empty list")
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in KNOWN_BODIES:
            errors.append(f"bodies: unknown body {item!r}")
            continue
        if item in names:
            errors.append(f"bodies: duplicate {item!r}")
            continue
        names.append(item)
    return names


def _cargo(data: Mapping[str, Any], ship: str, errors: list[str]) -> float:
    present = "cargo_kg" in data
    if ship == "loaded":
        if not present:
            errors.append("cargo_kg is required when ship is 'loaded'")
            return 0.0
        return _number(data.get("cargo_kg"), "cargo_kg", errors)
    if present:
        errors.append("cargo_kg is only allowed when ship is 'loaded'")
    return 0.0


def _epoch(value: Any, errors: list[str]) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            errors.append("epoch: expected an ISO-8601 UTC time")
            return None
    else:
        errors.append("epoch: expected an ISO-8601 UTC time")
        return None
    if parsed.tzinfo is None:
        errors.append("epoch: time zone is required")
        return None
    return parsed.astimezone(UTC)


def _vector(
    value: Any,
    where: str,
    errors: list[str],
) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3:
        errors.append(f"{where}: expected a list of three numbers")
        return None
    numbers = [
        _number(item, f"{where}[{index}]", errors, minimum=None) for index, item in enumerate(value)
    ]
    return (numbers[0], numbers[1], numbers[2])


def _number(
    value: Any,
    where: str,
    errors: list[str],
    *,
    minimum: float | None = 0.0,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: expected a number")
        return 0.0
    number = float(value)
    if minimum is not None and number < minimum:
        errors.append(f"{where}: must be >= {minimum:g}")
    return number


def _string(value: Any, where: str, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{where}: expected a string")
        return ""
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


# Imported for the mixture comment readers and preset code.
MIXTURE_OXYGEN_PER_HYDROGEN = HYDROLOX_RATIO
