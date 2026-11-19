"""Universal-variable Lambert solver for the patched-conic preset search.

The integrator does not call this. Presets use it to pick burn durations
without running the Cowell solver across a grid.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray


class LambertError(ValueError):
    """No transfer was found for this geometry and time of flight."""


def lambert(
    position_1: NDArray[np.float64],
    position_2: NDArray[np.float64],
    tof_s: float,
    mu: float,
    *,
    prograde: bool = True,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return departure and arrival velocity vectors, m/s.

    Uses Curtis's formulation with Stumpff functions and a bisection on the
    universal variable. The transfer angle follows the prograde branch.
    """
    if tof_s <= 0.0:
        raise LambertError("time of flight must be positive")
    radius_1 = float(np.linalg.norm(position_1))
    radius_2 = float(np.linalg.norm(position_2))
    if radius_1 == 0.0 or radius_2 == 0.0:
        raise LambertError("position is at the origin")
    cross = np.cross(position_1, position_2)
    cosine = float(np.clip(np.dot(position_1, position_2) / (radius_1 * radius_2), -1.0, 1.0))
    angle = math.acos(cosine)
    if prograde:
        if float(cross[2]) < 0.0:
            angle = 2.0 * math.pi - angle
    elif float(cross[2]) >= 0.0:
        angle = 2.0 * math.pi - angle
    if abs(math.sin(angle)) < 1e-8:
        raise LambertError("transfer angle is too close to 0 or 180 degrees")
    chord = math.sin(angle) * math.sqrt(radius_1 * radius_2 / (1.0 - math.cos(angle)))
    target = math.sqrt(mu) * tof_s
    z_low = _lower_bound(radius_1, radius_2, chord)
    z_high = 4.0 * math.pi**2
    f_low = _time_function(z_low, radius_1, radius_2, chord) - target
    f_high = _time_function(z_high, radius_1, radius_2, chord) - target
    for _ in range(24):
        if math.isfinite(f_low) and math.isfinite(f_high) and f_low * f_high < 0.0:
            break
        z_high *= 2.0
        f_high = _time_function(z_high, radius_1, radius_2, chord) - target
    else:
        raise LambertError("failed to bracket the transfer")
    z = 0.5 * (z_low + z_high)
    for _ in range(80):
        z = 0.5 * (z_low + z_high)
        residual = _time_function(z, radius_1, radius_2, chord) - target
        if abs(residual) < 1e-8 * target or abs(z_high - z_low) < 1e-12:
            break
        if f_low * residual > 0.0:
            z_low = z
            f_low = residual
        else:
            z_high = z
    y_value = _y(z, radius_1, radius_2, chord)
    if y_value <= 0.0:
        raise LambertError("transfer geometry is invalid")
    lagrange_f = 1.0 - y_value / radius_1
    lagrange_g = chord * math.sqrt(y_value / mu)
    lagrange_gdot = 1.0 - y_value / radius_2
    if abs(lagrange_g) < 1e-12:
        raise LambertError("transfer is singular")
    velocity_1 = (position_2 - lagrange_f * position_1) / lagrange_g
    velocity_2 = (lagrange_gdot * position_2 - position_1) / lagrange_g
    return velocity_1, velocity_2


def _lower_bound(radius_1: float, radius_2: float, chord: float) -> float:
    z_low = -8.0 * math.pi
    for _ in range(40):
        if _y(z_low, radius_1, radius_2, chord) > 0.0:
            return z_low
        z_low += 0.5
    return -1.0


def _time_function(z: float, radius_1: float, radius_2: float, chord: float) -> float:
    y_value = _y(z, radius_1, radius_2, chord)
    c_value = _stumpff_c(z)
    if y_value <= 0.0 or c_value <= 0.0:
        return math.inf
    return math.pow(y_value / c_value, 1.5) * _stumpff_s(z) + chord * math.sqrt(y_value)


def _y(z: float, radius_1: float, radius_2: float, chord: float) -> float:
    c_value = _stumpff_c(z)
    if c_value <= 0.0:
        return -1.0
    return radius_1 + radius_2 + chord * (z * _stumpff_s(z) - 1.0) / math.sqrt(c_value)


def _stumpff_c(z: float) -> float:
    if abs(z) < 1e-6:
        return 0.5 - z / 24.0 + z * z / 720.0
    if z > 0.0:
        root = math.sqrt(z)
        return (1.0 - math.cos(root)) / z
    root = math.sqrt(-z)
    return (math.cosh(root) - 1.0) / (-z)


def _stumpff_s(z: float) -> float:
    if abs(z) < 1e-6:
        return 1.0 / 6.0 - z / 120.0 + z * z / 5040.0
    if z > 0.0:
        root = math.sqrt(z)
        return (root - math.sin(root)) / (root**3)
    root = math.sqrt(-z)
    return (math.sinh(root) - root) / (root**3)
