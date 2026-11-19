"""One-year solar coast. Energy, angular momentum, and radius stay bounded."""

import math
from pathlib import Path

import numpy as np

from fastcraft.cli import main
from fastcraft.results import RunResult
from fastcraft.units import AU_M, JULIAN_YEAR_S, MU_SUN_M3_PER_S2

_CRAFT = """\
schema: fastcraft.craft/v1
name: Coast
dry_mass_kg: 1000.0
tanks:
  water_kg: 0.0
  lh2_kg: 0.0
  lox_kg: 0.0
starship:
  dry_mass_kg: 0.0
  landing_propellant_kg: 0.0
power:
  reactor_thermal_w: 1.0
  electric_w: 0.0
  radiator_reject_w: 1.0
electrolysis:
  max_kg_per_s: 0.0
engines:
  hydrolox:
    count: 1
    thrust_n: 0.0
    isp_s: 0.0
  mhd:
    count: 1
    thrust_n: 0.0
    isp_s: 0.0
geometry:
  tank_length_m: 1.0
  tank_radius_m: 1.0
  radiator_span_m: 0.0
notes: ""
"""


def test_coast_conserves_energy_and_radius(tmp_path: Path) -> None:
    speed = math.sqrt(MU_SUN_M3_PER_S2 / AU_M)
    scenario = tmp_path / "coast.scenario.yaml"
    (tmp_path / "coast.craft.yaml").write_text(_CRAFT, encoding="utf-8")
    scenario.write_text(
        f"""\
schema: fastcraft.scenario/v1
name: Solar coast
craft: coast.craft.yaml
epoch: "2035-01-01T00:00:00Z"
frame: heliocentric_ecliptic_j2000
bodies: [sun]
initial:
  mode: heliocentric
  position_m: [{AU_M}, 0.0, 0.0]
  velocity_m_per_s: [0.0, {speed}, 0.0]
  propellant:
    water_kg: 0.0
    lh2_kg: 0.0
    lox_kg: 0.0
arrival:
  body: mars
  agl_m: 400000.0
ship: none
burns:
  - role: departure
    duration_s: {JULIAN_YEAR_S}
    throttle: 0.0
  - role: accelerate
    duration_s: 0.0
    throttle: 0.0
    oxygen: off
  - role: decelerate
    duration_s: 0.0
    throttle: 0.0
    oxygen: off
  - role: insertion
    duration_s: 0.0
    throttle: 0.0
electrolysis: off
""",
        encoding="utf-8",
    )
    assert main([str(scenario)]) == 0
    playback = next(tmp_path.glob("*.npz"))
    result = RunResult.load(playback)
    radius = result.radius_m
    speed_series = result.speed_m_per_s
    energy = speed_series**2 / 2.0 - MU_SUN_M3_PER_S2 / radius
    angular = np.linalg.norm(np.cross(result.position_m, result.velocity_m_per_s), axis=1)
    assert np.max(np.abs(energy - energy[0]) / np.abs(energy[0])) < 1e-6
    assert np.max(np.abs(angular - angular[0]) / np.abs(angular[0])) < 1e-6
    assert abs(float(radius[-1]) - AU_M) / AU_M < 1e-4
    assert abs(float(result.t[-1]) - JULIAN_YEAR_S) < 1.0
    assert np.max(result.thrust_n) == 0.0
