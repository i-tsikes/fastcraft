"""Hydrolox burn against the rocket equation, and a tank that runs dry."""

import math
from pathlib import Path

import numpy as np

from fastcraft.cli import main
from fastcraft.results import RunResult, playback_path
from fastcraft.units import AU_M, G0_M_PER_S2, MU_SUN_M3_PER_S2

_HEADER = """\
schema: fastcraft.craft/v1
name: Burner
dry_mass_kg: {dry}
tanks:
  water_kg: 0.0
  lh2_kg: {lh2_cap}
  lox_kg: {lox_cap}
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
    thrust_n: {thrust}
    isp_s: {isp}
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


def _scenario(
    tmp_path: Path,
    *,
    dry: float,
    lh2: float,
    lox: float,
    thrust: float,
    isp: float,
    duration: float,
    name: str,
) -> Path:
    (tmp_path / "burner.craft.yaml").write_text(
        _HEADER.format(dry=dry, lh2_cap=lh2, lox_cap=lox, thrust=thrust, isp=isp),
        encoding="utf-8",
    )
    speed = math.sqrt(MU_SUN_M3_PER_S2 / AU_M)
    path = tmp_path / f"{name}.scenario.yaml"
    path.write_text(
        f"""\
schema: fastcraft.scenario/v1
name: {name}
craft: burner.craft.yaml
epoch: "2035-01-01T00:00:00Z"
frame: heliocentric_ecliptic_j2000
bodies: [sun]
initial:
  mode: heliocentric
  position_m: [{AU_M}, 0.0, 0.0]
  velocity_m_per_s: [0.0, {speed}, 0.0]
  propellant:
    water_kg: 0.0
    lh2_kg: {lh2}
    lox_kg: {lox}
arrival:
  body: mars
  agl_m: 400000.0
ship: none
burns:
  - role: departure
    duration_s: {duration}
    throttle: 1.0
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
    return path


def test_burn_matches_rocket_equation(tmp_path: Path) -> None:
    isp = 300.0
    path = _scenario(
        tmp_path,
        dry=10_000.0,
        lh2=500.0,
        lox=4_000.0,
        thrust=1_000.0,
        isp=isp,
        duration=7_200.0,
        name="burn",
    )
    assert main([str(path)]) == 0
    result = RunResult.load(next(tmp_path.glob("*.npz")))
    m0 = float(result.mass_kg[0])
    m1 = float(result.mass_kg[-1])
    expected = isp * G0_M_PER_S2 * math.log(m0 / m1)
    assert result.delta_v_m_per_s == pytest_approx(expected, rel=1e-3)
    consumed = (float(result.lh2_kg[0]) + float(result.lox_kg[0])) - (
        float(result.lh2_kg[-1]) + float(result.lox_kg[-1])
    )
    assert consumed == pytest_approx(m0 - m1, rel=1e-6)
    assert m0 > m1


def test_empty_tank_shuts_the_engine_off(tmp_path: Path) -> None:
    path = _scenario(
        tmp_path,
        dry=1_000.0,
        lh2=1.0,
        lox=8.0,
        thrust=1_000.0,
        isp=300.0,
        duration=3_600.0,
        name="empty",
    )
    assert main([str(path)]) == 0
    result = RunResult.load(next(tmp_path.glob("*.npz")))
    assert result.lh2_kg[-1] == 0.0 or result.lox_kg[-1] == 0.0
    depleted = np.where((result.lh2_kg == 0.0) | (result.lox_kg == 0.0))[0]
    assert depleted.size > 0
    assert np.all(result.thrust_n[int(depleted[0]) :] == 0.0)


def test_playback_name_uses_local_stamp(tmp_path: Path) -> None:
    from datetime import datetime

    path = tmp_path / "scenario462.scenario.yaml"
    stamp = datetime(2026, 11, 2, 2, 1, 9)
    assert playback_path(path, stamp).name == "scenario462-2026-11-02-020109.npz"


def pytest_approx(value: float, *, rel: float):
    import pytest

    return pytest.approx(value, rel=rel)
