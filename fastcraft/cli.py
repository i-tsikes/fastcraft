"""Command line. `fastcraft scenario.yaml` propagates and writes a playback file.

With no scenario path, the desktop window opens. Tests call this with a path
and do not start Qt.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastcraft.craft import load_craft
from fastcraft.ephemeris import open_ephemeris
from fastcraft.propagate import propagate
from fastcraft.results import playback_path
from fastcraft.scenario import load_scenario
from fastcraft.yamlio import DocumentError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fastcraft",
        description="Propagate a fastcraft scenario, or open the desktop app.",
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        type=Path,
        help="Path to a .scenario.yaml file. Omit to open the GUI.",
    )
    parser.add_argument("--kernel", type=Path, default=None, help="Local JPL DE kernel (.bsp).")
    parser.add_argument("--output", type=Path, default=None, help="Playback .npz path.")
    parser.add_argument("--gui", action="store_true", help="Open the desktop app.")
    args = parser.parse_args(argv)
    if args.gui or args.scenario is None:
        from fastcraft.gui.app import launch

        launch(args.scenario)
        return 0
    try:
        return run_scenario(args.scenario, kernel=args.kernel, output=args.output)
    except (DocumentError, FileNotFoundError, KeyError, RuntimeError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1


def run_scenario(path: Path, *, kernel: Path | None = None, output: Path | None = None) -> int:
    """Propagate `path` and write one playback file. Returns a process status."""
    scenario = load_scenario(path)
    if scenario.craft_path is None or not scenario.craft_path.is_file():
        raise DocumentError([f"craft file not found for {path}"])
    craft = load_craft(scenario.craft_path)
    ephemeris = open_ephemeris(scenario.epoch, kernel)
    result = propagate(scenario, craft, ephemeris)
    destination = output if output is not None else playback_path(path)
    result.save(destination)
    print(destination)
    return 0
