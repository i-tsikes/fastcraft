# fastcraft

Simulator for fast spacecraft

## What it does

fastcraft is a desktop simulator for a water-fueled nuclear Hauler.
A craft file describes the dry mass, tanks, docked Starship, reactor and
radiator limits, electrolysis rate, and engines. A scenario file describes
the epoch, the starting orbit, four burns, and how the Ship is carried.

Initiate integrates those burns with a Cowell propagator and opens a mission
view: a top-down heliocentric map, plots, and a schematic 3D Hauler. Playback
reads a saved `.npz` file and does not integrate again. Presets fill burn
duration and throttle. An assistant panel can propose YAML edits as a JSON
Patch. The app writes a craft or scenario file only when you save.

## Install

Python 3.14 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync
```

## Run

Open the desktop app:

```bash
uv run fastcraft
```

Propagate a scenario from the command line and write a playback file next to it:

```bash
uv run fastcraft examples/earth-mars.scenario.yaml
```

A local JPL Development Ephemeris kernel is optional. Set `FASTCRAFT_KERNEL`
or choose the kernel in Settings. With no kernel, fastcraft uses an analytic
Sun, Earth, and Mars and does not download one.

The assistant calls an OpenAI-compatible chat endpoint at OpenRouter. Set
`OPENROUTER_API_KEY`, or save a key in Settings. The key lives in
`~/.config/fastcraft/settings.yaml`, never in the repository and never in a
craft or scenario file.

## Test

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy fastcraft --exclude fastcraft/gui
uv run pytest
```

Tests do not open a window and do not download a JPL kernel.

## Initial Goals


- Simulate a water fueled nuclear spacecraft (Hauler)
  - ~1 GW advanced (90% enriched) nuclear reactor in aft position
  - Brayton cycle electric generation
  - Large "wing" radiators for cooling
  - Robust PMD setup
  - Large water tank
	- Useful for thermal management
	- Warmed by nuclear waste heat for deice
  - Heavy duty electrolysis
	- Cryogenic hydrogen and oxygen storage forward of water tank
	- Active cooling
  - Hydrolox chemical propulsion (four thrusters arranged outside nuclear core)
  - MHD hydrogen electric propulsion
    - Evaluate future tech
	- Thrusters in the tens to hundreds of Newtons range
	- Six thrusters arranged in hexagon, with three more vectored in center
  - Initial design is a "Starship hauler"
	- Starship (Ship stage) docks at front of assembly
	- Ship is crew quarters
		- One crazy idea was to have a water-filled 'sheath' surrounding the crew quarters for radiation shielding
	- Transit is made with Starship landing fuel only
	
- Explore engineering trades based on reactor size, water tank size, etc.

- Simulate flights assuming Hauler is constructed and initially water-fueled in orbit.
  - Mars flight initial focus
  - Modified Hohmann trajectories?
	- What are requirements for much shorter flight times?


