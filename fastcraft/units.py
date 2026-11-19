"""SI constants and display helpers.

Files store plain numbers. Integration uses seconds past the scenario epoch
and radians. Angles in documents are degrees.
"""

from __future__ import annotations

import math

G0_M_PER_S2: float = 9.80665
AU_M: float = 149_597_870_700.0
DAY_S: float = 86_400.0
JULIAN_YEAR_S: float = 365.25 * DAY_S
WGS84_EQUATORIAL_RADIUS_M: float = 6_378_137.0
MARS_EQUATORIAL_RADIUS_M: float = 3_396_190.0
MIN_AGL_M: float = 100_000.0
MAX_STEP_S: float = 3_600.0
OBLIQUITY_RAD: float = math.radians(23.4392911)
MARS_OBLIQUITY_RAD: float = math.radians(25.19)
# Mean Earth-Mars synodic period.
SYNODIC_EARTH_MARS_S: float = 779.94 * DAY_S

MU_SUN_M3_PER_S2: float = 1.32712440018e20
MU_EARTH_M3_PER_S2: float = 3.986004418e14
MU_MARS_M3_PER_S2: float = 4.2828372e13

CRAFT_SCHEMA: str = "fastcraft.craft/v1"
SCENARIO_SCHEMA: str = "fastcraft.scenario/v1"
FRAME: str = "heliocentric_ecliptic_j2000"
KNOWN_BODIES: tuple[str, ...] = ("sun", "earth", "mars")

PHASE_COAST: int = 0
PHASE_DEPARTURE: int = 1
PHASE_ACCELERATE: int = 2
PHASE_DECELERATE: int = 3
PHASE_INSERTION: int = 4

HYDROLOX_RATIO: float = 8.0


def body_mu(name: str) -> float:
    """Gravitational parameter, m^3/s^2."""
    table = {
        "sun": MU_SUN_M3_PER_S2,
        "earth": MU_EARTH_M3_PER_S2,
        "mars": MU_MARS_M3_PER_S2,
    }
    try:
        return table[name]
    except KeyError as exc:
        raise KeyError(f"unknown body {name!r}") from exc


def body_radius_m(name: str) -> float:
    """Equatorial radius used for altitude above the ground, m."""
    table = {
        "sun": 695_700_000.0,
        "earth": WGS84_EQUATORIAL_RADIUS_M,
        "mars": MARS_EQUATORIAL_RADIUS_M,
    }
    try:
        return table[name]
    except KeyError as exc:
        raise KeyError(f"unknown body {name!r}") from exc


def circular_speed_m_per_s(body: str, agl_m: float) -> float:
    """Speed of a circular equatorial orbit at `agl_m` above `body`."""
    radius = body_radius_m(body) + agl_m
    return math.sqrt(body_mu(body) / radius)


def format_elapsed(seconds: float) -> str:
    """Format a duration as `1d 2h 47m 33s`."""
    sign = "-" if seconds < 0.0 else ""
    total = int(abs(seconds))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    return f"{sign}{days}d {hours}h {minutes}m {secs}s"
