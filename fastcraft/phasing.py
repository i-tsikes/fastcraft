"""Departure-date pick list, then a one-day Cowell search around one date.

The pick list samples the ephemeris only. The refine step is the part that
runs the saved burns. It writes nothing; the caller writes `epoch` after the
user accepts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import numpy as np

from fastcraft.craft import Craft
from fastcraft.ephemeris import Ephemeris
from fastcraft.propagate import propagate
from fastcraft.scenario import Scenario
from fastcraft.units import (
    DAY_S,
    G0_M_PER_S2,
    MU_SUN_M3_PER_S2,
    SYNODIC_EARTH_MARS_S,
    body_mu,
    body_radius_m,
)


@dataclass(frozen=True)
class DateChoice:
    epoch: datetime
    flight_s: float
    angle_rad: float

    @property
    def angle_deg(self) -> float:
        return math.degrees(self.angle_rad)


def departure_candidates(
    scenario: Scenario,
    ephemeris: Ephemeris,
    *,
    limit: int = 5,
    step_s: float = 5.0 * DAY_S,
) -> list[DateChoice]:
    """Best handful of epochs over one Earth-Mars synodic period."""
    flight = sum(burn.duration_s for burn in scenario.burns)
    origin, dest = _ends(scenario)
    found: list[DateChoice] = []
    cursor = 0.0
    while cursor <= SYNODIC_EARTH_MARS_S + 1.0:
        epoch = scenario.epoch + timedelta(seconds=cursor)
        local = ephemeris.with_epoch(epoch)
        r_depart, _v_depart = local.state(origin, 0.0)
        r_arrive, _v_arrive = local.state(dest, flight)
        depart_lon = math.atan2(float(r_depart[1]), float(r_depart[0]))
        radius = 0.5 * (float(np.linalg.norm(r_depart)) + float(np.linalg.norm(r_arrive)))
        sweep = flight * math.sqrt(MU_SUN_M3_PER_S2 / radius**3) if radius > 0.0 else 0.0
        arrive_lon = math.atan2(float(r_arrive[1]), float(r_arrive[0]))
        angle = abs(_wrap(arrive_lon - (depart_lon + sweep)))
        found.append(DateChoice(epoch=epoch, flight_s=flight, angle_rad=angle))
        cursor += step_s
    found.sort(key=lambda item: item.angle_rad)
    return found[:limit]


def refine_epoch(
    scenario: Scenario,
    craft: Craft,
    ephemeris: Ephemeris,
    center: datetime,
    *,
    window_days: int = 5,
    step_days: int = 1,
    cancel: object | None = None,
) -> tuple[datetime, float]:
    """Shift `center` by whole days, propagate, and return the best epoch and score.

    The score is miss distance at the arrival altitude plus the hydrolox mass
    still needed to circularize. Nothing is written back.
    """
    best_epoch = center
    best_score = math.inf
    for day in range(-window_days, window_days + 1, step_days):
        if callable(cancel) and cancel():
            break
        epoch = center + timedelta(days=day)
        trial = replace(scenario, epoch=epoch)
        result = propagate(trial, craft, ephemeris.with_epoch(epoch))
        score = arrival_score(
            result.position_m[-1],
            result.velocity_m_per_s[-1],
            result.mass_kg[-1],
            result.t[-1],
            trial,
            craft,
            ephemeris.with_epoch(epoch),
        )
        if score < best_score:
            best_score = score
            best_epoch = epoch
    return best_epoch, best_score


def arrival_score(
    position: np.ndarray,
    velocity: np.ndarray,
    mass_kg: float,
    t_s: float,
    scenario: Scenario,
    craft: Craft,
    ephemeris: Ephemeris,
) -> float:
    """Meters of radial miss plus 1000 m per kg of hydrolox still required."""
    body = scenario.arrival.body
    body_r, body_v = ephemeris.state(body, float(t_s))
    relative_r = np.asarray(position, dtype=np.float64) - body_r
    relative_v = np.asarray(velocity, dtype=np.float64) - body_v
    distance = float(np.linalg.norm(relative_r))
    target = body_radius_m(body) + scenario.arrival.agl_m
    miss = abs(distance - target)
    speed = float(np.linalg.norm(relative_v))
    radial = float(np.dot(relative_v, relative_r) / distance) if distance > 0.0 else 0.0
    tangential = math.sqrt(max(speed * speed - radial * radial, 0.0))
    circular = math.sqrt(body_mu(body) / max(distance, target))
    delta_v = math.sqrt((tangential - circular) ** 2 + radial * radial)
    isp = craft.engines.hydrolox.isp_s
    if isp <= 0.0 or delta_v <= 0.0 or mass_kg <= 0.0:
        hydrolox = 0.0
    else:
        hydrolox = float(mass_kg) * (1.0 - math.exp(-delta_v / (isp * G0_M_PER_S2)))
    return miss + hydrolox * 1_000.0


def _ends(scenario: Scenario) -> tuple[str, str]:
    dest = scenario.arrival.body
    origin = scenario.departure_body
    if origin is None:
        origin = "mars" if dest == "earth" else "earth"
    return origin, dest


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
