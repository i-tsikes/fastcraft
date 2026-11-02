"""Cowell propagation of a scenario.

State is length 10: heliocentric position (m), velocity (m/s), total mass,
water, LH2, and LOX. Landing propellant is constant and stays inside the
total mass. The integrator is SciPy DOP853 with a 3600 s maximum step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp

from fastcraft.craft import Craft, Engine
from fastcraft.ephemeris import Ephemeris
from fastcraft.results import RunResult
from fastcraft.scenario import Burn, Scenario, fixed_mass_kg, initial_state
from fastcraft.units import (
    G0_M_PER_S2,
    MAX_STEP_S,
    PHASE_ACCELERATE,
    PHASE_DECELERATE,
    PHASE_DEPARTURE,
    PHASE_INSERTION,
)
from fastcraft.yamlio import DocumentError

_RTOL = 1e-10
_ATOL = 1e-8
_DUST_KG = 1e-8

_PHASE = {
    "departure": PHASE_DEPARTURE,
    "accelerate": PHASE_ACCELERATE,
    "decelerate": PHASE_DECELERATE,
    "insertion": PHASE_INSERTION,
}


@dataclass
class _Activity:
    """Which engines are still allowed to fire in the current segment."""

    hydrolox: bool
    mhd: bool
    oxygen: bool
    electrolysis: bool
    follow: int
    direction: NDArray[np.float64]
    phase: int
    throttle: float


class _Dynamics:
    def __init__(
        self,
        craft: Craft,
        ephemeris: Ephemeris,
        bodies: tuple[str, ...],
        activity: _Activity,
        fixed_kg: float,
        capacity: NDArray[np.float64],
    ) -> None:
        self.craft = craft
        self.ephemeris = ephemeris
        self.bodies = bodies
        self.activity = activity
        self.fixed_kg = fixed_kg
        self.capacity = capacity

    def ode(self, t: float, y: NDArray[np.float64]) -> NDArray[np.float64]:
        thrust, _mag, rates = self.thrust_and_rates(y)
        mass = float(y[6]) if float(y[6]) > 1.0 else 1.0
        derivative = np.empty(10, dtype=np.float64)
        derivative[0:3] = y[3:6]
        derivative[3:6] = _gravity(float(t), y[0:3], self.bodies, self.ephemeris) + thrust / mass
        derivative[6] = float(np.sum(rates))
        derivative[7:10] = rates
        return derivative

    def thrust_and_rates(
        self, y: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], float, NDArray[np.float64]]:
        thrust = np.zeros(3, dtype=np.float64)
        magnitude = 0.0
        mdot_lh2 = 0.0
        mdot_lox = 0.0
        activity = self.activity
        if activity.hydrolox:
            added, flow = _hydrolox(self.craft, activity.throttle, activity.direction)
            thrust += added
            magnitude += float(np.linalg.norm(added))
            mdot_lh2 += flow / 9.0
            mdot_lox += flow * 8.0 / 9.0
        if activity.mhd:
            added, hydrogen, oxygen = _mhd(self.craft, activity, y[3:6])
            thrust += added
            magnitude += float(np.linalg.norm(added))
            mdot_lh2 += hydrogen
            mdot_lox += oxygen
        e_rate = 0.0
        if activity.electrolysis and float(y[7]) > 0.0:
            e_rate = self.craft.electrolysis.max_kg_per_s
            if float(y[8]) >= float(self.capacity[1]):
                e_rate = min(e_rate, mdot_lh2 * 9.0)
        rates = np.array(
            [
                -e_rate,
                e_rate / 9.0 - mdot_lh2,
                e_rate * 8.0 / 9.0 - mdot_lox,
            ],
            dtype=np.float64,
        )
        if float(y[9]) >= float(self.capacity[2]) and float(rates[2]) > 0.0:
            rates[2] = 0.0
        return thrust, magnitude, rates


class _Recorder:
    def __init__(self) -> None:
        self.t: list[float] = []
        self.y: list[NDArray[np.float64]] = []
        self.thrust: list[NDArray[np.float64]] = []
        self.phase: list[int] = []
        self.delta_v = 0.0

    def add(
        self,
        t: float,
        y: NDArray[np.float64],
        thrust: NDArray[np.float64],
        phase: int,
    ) -> None:
        y = y.copy()
        for index in (7, 8, 9):
            if float(y[index]) < _DUST_KG:
                y[index] = 0.0
        if self.t and abs(t - self.t[-1]) <= 1e-6:
            self.y[-1] = y.copy()
            self.thrust[-1] = thrust.copy()
            self.phase[-1] = phase
            return
        self.t.append(float(t))
        self.y.append(y.copy())
        self.thrust.append(thrust.copy())
        self.phase.append(phase)


def propagate(
    scenario: Scenario,
    craft: Craft,
    ephemeris: Ephemeris,
    *,
    cancel: Callable[[], bool] | None = None,
    on_update: Callable[[RunResult], None] | None = None,
    chunk_s: float | None = None,
) -> RunResult:
    """Integrate the four burns in order. A zero throttle is a coast."""
    _check_ready(scenario, craft)
    y = initial_state(scenario, craft, ephemeris)
    fixed = fixed_mass_kg(craft, scenario)
    capacity = np.array(
        [craft.tanks.water_kg, craft.tanks.lh2_kg, craft.tanks.lox_kg],
        dtype=np.float64,
    )
    recorder = _Recorder()
    cancelled = False
    t_cursor = 0.0
    for burn in scenario.burns:
        if cancel is not None and cancel():
            cancelled = True
            break
        t_end = t_cursor + burn.duration_s
        activity = _activity_for(burn, craft, scenario, y)
        dynamics = _Dynamics(craft, ephemeris, scenario.bodies, activity, fixed, capacity)
        while t_cursor < t_end - 1e-6:
            if cancel is not None and cancel():
                cancelled = True
                break
            step_end = t_end
            if chunk_s is not None:
                step_end = min(t_end, t_cursor + chunk_s)
            t_cursor, y = _integrate_span(
                dynamics, recorder, y, t_cursor, step_end, fixed, capacity
            )
            if on_update is not None:
                on_update(_result(scenario, recorder, cancelled))
        if cancelled:
            break
    if not recorder.t:
        thrust, _mag, _rates = _Dynamics(
            craft,
            ephemeris,
            scenario.bodies,
            _activity_for(scenario.burns[0], craft, scenario, y),
            fixed,
            capacity,
        ).thrust_and_rates(y)
        recorder.add(0.0, y, thrust, 0)
    return _result(scenario, recorder, cancelled)


def _integrate_span(
    dynamics: _Dynamics,
    recorder: _Recorder,
    y: NDArray[np.float64],
    t0: float,
    t1: float,
    fixed: float,
    capacity: NDArray[np.float64],
) -> tuple[float, NDArray[np.float64]]:
    guard = 0
    while t0 < t1 - 1e-6:
        guard += 1
        if guard > 12:
            raise RuntimeError("too many propulsion events in one segment")
        t0, y, finished = _one_solve(dynamics, recorder, y, t0, t1, fixed, capacity)
        if finished:
            break
    return t0, y


def _one_solve(
    dynamics: _Dynamics,
    recorder: _Recorder,
    y0: NDArray[np.float64],
    t0: float,
    t1: float,
    fixed: float,
    capacity: NDArray[np.float64],
) -> tuple[float, NDArray[np.float64], bool]:
    events, labels = _events(dynamics, y0, capacity)
    n_steps = max(1, int(np.ceil((t1 - t0) / MAX_STEP_S)))
    t_eval = np.linspace(t0, t1, n_steps + 1)
    solution = solve_ivp(
        dynamics.ode,
        (t0, t1),
        y0,
        method="DOP853",
        rtol=_RTOL,
        atol=_ATOL,
        max_step=MAX_STEP_S,
        t_eval=t_eval,
        events=events if events else None,
        dense_output=True,
    )
    if not solution.success and solution.status != 1:
        raise RuntimeError(solution.message)
    event_time, event_label = _earliest_event(solution, labels)
    limit = t1 if event_time is None else event_time
    _record_until(solution, recorder, dynamics, limit, event_time is not None)
    dense = solution.sol
    if dense is not None and limit > t0:
        recorder.delta_v += _thrust_delta_v(dense, dynamics, t0, limit)
    if event_time is None or event_label is None:
        y_end = np.asarray(solution.y[:, -1], dtype=np.float64)
        return t1, _reconcile(y_end, fixed, capacity), True
    y_event = _event_state(solution, event_label, labels)
    y_event = _apply_event(y_event, event_label, dynamics, capacity)
    y_event = _reconcile(y_event, fixed, capacity)
    thrust, _mag, _rates = dynamics.thrust_and_rates(y_event)
    recorder.add(
        event_time,
        y_event,
        thrust,
        dynamics.activity.phase if _mag > 0.0 else dynamics.activity.phase,
    )
    return event_time, y_event, False


def _record_until(
    solution: object,
    recorder: _Recorder,
    dynamics: _Dynamics,
    limit: float,
    stopped: bool,
) -> None:
    times = np.asarray(solution.t, dtype=np.float64)  # type: ignore[attr-defined]
    states = np.asarray(solution.y, dtype=np.float64)  # type: ignore[attr-defined]
    for index, time in enumerate(times):
        if stopped and float(time) >= limit - 1e-8:
            break
        if float(time) > limit + 1e-8:
            break
        state = states[:, index]
        thrust, _mag, _rates = dynamics.thrust_and_rates(state)
        recorder.add(float(time), state, thrust, dynamics.activity.phase)


def _thrust_delta_v(dense: object, dynamics: _Dynamics, t0: float, t1: float) -> float:
    count = max(2, int(np.ceil((t1 - t0) / 20.0)) + 1)
    times = np.linspace(t0, t1, count)
    samples = np.asarray(dense(times), dtype=np.float64)  # type: ignore[operator]
    accel = np.empty(count, dtype=np.float64)
    for index in range(count):
        _thrust, magnitude, _rates = dynamics.thrust_and_rates(samples[:, index])
        mass = float(samples[6, index])
        accel[index] = magnitude / mass if mass > 0.0 else 0.0
    return float(np.trapezoid(accel, times))


def _events(
    dynamics: _Dynamics,
    y0: NDArray[np.float64],
    capacity: NDArray[np.float64],
) -> tuple[list[_Terminal], list[str]]:
    _thrust, _mag, rates = dynamics.thrust_and_rates(y0)
    events: list[_Terminal] = []
    labels: list[str] = []
    if float(rates[0]) < 0.0 and float(y0[7]) > _DUST_KG:
        events.append(_zero_event(7))
        labels.append("water")
    if float(rates[1]) < 0.0 and float(y0[8]) > _DUST_KG:
        events.append(_zero_event(8))
        labels.append("lh2")
    if float(rates[2]) < 0.0 and float(y0[9]) > _DUST_KG:
        events.append(_zero_event(9))
        labels.append("lox")
    if float(rates[1]) > 0.0 and float(y0[8]) < float(capacity[1]):
        events.append(_cap_event(8, float(capacity[1])))
        labels.append("lh2_full")
    if float(rates[2]) > 0.0 and float(y0[9]) < float(capacity[2]):
        events.append(_cap_event(9, float(capacity[2])))
        labels.append("lox_full")
    return events, labels


class _Terminal:
    """SciPy event callable. `terminal` and `direction` are read by the solver."""

    def __init__(self, index: int, direction: int, *, level: float, full: bool) -> None:
        self.terminal = True
        self.direction = direction
        self._index = index
        self._level = level
        self._full = full

    def __call__(self, _t: float, y: NDArray[np.float64]) -> float:
        if self._full:
            return self._level - float(y[self._index])
        return float(y[self._index])


def _zero_event(index: int) -> _Terminal:
    return _Terminal(index, -1, level=0.0, full=False)


def _cap_event(index: int, level: float) -> _Terminal:
    return _Terminal(index, -1, level=level, full=True)


def _earliest_event(solution: object, labels: list[str]) -> tuple[float | None, str | None]:
    times = getattr(solution, "t_events", None)
    if not times:
        return None, None
    found: list[tuple[float, str]] = []
    for index, label in enumerate(labels):
        series = times[index]
        if len(series):
            found.append((float(series[0]), label))
    if not found:
        return None, None
    found.sort(key=lambda item: item[0])
    return found[0]


def _event_state(solution: object, label: str, labels: list[str]) -> NDArray[np.float64]:
    states = getattr(solution, "y_events", None)
    index = labels.index(label)
    if states is not None and len(states[index]):
        return np.asarray(states[index][0], dtype=np.float64)
    return np.asarray(solution.y[:, -1], dtype=np.float64)  # type: ignore[attr-defined]


def _apply_event(
    y: NDArray[np.float64],
    label: str,
    dynamics: _Dynamics,
    capacity: NDArray[np.float64],
) -> NDArray[np.float64]:
    activity = dynamics.activity
    if label == "water":
        y[7] = 0.0
        activity.electrolysis = False
    elif label == "lh2":
        y[8] = 0.0
        activity.hydrolox = False
        activity.mhd = False
        activity.oxygen = False
    elif label == "lox":
        y[9] = 0.0
        activity.hydrolox = False
        activity.oxygen = False
        if activity.mhd and _oxygen_required(dynamics.craft, activity):
            activity.mhd = False
    elif label == "lh2_full":
        y[8] = float(capacity[1])
    elif label == "lox_full":
        y[9] = float(capacity[2])
    return y


def _oxygen_required(craft: Craft, activity: _Activity) -> bool:
    oxygen = craft.engines.oxygen
    return activity.oxygen and oxygen is not None and oxygen.count > 0


def _reconcile(
    y: NDArray[np.float64],
    fixed: float,
    capacity: NDArray[np.float64],
) -> NDArray[np.float64]:
    state = y.copy()
    for index, cap in ((7, capacity[0]), (8, capacity[1]), (9, capacity[2])):
        value = float(state[index])
        if value < _DUST_KG:
            value = 0.0
        state[index] = min(max(value, 0.0), float(cap))
    state[6] = fixed + float(state[7]) + float(state[8]) + float(state[9])
    return state


def _activity_for(
    burn: Burn, craft: Craft, scenario: Scenario, y: NDArray[np.float64]
) -> _Activity:
    hydrolox = (
        burn.role in ("departure", "insertion")
        and burn.throttle > 0.0
        and _can_fire(craft.engines.hydrolox)
        and float(y[8]) > _DUST_KG
        and float(y[9]) > _DUST_KG
    )
    mhd = (
        burn.role in ("accelerate", "decelerate")
        and burn.throttle > 0.0
        and _can_fire(craft.engines.mhd)
        and float(y[8]) > _DUST_KG
    )
    oxygen = mhd and burn.oxygen == "auto" and craft.has_oxygen and float(y[9]) > _DUST_KG
    electrolysis = (
        scenario.electrolysis == "on"
        and craft.power.electric_w > 0.0
        and craft.electrolysis.max_kg_per_s > 0.0
        and float(y[7]) > _DUST_KG
    )
    follow = 0
    direction = _unit(y[3:6])
    if burn.role == "insertion":
        direction = -direction
    elif burn.role == "accelerate":
        follow = 1
    elif burn.role == "decelerate":
        follow = -1
    return _Activity(
        hydrolox=hydrolox,
        mhd=mhd,
        oxygen=oxygen,
        electrolysis=electrolysis,
        follow=follow,
        direction=direction,
        phase=_PHASE[burn.role],
        throttle=burn.throttle,
    )


def _hydrolox(
    craft: Craft,
    throttle: float,
    direction: NDArray[np.float64],
) -> tuple[NDArray[np.float64], float]:
    engine = craft.engines.hydrolox
    thrust = engine.count * engine.thrust_n * throttle
    flow = thrust / (engine.isp_s * G0_M_PER_S2)
    return direction * thrust, flow


def _mhd(
    craft: Craft,
    activity: _Activity,
    velocity: NDArray[np.float64],
) -> tuple[NDArray[np.float64], float, float]:
    engine = craft.engines.mhd
    thrust = engine.count * engine.thrust_n * activity.throttle
    hydrogen = thrust / (engine.isp_s * G0_M_PER_S2)
    oxygen_flow = 0.0
    oxygen_thrust = 0.0
    if activity.oxygen and craft.engines.oxygen is not None:
        oxygen = craft.engines.oxygen
        oxygen_thrust = oxygen.count * oxygen.thrust_n * activity.throttle
        oxygen_cap = oxygen_thrust / (oxygen.isp_s * G0_M_PER_S2)
        wanted = 8.0 * hydrogen
        if oxygen_cap <= 0.0:
            hydrogen = 0.0
            thrust = 0.0
            oxygen_thrust = 0.0
        elif wanted > oxygen_cap:
            scale = oxygen_cap / wanted
            hydrogen *= scale
            thrust *= scale
            oxygen_flow = oxygen_cap
        else:
            oxygen_flow = wanted
            oxygen_thrust = oxygen_flow * oxygen.isp_s * G0_M_PER_S2
    direction = activity.follow * _unit(velocity)
    return direction * (thrust + oxygen_thrust), hydrogen, oxygen_flow


def _can_fire(engine: Engine) -> bool:
    return engine.count > 0 and engine.thrust_n > 0.0 and engine.isp_s > 0.0


def _unit(vector: NDArray[np.float64]) -> NDArray[np.float64]:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        return np.array([1.0, 0.0, 0.0], dtype=np.float64)
    return vector / norm


def _gravity(
    t: float,
    position: NDArray[np.float64],
    bodies: tuple[str, ...],
    ephemeris: Ephemeris,
) -> NDArray[np.float64]:
    acceleration = np.zeros(3, dtype=np.float64)
    for body in bodies:
        body_r, _body_v = ephemeris.state(body, t)
        relative = position - body_r
        distance = float(np.linalg.norm(relative))
        if distance < 1.0:
            continue
        acceleration += -ephemeris.mu(body) * relative / distance**3
    return acceleration


def _check_ready(scenario: Scenario, craft: Craft) -> None:
    errors: list[str] = []
    propellant = scenario.initial.propellant
    if propellant.water_kg > craft.tanks.water_kg:
        errors.append("initial water exceeds tank capacity")
    if propellant.lh2_kg > craft.tanks.lh2_kg:
        errors.append("initial LH2 exceeds tank capacity")
    if propellant.lox_kg > craft.tanks.lox_kg:
        errors.append("initial LOX exceeds tank capacity")
    if errors:
        raise DocumentError(errors)


def _result(scenario: Scenario, recorder: _Recorder, cancelled: bool) -> RunResult:
    states = np.vstack(recorder.y)
    thrust = np.vstack(recorder.thrust)
    position = states[:, 0:3]
    velocity = states[:, 3:6]
    epoch = scenario.epoch.strftime("%Y-%m-%dT%H:%M:%SZ")
    return RunResult(
        name=scenario.name,
        epoch=epoch,
        t=np.asarray(recorder.t, dtype=np.float64),
        position_m=position,
        velocity_m_per_s=velocity,
        mass_kg=states[:, 6],
        water_kg=states[:, 7],
        lh2_kg=states[:, 8],
        lox_kg=states[:, 9],
        radius_m=np.linalg.norm(position, axis=1),
        speed_m_per_s=np.linalg.norm(velocity, axis=1),
        thrust_n=np.linalg.norm(thrust, axis=1),
        thrust_m=thrust,
        phase=np.asarray(recorder.phase, dtype=np.int64),
        delta_v_m_per_s=recorder.delta_v,
        cancelled=cancelled,
    )
