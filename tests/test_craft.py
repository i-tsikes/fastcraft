"""Craft loader checks, including the waste-heat limit."""

from pathlib import Path

import pytest

from fastcraft.craft import load_craft, parse_craft
from fastcraft.patch import apply_patch, extract_patch
from fastcraft.yamlio import DocumentError, dump_yaml, load_yaml

_VALID = """\
schema: fastcraft.craft/v1
name: Hauler
# Dry mass excludes propellant and the docked Starship.
dry_mass_kg: 1000.0
tanks:
  water_kg: 10.0
  lh2_kg: 20.0
  lox_kg: 160.0
starship:
  dry_mass_kg: 50.0
  landing_propellant_kg: 25.0
power:
  reactor_thermal_w: 1000.0
  electric_w: 100.0
  radiator_reject_w: 900.0
electrolysis:
  max_kg_per_s: 0.1
engines:
  hydrolox:
    count: 4
    thrust_n: 1000.0
    isp_s: 300.0
  mhd:
    count: 9
    thrust_n: 10.0
    isp_s: 2000.0
geometry:
  tank_length_m: 10.0
  tank_radius_m: 2.0
  radiator_span_m: 8.0
notes: ""
"""


def test_load_craft_and_round_trip_comments(tmp_path: Path) -> None:
    path = tmp_path / "hauler.craft.yaml"
    path.write_text(_VALID, encoding="utf-8")
    craft = load_craft(path)
    assert craft.name == "Hauler"
    assert craft.engines.oxygen is None
    assert craft.power.waste_heat_w == pytest.approx(900.0)
    data = load_yaml(path)
    data["dry_mass_kg"] = 1234.0
    dumped = tmp_path / "out.craft.yaml"
    dump_yaml(data, dumped)
    text = dumped.read_text(encoding="utf-8")
    assert "Dry mass excludes propellant" in text
    assert "1234" in text
    assert load_craft(dumped).dry_mass_kg == pytest.approx(1234.0)


def test_rejects_unknown_field(tmp_path: Path) -> None:
    path = tmp_path / "hauler.craft.yaml"
    path.write_text(_VALID.replace("notes:", "extra: 1\nnotes:"), encoding="utf-8")
    with pytest.raises(DocumentError, match="unknown field"):
        load_craft(path)


def test_rejects_waste_heat_above_radiator() -> None:
    data = load_yaml_text(_VALID.replace("radiator_reject_w: 900.0", "radiator_reject_w: 50.0"))
    with pytest.raises(DocumentError, match="radiator_reject_w"):
        parse_craft(data)


def test_rejects_electric_above_thermal() -> None:
    text = _VALID.replace("electric_w: 100.0", "electric_w: 1000.0")
    text = text.replace("radiator_reject_w: 900.0", "radiator_reject_w: 0.0")
    with pytest.raises(DocumentError, match="electric_w"):
        parse_craft(load_yaml_text(text))


def test_json_patch_replaces_a_field() -> None:
    data = load_yaml_text(_VALID)
    patched = apply_patch(data, [{"op": "replace", "path": "/dry_mass_kg", "value": 42.0}])
    assert patched["dry_mass_kg"] == 42.0
    assert data["dry_mass_kg"] == 1000.0
    reply = (
        "Here is a change:\n```json\n"
        '{"target": "craft", "patch": [{"op": "replace", "path": "/name", "value": "Tug"}]}\n'
        "```\n"
    )
    extracted = extract_patch(reply)
    assert extracted is not None
    target, ops = extracted
    assert target == "craft"
    assert apply_patch(data, ops)["name"] == "Tug"


def load_yaml_text(text: str):
    from fastcraft.yamlio import loads_yaml

    return loads_yaml(text)
