"""Scenario loader checks, including propellant capacity and the Earth orbit."""

import math
from pathlib import Path

import numpy as np
import pytest

from fastcraft.craft import load_craft
from fastcraft.ephemeris import AnalyticEphemeris
from fastcraft.scenario import initial_rv, load_scenario
from fastcraft.units import (
    AU_M,
    MU_EARTH_M3_PER_S2,
    MU_SUN_M3_PER_S2,
    WGS84_EQUATORIAL_RADIUS_M,
)
from fastcraft.yamlio import DocumentError

_CRAFT = """\
schema: fastcraft.craft/v1
name: Hauler
dry_mass_kg: 1000.0
tanks:
  water_kg: 100.0
  lh2_kg: 50.0
  lox_kg: 400.0
starship:
  dry_mass_kg: 10.0
  landing_propellant_kg: 5.0
power:
  reactor_thermal_w: 1000.0
  electric_w: 0.0
  radiator_reject_w: 1000.0
electrolysis:
  max_kg_per_s: 0.0
engines:
  hydrolox:
    count: 1
    thrust_n: 100.0
    isp_s: 300.0
  mhd:
    count: 1
    thrust_n: 1.0
    isp_s: 1000.0
geometry:
  tank_length_m: 1.0
  tank_radius_m: 1.0
  radiator_span_m: 1.0
notes: ""
"""

_SCENARIO = """\
schema: fastcraft.scenario/v1
name: Earth to Mars
craft: hauler.craft.yaml
epoch: "2035-04-01T00:00:00Z"
frame: heliocentric_ecliptic_j2000
bodies: [sun, earth, mars]
initial:
  mode: earth_circular_equatorial
  agl_m: 400000.0
  longitude_deg: 0.0
  propellant:
    water_kg: 100.0
    lh2_kg: 50.0
    lox_kg: 400.0
arrival:
  body: mars
  agl_m: 400000.0
ship: docked
burns:
  - role: departure
    duration_s: 0.0
    throttle: 1.0
  - role: accelerate
    duration_s: 0.0
    throttle: 1.0
    oxygen: off
  - role: decelerate
    duration_s: 0.0
    throttle: 1.0
    oxygen: off
  - role: insertion
    duration_s: 0.0
    throttle: 1.0
electrolysis: off
"""


def _write(tmp_path: Path, scenario: str = _SCENARIO) -> Path:
    (tmp_path / "hauler.craft.yaml").write_text(_CRAFT, encoding="utf-8")
    path = tmp_path / "flight.scenario.yaml"
    path.write_text(scenario, encoding="utf-8")
    return path


def test_earth_circular_state_is_computed(tmp_path: Path) -> None:
    scenario = load_scenario(_write(tmp_path))
    craft = load_craft(tmp_path / "hauler.craft.yaml")
    assert craft.dry_mass_kg == pytest.approx(1000.0)
    ephemeris = AnalyticEphemeris(scenario.epoch)
    position, velocity = initial_rv(scenario, ephemeris)
    earth_r, earth_v = ephemeris.state("earth", 0.0)
    relative_r = position - earth_r
    relative_v = velocity - earth_v
    radius = WGS84_EQUATORIAL_RADIUS_M + 400_000.0
    speed = math.sqrt(MU_EARTH_M3_PER_S2 / radius)
    assert np.linalg.norm(relative_r) == pytest.approx(radius, rel=1e-12)
    assert np.linalg.norm(relative_v) == pytest.approx(speed, rel=1e-12)
    assert abs(float(np.dot(relative_r, relative_v))) < 1e-6 * radius * speed
    assert np.linalg.norm(earth_r) == pytest.approx(AU_M, rel=0.02)
    mars_r, _mars_v = ephemeris.state("mars", 0.0)
    assert np.linalg.norm(mars_r) == pytest.approx(1.524 * AU_M, rel=0.05)
    assert ephemeris.mu("sun") == MU_SUN_M3_PER_S2


def test_rejects_propellant_above_capacity(tmp_path: Path) -> None:
    text = _SCENARIO.replace("water_kg: 100.0", "water_kg: 101.0", 1)
    # The craft capacity is 100 and the first propellant value in the scenario is water.
    text = _SCENARIO.replace(
        """  propellant:
    water_kg: 100.0
""",
        """  propellant:
    water_kg: 250.0
""",
    )
    with pytest.raises(DocumentError, match="exceeds tank capacity"):
        load_scenario(_write(tmp_path, text))


def test_rejects_low_altitude(tmp_path: Path) -> None:
    text = _SCENARIO.replace("agl_m: 400000.0", "agl_m: 90000.0", 1)
    with pytest.raises(DocumentError, match="100000"):
        load_scenario(_write(tmp_path, text))


def test_rejects_unknown_scenario_field(tmp_path: Path) -> None:
    text = _SCENARIO.replace("electrolysis: off", "bonus: 1\nelectrolysis: off")
    with pytest.raises(DocumentError, match="unknown field"):
        load_scenario(_write(tmp_path, text))


def test_heliocentric_initial_is_copied(tmp_path: Path) -> None:
    text = _SCENARIO.replace(
        """  mode: earth_circular_equatorial
  agl_m: 400000.0
  longitude_deg: 0.0
""",
        f"""  mode: heliocentric
  position_m: [{AU_M}, 0.0, 0.0]
  velocity_m_per_s: [0.0, 29784.0, 0.0]
""",
    )
    scenario = load_scenario(_write(tmp_path, text))
    position, velocity = initial_rv(scenario, AnalyticEphemeris(scenario.epoch))
    assert position[0] == pytest.approx(AU_M)
    assert velocity[1] == pytest.approx(29784.0)
