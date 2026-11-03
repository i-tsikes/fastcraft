"""Body states in the heliocentric ecliptic J2000 frame.

Callers see `state` and `mu` only. A local JPL kernel is read with Skyfield
when one is configured. Otherwise a Keplerian Sun, Earth, and Mars stand in
so tests and the app run without downloading a kernel.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from fastcraft.settings import kernel_path
from fastcraft.units import AU_M, MU_SUN_M3_PER_S2, body_mu

_J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)


class Ephemeris(Protocol):
    """Heliocentric ecliptic J2000 body states. `t_s` is seconds past `epoch`."""

    epoch: datetime

    def state(self, body: str, t_s: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...

    def mu(self, body: str) -> float: ...

    def with_epoch(self, epoch: datetime) -> Ephemeris: ...


def open_ephemeris(epoch: datetime, kernel: Path | None = None) -> Ephemeris:
    """Open a kernel ephemeris, or the analytic one when no kernel is set."""
    path = kernel if kernel is not None else _kernel_from_env()
    if path is None:
        return AnalyticEphemeris(epoch)
    return SkyfieldEphemeris(path, epoch)


def _kernel_from_env() -> Path | None:
    env = os.environ.get("FASTCRAFT_KERNEL")
    if env:
        return Path(env)
    return kernel_path()


@dataclass(frozen=True)
class _Orbit:
    a_m: float
    eccentricity: float
    inclination_rad: float
    mean_longitude_rad: float
    perihelion_rad: float
    node_rad: float


_EARTH = _Orbit(
    a_m=1.00000261 * AU_M,
    eccentricity=0.01671123,
    inclination_rad=math.radians(-0.00001531),
    mean_longitude_rad=math.radians(100.46457166),
    perihelion_rad=math.radians(102.93768193),
    node_rad=0.0,
)
_MARS = _Orbit(
    a_m=1.52371034 * AU_M,
    eccentricity=0.09339410,
    inclination_rad=math.radians(1.84969142),
    mean_longitude_rad=math.radians(-4.55343205),
    perihelion_rad=math.radians(-23.94362959),
    node_rad=math.radians(49.55953891),
)


class AnalyticEphemeris:
    """Keplerian Sun, Earth, and Mars. No network and no kernel file."""

    def __init__(self, epoch: datetime) -> None:
        self.epoch = epoch if epoch.tzinfo is not None else epoch.replace(tzinfo=UTC)

    def with_epoch(self, epoch: datetime) -> AnalyticEphemeris:
        return AnalyticEphemeris(epoch)

    def mu(self, body: str) -> float:
        return body_mu(body)

    def state(self, body: str, t_s: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        name = body.lower()
        if name == "sun":
            zero = np.zeros(3, dtype=np.float64)
            return zero, zero.copy()
        orbits = {"earth": _EARTH, "mars": _MARS}
        try:
            orbit = orbits[name]
        except KeyError as exc:
            raise KeyError(f"unknown body {body!r}") from exc
        when = self.epoch + timedelta(seconds=float(t_s))
        return _kepler_state(orbit, when)


class SkyfieldEphemeris:
    """JPL DE kernel via Skyfield, rotated into ecliptic J2000."""

    def __init__(
        self, kernel: Path, epoch: datetime, *, _cache: tuple[Any, Any] | None = None
    ) -> None:
        self.kernel = kernel
        self.epoch = epoch if epoch.tzinfo is not None else epoch.replace(tzinfo=UTC)
        if not kernel.is_file():
            raise FileNotFoundError(
                f"JPL kernel not found at {kernel}. "
                "Pass a local .bsp path; fastcraft does not download one."
            )
        if _cache is None:
            self._eph, self._ts = _load_kernel(kernel)
        else:
            self._eph, self._ts = _cache

    def with_epoch(self, epoch: datetime) -> SkyfieldEphemeris:
        return SkyfieldEphemeris(self.kernel, epoch, _cache=(self._eph, self._ts))

    def mu(self, body: str) -> float:
        return body_mu(body)

    def state(self, body: str, t_s: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        name = body.lower()
        if name == "sun":
            zero = np.zeros(3, dtype=np.float64)
            return zero, zero.copy()
        when = self.epoch + timedelta(seconds=float(t_s))
        target = _spice_body(self._eph, name)
        sun = _spice_body(self._eph, "sun")
        time = self._ts.from_datetime(when.astimezone(UTC))
        observed = (target - sun).at(time)
        position = np.asarray(observed.ecliptic_xyz().m, dtype=np.float64).reshape(3)
        velocity = np.asarray(observed.ecliptic_velocity().m_per_s, dtype=np.float64).reshape(3)
        return position, velocity


def _load_kernel(kernel: Path) -> tuple[Any, Any]:
    from skyfield.api import load, load_file

    return load_file(str(kernel)), load.timescale(builtin=True)


def _spice_body(ephemeris: Any, name: str) -> Any:
    aliases = {
        "sun": ("sun",),
        "earth": ("earth", "earth_barycenter"),
        "mars": ("mars", "mars_barycenter"),
    }
    try:
        options = aliases[name]
    except KeyError as exc:
        raise KeyError(f"unknown body {name!r}") from exc
    last: KeyError | None = None
    for alias in options:
        try:
            return ephemeris[alias]
        except KeyError as exc:
            last = exc
    raise KeyError(f"kernel has no segment for {name}") from last


def _kepler_state(orbit: _Orbit, when: datetime) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    elapsed = (when - _J2000).total_seconds()
    mean_motion = math.sqrt(MU_SUN_M3_PER_S2 / orbit.a_m**3)
    mean_anomaly = _wrap(orbit.mean_longitude_rad + mean_motion * elapsed - orbit.perihelion_rad)
    eccentric = _solve_kepler(mean_anomaly, orbit.eccentricity)
    cosine = math.cos(eccentric)
    sine = math.sin(eccentric)
    radius = orbit.a_m * (1.0 - orbit.eccentricity * cosine)
    factor = math.sqrt(MU_SUN_M3_PER_S2 * orbit.a_m) / radius
    perifocal_r = np.array(
        [
            orbit.a_m * (cosine - orbit.eccentricity),
            orbit.a_m * math.sqrt(1.0 - orbit.eccentricity**2) * sine,
            0.0,
        ],
        dtype=np.float64,
    )
    perifocal_v = np.array(
        [-factor * sine, factor * math.sqrt(1.0 - orbit.eccentricity**2) * cosine, 0.0],
        dtype=np.float64,
    )
    argument = orbit.perihelion_rad - orbit.node_rad
    rotation = _rotation(orbit.node_rad, orbit.inclination_rad, argument)
    return rotation @ perifocal_r, rotation @ perifocal_v


def _solve_kepler(mean_anomaly: float, eccentricity: float) -> float:
    eccentric = mean_anomaly if eccentricity < 0.8 else math.pi
    for _ in range(30):
        residual = eccentric - eccentricity * math.sin(eccentric) - mean_anomaly
        slope = 1.0 - eccentricity * math.cos(eccentric)
        step = residual / slope
        eccentric -= step
        if abs(step) < 1e-14:
            break
    return eccentric


def _rotation(node: float, inclination: float, argument: float) -> NDArray[np.float64]:
    return _rot_z(node) @ _rot_x(inclination) @ _rot_z(argument)


def _rot_z(angle: float) -> NDArray[np.float64]:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array(
        [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def _rot_x(angle: float) -> NDArray[np.float64]:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array(
        [[1.0, 0.0, 0.0], [0.0, cosine, -sine], [0.0, sine, cosine]],
        dtype=np.float64,
    )


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
