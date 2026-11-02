"""Playback cache. One `.npz` per run, beside the scenario, not inside it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from fastcraft.units import format_elapsed

PLOT_COLUMNS: tuple[str, ...] = (
    "time",
    "radius",
    "speed",
    "mass",
    "water",
    "lh2",
    "lox",
    "thrust",
)

_FILE_COLUMNS: tuple[str, ...] = (
    "t",
    "x",
    "y",
    "z",
    "vx",
    "vy",
    "vz",
    "mass_kg",
    "water_kg",
    "lh2_kg",
    "lox_kg",
    "radius_m",
    "speed_m_per_s",
    "thrust_n",
    "thrust_x",
    "thrust_y",
    "thrust_z",
    "phase",
)


@dataclass
class RunResult:
    """Time series for plots, playback, and the mission view."""

    name: str
    epoch: str
    t: NDArray[np.float64]
    position_m: NDArray[np.float64]
    velocity_m_per_s: NDArray[np.float64]
    mass_kg: NDArray[np.float64]
    water_kg: NDArray[np.float64]
    lh2_kg: NDArray[np.float64]
    lox_kg: NDArray[np.float64]
    radius_m: NDArray[np.float64]
    speed_m_per_s: NDArray[np.float64]
    thrust_n: NDArray[np.float64]
    thrust_m: NDArray[np.float64]
    phase: NDArray[np.int64]
    delta_v_m_per_s: float = 0.0
    cancelled: bool = False

    def column(self, name: str) -> NDArray[np.float64]:
        table = {
            "time": self.t,
            "radius": self.radius_m,
            "speed": self.speed_m_per_s,
            "mass": self.mass_kg,
            "water": self.water_kg,
            "lh2": self.lh2_kg,
            "lox": self.lox_kg,
            "thrust": self.thrust_n,
        }
        try:
            return table[name]
        except KeyError as exc:
            raise KeyError(f"unknown column {name!r}") from exc

    def summary(self) -> str:
        if self.t.size == 0:
            return "no samples"
        used = float(self.mass_kg[0] - self.mass_kg[-1])
        elapsed = format_elapsed(float(self.t[-1] - self.t[0]))
        return (
            f"duration: {elapsed} ({float(self.t[-1] - self.t[0]):.3f} s)\n"
            f"final_mass_kg: {float(self.mass_kg[-1]):.3f}\n"
            f"final_radius_m: {float(self.radius_m[-1]):.3f}\n"
            f"propellant_used_kg: {used:.3f}\n"
            f"delta_v_m_per_s: {self.delta_v_m_per_s:.3f}\n"
        )

    def save(self, path: Path) -> None:
        meta = {
            "scenario": self.name,
            "epoch": self.epoch,
            "columns": list(_FILE_COLUMNS),
            "delta_v_m_per_s": self.delta_v_m_per_s,
            "cancelled": self.cancelled,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            meta=np.asarray(json.dumps(meta)),
            t=self.t,
            x=self.position_m[:, 0],
            y=self.position_m[:, 1],
            z=self.position_m[:, 2],
            vx=self.velocity_m_per_s[:, 0],
            vy=self.velocity_m_per_s[:, 1],
            vz=self.velocity_m_per_s[:, 2],
            mass_kg=self.mass_kg,
            water_kg=self.water_kg,
            lh2_kg=self.lh2_kg,
            lox_kg=self.lox_kg,
            radius_m=self.radius_m,
            speed_m_per_s=self.speed_m_per_s,
            thrust_n=self.thrust_n,
            thrust_x=self.thrust_m[:, 0],
            thrust_y=self.thrust_m[:, 1],
            thrust_z=self.thrust_m[:, 2],
            phase=self.phase,
        )

    @classmethod
    def load(cls, path: Path) -> RunResult:
        with np.load(path, allow_pickle=False) as archive:
            meta_raw = archive["meta"]
            meta = json.loads(str(meta_raw.item()))
            position = np.column_stack([archive["x"], archive["y"], archive["z"]])
            velocity = np.column_stack([archive["vx"], archive["vy"], archive["vz"]])
            thrust = np.column_stack(
                [archive["thrust_x"], archive["thrust_y"], archive["thrust_z"]]
            )
            return cls(
                name=str(meta["scenario"]),
                epoch=str(meta["epoch"]),
                t=np.asarray(archive["t"], dtype=np.float64),
                position_m=np.asarray(position, dtype=np.float64),
                velocity_m_per_s=np.asarray(velocity, dtype=np.float64),
                mass_kg=np.asarray(archive["mass_kg"], dtype=np.float64),
                water_kg=np.asarray(archive["water_kg"], dtype=np.float64),
                lh2_kg=np.asarray(archive["lh2_kg"], dtype=np.float64),
                lox_kg=np.asarray(archive["lox_kg"], dtype=np.float64),
                radius_m=np.asarray(archive["radius_m"], dtype=np.float64),
                speed_m_per_s=np.asarray(archive["speed_m_per_s"], dtype=np.float64),
                thrust_n=np.asarray(archive["thrust_n"], dtype=np.float64),
                thrust_m=np.asarray(thrust, dtype=np.float64),
                phase=np.asarray(archive["phase"], dtype=np.int64),
                delta_v_m_per_s=float(meta.get("delta_v_m_per_s", 0.0)),
                cancelled=bool(meta.get("cancelled", False)),
            )


def playback_path(scenario: Path, when: datetime | None = None) -> Path:
    """`{stem}-{YYYY-MM-DD}-{HHMMSS}.npz` using local 24-hour wall time."""
    stamp = when if when is not None else datetime.now()
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone()
    name = scenario.name
    stem = name.removesuffix(".scenario.yaml")
    if stem == name:
        stem = scenario.stem
    return scenario.parent / f"{stem}-{stamp:%Y-%m-%d-%H%M%S}.npz"
