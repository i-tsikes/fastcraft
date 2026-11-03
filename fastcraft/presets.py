"""Fill burn duration and throttle fields.

The search is patched-conic: a Lambert transfer plus the rocket equation.
It does not run the Cowell integrator. The departure-date refine does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from fastcraft.craft import Craft
from fastcraft.ephemeris import Ephemeris
from fastcraft.lambert import LambertError, lambert
from fastcraft.scenario import Burn, CircularInitial, Propellant, Scenario
from fastcraft.units import G0_M_PER_S2, MU_SUN_M3_PER_S2, body_mu, body_radius_m

_KINDS = ("fastest", "return_none", "return_empty", "return_loaded")


@dataclass(frozen=True)
class PresetPlan:
    burns: tuple[Burn, Burn, Burn, Burn]
    ship: str
    cargo_kg: float
    arrival_body: str
    initial_mode: str | None
    fill_propellant: bool
    message: str
    applied: bool


def plan_preset(
    kind: str,
    scenario: Scenario,
    craft: Craft,
    ephemeris: Ephemeris,
    *,
    cargo_kg: float = 0.0,
) -> PresetPlan:
    """Search durations and throttles and return the fields to write."""
    if kind not in _KINDS:
        raise ValueError(f"unknown preset {kind!r}")
    ship, arrival, mode, cargo, propellant, fill = _case(kind, scenario, craft, cargo_kg)
    origin = "earth" if kind == "fastest" else "mars"
    origin_agl, dest_agl = _altitudes(scenario, kind)
    unchanged = _unchanged(scenario, ship, cargo, arrival, mode, fill)
    engine = craft.engines.hydrolox
    if engine.count <= 0 or engine.thrust_n <= 0.0 or engine.isp_s <= 0.0:
        return PresetPlan(
            *unchanged,
            message="Hydrolox thrust and specific impulse are required before a preset can run.",
            applied=False,
        )
    mdot = engine.count * engine.thrust_n / (engine.isp_s * G0_M_PER_S2)
    mixture = 9.0 * min(propellant.lh2_kg, propellant.lox_kg / 8.0)
    if mixture <= 0.0 or mdot <= 0.0:
        return PresetPlan(
            *unchanged,
            message="The tanks do not hold a hydrolox mixture, so no preset was written.",
            applied=False,
        )
    fixed = _fixed_mass(craft, ship, cargo)
    m0 = fixed + propellant.water_kg + propellant.lh2_kg + propellant.lox_kg
    best: _Candidate | None = None
    best = _consider_coast(
        best,
        scenario.epoch,
        ephemeris,
        origin,
        arrival,
        origin_agl,
        dest_agl,
        m0,
        engine.isp_s,
        mdot,
        mixture,
    )
    best = _consider_powered(
        best,
        scenario.epoch,
        ephemeris,
        craft,
        origin,
        arrival,
        origin_agl,
        dest_agl,
        m0,
        engine.isp_s,
        mdot,
        mixture,
        propellant,
    )
    if best is None or not best.feasible:
        if kind != "fastest":
            return PresetPlan(
                *unchanged,
                message=(
                    "No return plan fits in the tanks while leaving the propellant that "
                    "four-burn arrival needs. Fields were left unchanged."
                ),
                applied=False,
            )
        if best is None:
            return PresetPlan(
                *unchanged,
                message="The preset search did not find a transfer. Fields were left unchanged.",
                applied=False,
            )
    assert best is not None
    oxygen = "auto" if craft.has_oxygen else "off"
    burns = (
        Burn("departure", best.departure_s, 1.0 if best.departure_s > 0.0 else 0.0, None),
        Burn("accelerate", best.accelerate_s, best.accelerate_throttle, oxygen),
        Burn("decelerate", best.decelerate_s, best.decelerate_throttle, oxygen),
        Burn("insertion", best.insertion_s, 1.0 if best.insertion_s > 0.0 else 0.0, None),
    )
    days = best.tof_s / 86_400.0
    note = (
        "closes the patched-conic transfer"
        if best.feasible
        else "is the closest split, but it does not yet close"
    )
    return PresetPlan(
        burns=burns,
        ship=ship,
        cargo_kg=cargo,
        arrival_body=arrival,
        initial_mode=mode,
        fill_propellant=fill,
        message=(
            f"Preset filled a {days:.1f} day flight that {note}. Edit any duration or throttle."
        ),
        applied=True,
    )


@dataclass(frozen=True)
class _Candidate:
    tof_s: float
    departure_s: float
    accelerate_s: float
    decelerate_s: float
    insertion_s: float
    accelerate_throttle: float
    decelerate_throttle: float
    feasible: bool
    ratio: float


def _consider_coast(
    best: _Candidate | None,
    epoch: datetime,
    ephemeris: Ephemeris,
    origin: str,
    dest: str,
    origin_agl: float,
    dest_agl: float,
    m0: float,
    isp: float,
    mdot: float,
    mixture: float,
) -> _Candidate | None:
    for day in range(40, 421, 5):
        tof = float(day) * 86_400.0
        needed = _transfer_dv(ephemeris, epoch, tof, origin, dest, origin_agl, dest_agl)
        if needed is None:
            continue
        used_dep = _propellant_for_dv(m0, needed[0], isp)
        if used_dep is None or used_dep > mixture:
            continue
        after = m0 - used_dep
        used_ins = _propellant_for_dv(after, needed[1], isp)
        if used_ins is None or used_dep + used_ins > mixture:
            continue
        t_dep = used_dep / mdot
        t_ins = used_ins / mdot
        coast = tof - t_dep - t_ins
        if coast < 0.0:
            continue
        candidate = _Candidate(
            tof_s=tof,
            departure_s=t_dep,
            accelerate_s=coast,
            decelerate_s=0.0,
            insertion_s=t_ins,
            accelerate_throttle=0.0,
            decelerate_throttle=0.0,
            feasible=True,
            ratio=1.0,
        )
        best = _prefer(best, candidate)
    return best


def _consider_powered(
    best: _Candidate | None,
    epoch: datetime,
    ephemeris: Ephemeris,
    craft: Craft,
    origin: str,
    dest: str,
    origin_agl: float,
    dest_agl: float,
    m0: float,
    isp: float,
    mdot: float,
    mixture: float,
    propellant: Propellant,
) -> _Candidate | None:
    mhd = craft.engines.mhd
    if mhd.count <= 0 or mhd.thrust_n <= 0.0 or mhd.isp_s <= 0.0:
        return best
    mdot_mhd = mhd.count * mhd.thrust_n / (mhd.isp_s * G0_M_PER_S2)
    for chem_frac in (0.25, 0.5, 0.75, 1.0):
        mix = mixture * chem_frac
        used_dep = mix * 0.6
        used_ins = mix * 0.4
        lh2_left = propellant.lh2_kg - mix / 9.0
        lox_left = propellant.lox_kg - mix * 8.0 / 9.0
        if lh2_left < 0.0 or lox_left < 0.0:
            continue
        lh2_mhd = lh2_left
        if craft.has_oxygen:
            lh2_mhd = min(lh2_mhd, lox_left / 8.0)
        if lh2_mhd <= 0.0:
            continue
        t_mhd = lh2_mhd / mdot_mhd
        t_dep = used_dep / mdot
        t_ins = used_ins / mdot
        tof = t_dep + t_mhd + t_ins
        lox_mhd = 8.0 * lh2_mhd if craft.has_oxygen else 0.0
        after_cruise = m0 - used_dep - lh2_mhd - lox_mhd
        if after_cruise <= used_ins:
            continue
        have_dep = _dv_from_propellant(m0, used_dep, isp)
        have_ins = _dv_from_propellant(after_cruise, used_ins, isp)
        needed = _transfer_dv(ephemeris, epoch, tof, origin, dest, origin_agl, dest_agl)
        feasible = False
        ratio = 0.0
        if needed is not None and needed[0] > 0.0 and needed[1] > 0.0:
            ratio = min(have_dep / needed[0], have_ins / needed[1])
            feasible = ratio >= 1.0
        candidate = _Candidate(
            tof_s=tof,
            departure_s=t_dep,
            accelerate_s=0.55 * t_mhd,
            decelerate_s=0.45 * t_mhd,
            insertion_s=t_ins,
            accelerate_throttle=1.0,
            decelerate_throttle=1.0,
            feasible=feasible,
            ratio=ratio,
        )
        best = _prefer(best, candidate)
    return best


def _prefer(best: _Candidate | None, candidate: _Candidate) -> _Candidate:
    if best is None:
        return candidate
    if candidate.feasible and not best.feasible:
        return candidate
    if best.feasible and not candidate.feasible:
        return best
    if candidate.feasible and best.feasible:
        return candidate if candidate.tof_s < best.tof_s else best
    return candidate if candidate.ratio > best.ratio else best


def _transfer_dv(
    ephemeris: Ephemeris,
    epoch: datetime,
    tof_s: float,
    origin: str,
    dest: str,
    origin_agl: float,
    dest_agl: float,
) -> tuple[float, float] | None:
    local = ephemeris.with_epoch(epoch)
    r1, v1 = local.state(origin, 0.0)
    r2, v2 = local.state(dest, tof_s)
    try:
        depart, arrive = lambert(r1, r2, tof_s, MU_SUN_M3_PER_S2)
    except LambertError:
        return None
    vinf_depart = float(np.linalg.norm(depart - v1))
    vinf_arrive = float(np.linalg.norm(arrive - v2))
    return (
        _capture_dv(vinf_depart, origin, origin_agl),
        _capture_dv(vinf_arrive, dest, dest_agl),
    )


def _capture_dv(v_infinity: float, body: str, agl_m: float) -> float:
    radius = body_radius_m(body) + agl_m
    circular = math.sqrt(body_mu(body) / radius)
    escape = math.sqrt(2.0) * circular
    return math.sqrt(v_infinity * v_infinity + escape * escape) - circular


def _propellant_for_dv(mass: float, delta_v: float, isp_s: float) -> float | None:
    if delta_v <= 0.0:
        return 0.0
    if isp_s <= 0.0 or mass <= 0.0:
        return None
    remaining = mass * math.exp(-delta_v / (isp_s * G0_M_PER_S2))
    used = mass - remaining
    if used >= mass:
        return None
    return used


def _dv_from_propellant(mass: float, used: float, isp_s: float) -> float:
    if used <= 0.0 or mass <= used:
        return 0.0
    return isp_s * G0_M_PER_S2 * math.log(mass / (mass - used))


def _case(
    kind: str,
    scenario: Scenario,
    craft: Craft,
    cargo_kg: float,
) -> tuple[str, str, str | None, float, Propellant, bool]:
    if kind == "fastest":
        propellant = Propellant(craft.tanks.water_kg, craft.tanks.lh2_kg, craft.tanks.lox_kg)
        return "docked", "mars", "earth_circular_equatorial", 0.0, propellant, True
    propellant = scenario.initial.propellant
    if kind == "return_none":
        return "none", "earth", "mars_circular_equatorial", 0.0, propellant, False
    if kind == "return_empty":
        return "empty", "earth", "mars_circular_equatorial", 0.0, propellant, False
    return "loaded", "earth", "mars_circular_equatorial", max(cargo_kg, 0.0), propellant, False


def _altitudes(scenario: Scenario, kind: str) -> tuple[float, float]:
    initial_agl = (
        scenario.initial.agl_m if isinstance(scenario.initial, CircularInitial) else 400_000.0
    )
    arrival_agl = scenario.arrival.agl_m
    if kind == "fastest":
        return initial_agl, arrival_agl
    return arrival_agl, initial_agl


def _fixed_mass(craft: Craft, ship: str, cargo_kg: float) -> float:
    mass = craft.dry_mass_kg
    if ship == "none":
        return mass
    mass += craft.starship.dry_mass_kg + craft.starship.landing_propellant_kg
    if ship == "loaded":
        mass += cargo_kg
    return mass


def _unchanged(
    scenario: Scenario,
    ship: str,
    cargo: float,
    arrival: str,
    mode: str | None,
    fill: bool,
) -> tuple[tuple[Burn, Burn, Burn, Burn], str, float, str, str | None, bool]:
    burns = scenario.burns
    quad = (burns[0], burns[1], burns[2], burns[3])
    return quad, ship, cargo, arrival, mode, fill
