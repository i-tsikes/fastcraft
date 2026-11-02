"""Lambert check and a fast preset fill. No Cowell grid."""

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from fastcraft.ephemeris import AnalyticEphemeris
from fastcraft.lambert import lambert
from fastcraft.phasing import departure_candidates
from fastcraft.presets import plan_preset
from fastcraft.scenario import load_scenario
from fastcraft.units import AU_M, MU_SUN_M3_PER_S2
from tests.test_scenario import _write


def test_lambert_quarter_circle() -> None:
    radius = AU_M
    mu = MU_SUN_M3_PER_S2
    speed = math.sqrt(mu / radius)
    period = 2.0 * math.pi * math.sqrt(radius**3 / mu)
    r1 = np.array([radius, 0.0, 0.0])
    r2 = np.array([0.0, radius, 0.0])
    v1, v2 = lambert(r1, r2, period / 4.0, mu)
    assert v1[1] == pytest.approx(speed, rel=1e-6)
    assert v1[0] == pytest.approx(0.0, abs=1e-4)
    assert v2[0] == pytest.approx(-speed, rel=1e-6)
    assert v2[1] == pytest.approx(0.0, abs=1e-4)


def test_fastest_preset_fills_four_burns(tmp_path: Path) -> None:
    scenario = load_scenario(_write(tmp_path))
    from fastcraft.craft import load_craft

    craft = load_craft(tmp_path / "hauler.craft.yaml")
    plan = plan_preset("fastest", scenario, craft, AnalyticEphemeris(scenario.epoch))
    assert plan.applied
    assert [burn.role for burn in plan.burns] == [
        "departure",
        "accelerate",
        "decelerate",
        "insertion",
    ]
    assert all(0.0 <= burn.throttle <= 1.0 for burn in plan.burns)
    assert sum(burn.duration_s for burn in plan.burns) > 0.0
    assert plan.ship == "docked"
    assert plan.arrival_body == "mars"


def test_departure_list_is_sorted_and_short(tmp_path: Path) -> None:
    scenario = load_scenario(_write(tmp_path))
    choices = departure_candidates(scenario, AnalyticEphemeris(scenario.epoch))
    assert 1 <= len(choices) <= 5
    angles = [item.angle_rad for item in choices]
    assert angles == sorted(angles)
    assert choices[0].epoch >= datetime(2035, 4, 1, tzinfo=UTC)
