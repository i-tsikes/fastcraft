"""The shipped examples are documents the loader accepts."""

from pathlib import Path

from fastcraft.craft import load_craft
from fastcraft.scenario import load_scenario

_ROOT = Path(__file__).resolve().parents[1]


def test_example_documents_load() -> None:
    craft = load_craft(_ROOT / "examples" / "hauler.craft.yaml")
    scenario = load_scenario(_ROOT / "examples" / "earth-mars.scenario.yaml")
    assert craft.name == "Hauler"
    assert scenario.craft_path == craft.source_path
    assert scenario.ship == "docked"
    assert [burn.role for burn in scenario.burns] == [
        "departure",
        "accelerate",
        "decelerate",
        "insertion",
    ]
