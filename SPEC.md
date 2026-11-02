# fastcraft software specification

Status: draft for review. Not in the repository until accepted.

Functional source: the README on `main` (commit `5d86cafe1f`). This document locks the software architecture. The vehicle mission stays as stated there: a water-fueled nuclear Hauler, Starship as the docked crew stage, Mars first, trades on reactor and tank size, and flights shorter than a Hohmann transfer.

v1 does not simulate a coupled plant. The reactor, Brayton cycle, radiators, and electrolysis are scalar limits on power, heat rejection, and propellant production.

## 1. Package layout

One desktop process. The core imports neither Qt nor PyVista.

```text
fastcraft/
  __init__.py
  units.py
  craft.py            # load and validate *.craft.yaml
  scenario.py         # load and validate *.scenario.yaml
  ephemeris.py        # body states from a JPL kernel
  propagate.py        # Cowell ODE and segment timeline
  results.py          # time series cache
  gui/
    __init__.py
    app.py            # PySide6 main window
    plots.py          # pyqtgraph
    view3d.py         # PyVista
    assistant.py      # OpenRouter side panel
tests/
  test_craft.py
  test_scenario.py
  test_coast.py
  test_burn.py
```

A CLI entry point calls `propagate` on a scenario path and writes the result cache. Tests use that path and do not start Qt.

Dependencies: Python 3.14, NumPy, SciPy, Astropy or Skyfield (one, chosen at implementation; the ephemeris module hides which), ruamel.yaml, PySide6, pyqtgraph, PyVista, and an OpenAI-compatible HTTP client for OpenRouter. 3.14 is the newest release with VTK wheels, which PyVista needs. 3.15 is not supported until those wheels exist. JPL DE kernel files are downloaded by the user and referenced by path. They are not committed.

## 2. Units and frames

SI throughout, stored in the files with no unit suffix. Time is UTC as an ISO-8601 string. Integration uses seconds past the scenario epoch. Angles in files are degrees. The integrator uses radians.

| Quantity | Unit |
| --- | --- |
| Mass | kg |
| Length | m |
| Time | s |
| Thrust | N |
| Specific impulse | s |
| Power | W |
| Epoch | UTC |

The integration frame is heliocentric ecliptic J2000, Cartesian meters and meters per second. Body and spacecraft states are converted into that frame at each force evaluation. A scenario may also store an initial state relative to a departure body; the loader converts it once, at epoch, into the heliocentric frame.

## 3. Documents

ruamel.yaml reads and writes every document. Key order is stable. Comments round-trip. Each file has `schema`. Unknown fields are rejected. The GUI saves only when the user asks.

Run results are not inside the scenario. They live in a cache directory next to it (`<scenario-stem>.run/`), as a NumPy `.npz` plus a small JSON index of column names. Deleting that directory is safe. Analysis reads the cache.

The assistant reads the YAML text. When it proposes an edit, it returns a JSON Patch (RFC 6902) against the parsed document. The app applies the patch in memory and writes YAML only after the user saves.

### 3.1 `*.craft.yaml`

`schema` is `fastcraft.craft/v1`.

```yaml
schema: fastcraft.craft/v1
name: Hauler
# Dry mass excludes propellant and the docked Starship.
dry_mass_kg: 0.0
tanks:
  water_kg: 0.0          # capacity
  lh2_kg: 0.0
  lox_kg: 0.0
starship:
  dry_mass_kg: 0.0
  landing_propellant_kg: 0.0   # budget, not burned by Hauler engines
power:
  reactor_thermal_w: 1.0e9
  electric_w: 0.0              # Brayton output limit
  radiator_reject_w: 0.0
electrolysis:
  max_kg_per_s: 0.0            # water converted to hydrolox
  # Stoichiometric split. Mass of water in equals LH2 plus LOX out.
engines:
  hydrolox:
    count: 4
    thrust_n: 0.0              # each, vacuum
    isp_s: 0.0
  mhd:
    count: 9                   # six around, three center, schematic only
    thrust_n: 0.0              # each, hydrogen
    isp_s: 0.0
  oxygen:                      # optional; omit the block to leave them off
    count: 0
    thrust_n: 0.0              # each
    isp_s: 0.0
geometry:                      # schematic, meters, not CAD
  tank_length_m: 0.0
  tank_radius_m: 0.0
  radiator_span_m: 0.0
notes: ""
```

Loaded propellant at the start of a flight lives on the scenario, and it cannot exceed these capacities. `landing_propellant_kg` is a reserved mass. v1 does not burn it and does not let electrolysis or Hauler engines draw from it.

`dry_mass_kg` is one number. v1 does not split it across reactor, tanks, radiators, or engines. A later enhancement can assign mass to those parts so trades show where the dry mass went.

Oxygen electric thrusters are optional. Omit `engines.oxygen`, or set `count` to 0, and the craft has none. When present, their parameters match the hydrogen MHD set: count, thrust per engine, and specific impulse. They consume LOX only. Whenever hydrogen MHD is firing, they fire at the same time at 8 kg oxygen per 1 kg hydrogen, so the tanks stay on the hydrolox mixture and net acceleration rises. Throttle drops if LOX or thrust authority runs out. Venting remains only if the LOX tank is full and the oxygen thrusters cannot take the excess. Mass flow is `thrust / (Isp * g0)`.

Electrolysis availability: while `electric_w` is positive and water remains, the scenario may turn electrolysis on. The rate is at most `max_kg_per_s`. Products split by mass fraction 1/9 hydrogen and 8/9 oxygen. Power and radiator limits are recorded and checked (`electric_w` must be below `reactor_thermal_w`, and waste heat `reactor_thermal_w - electric_w` must be within `radiator_reject_w`). v1 does not integrate a thermal network.

### 3.2 `*.scenario.yaml`

`schema` is `fastcraft.scenario/v1`. `craft` is a path relative to the scenario file.

```yaml
schema: fastcraft.scenario/v1
name: Earth to Mars
craft: ../craft/hauler.craft.yaml
epoch: "2035-04-01T00:00:00Z"
frame: heliocentric_ecliptic_j2000
bodies: [sun, earth, mars]
initial:
  # GUI default. Velocity is computed, not typed.
  mode: earth_circular_equatorial
  agl_m: 400000.0
  longitude_deg: 0.0          # east, Earth-fixed at epoch; optional
  propellant:
    water_kg: 0.0             # full tanks at outbound departure
    lh2_kg: 0.0
    lox_kg: 0.0
arrival:
  body: mars
  agl_m: 400000.0             # nearly circular orbit; Ship release altitude
ship: docked                  # outbound. Return: none, empty, or loaded
cargo_kg: 0.0                 # only when ship is loaded
burns:
  - role: departure           # hydrolox
    duration_s: 0.0
    throttle: 1.0             # 0 to 1, scales thrust and mass flow
  - role: accelerate          # hydrogen MHD, plus optional oxygen
    duration_s: 0.0
    throttle: 1.0
    oxygen: auto              # auto or off
  - role: decelerate
    duration_s: 0.0
    throttle: 1.0
    oxygen: auto
  - role: insertion           # hydrolox
    duration_s: 0.0
    throttle: 1.0
electrolysis: on              # on or off for the whole scenario in v1
```

Outbound flights start in Earth orbit with the Ship stage already docked and tanks full. A return is a separate scenario and may set `ship` to `none`, `empty`, or `loaded`. `loaded` adds `cargo_kg` to Starship dry mass. `empty` is Starship dry mass and no cargo. `none` omits Ship mass. There is no rendezvous. Landing propellant stays reserved when a Ship is attached.

The GUI default initial state is `earth_circular_equatorial`. The user sets altitude above the ground. Radius is the WGS84 equatorial radius (6378137 m) plus `agl_m`. Speed relative to Earth is `sqrt(mu_earth / radius)`, due east in the equatorial plane. The heliocentric state is Earth’s inertial position and velocity at `epoch` plus that relative state. `longitude_deg` defaults to 0 east. The loader rejects any `agl_m` below 100000 m (100 km).

A free-form initial state remains for tests and is not the GUI default. It uses `mode: heliocentric` with `position_m` and `velocity_m_per_s` in the integration frame. The GUI does not offer it on a new scenario.

The four burns run in order, with a coast in any gap the durations leave. Throttle scales thrust and mass flow from 0 to 1. Hydrolox departure and insertion thrust is along the velocity or anti-velocity direction, frozen at the start of that burn. MHD acceleration thrusts along inertial velocity. MHD deceleration thrusts against it. Thrust drops to zero early if the required propellant is exhausted. Hydrolox consumes LH2 and LOX at a 1:8 mass ratio and shuts down when either tank hits zero. Hydrogen MHD consumes LH2 only. Optional oxygen thrusters consume LOX only. Whenever hydrogen MHD is firing, they fire at the same time at 8 kg oxygen per 1 kg hydrogen, so the tanks stay on the hydrolox mixture and net acceleration rises. Their throttle drops if LOX or thrust authority runs out. Mass flow is `thrust / (Isp * g0)` with `g0 = 9.80665` m/s^2, summed over engines that are firing.

Mars is never moved. Its state comes from the kernel. The user sets `arrival.agl_m`. The GUI offers a departure-date pick list before any full-calendar search with the integrator. Over one Earth-Mars synodic period, step the date by a few days. Flight time is the sum of the four burn durations. Keep the dates where Mars (or Earth, on a return) is near the arrival direction, and show the best handful with the date and the estimated flight time. After the user picks one, a one-dimensional search shifts `epoch` by days, runs the saved burns, and scores miss distance plus remaining hydrolox needed to circularize at `arrival.agl_m`. The best epoch is written back only when the user accepts it. The search does not retune durations, throttles, or burn order.

### 3.3 Trip presets

The burn duration and throttle fields are what the integrator runs. Presets only fill them. The user can edit any of them afterward.

- **Fastest.** Search those durations and throttles, tanks full, Ship docked, and keep the set with the shortest arrival. Hydrolox is departure and insertion. Hydrogen MHD is acceleration and deceleration. If the craft has oxygen thrusters, the search may set `oxygen: auto` on the MHD burns. Write the winning values into the fields.
- **Return, no Ship.** Same search toward Earth, `ship: none`, leaving at least the propellant that mirrored four-burn return needs.
- **Return, empty Ship.** Same, with empty-Ship dry mass in that reserve.
- **Return, loaded Ship.** Same, plus the `cargo_kg` the user entered.

Fastest may spend everything except the return reserve when a return preset is selected. A return preset will not fill in a plan below that reserve. The epoch pick list still runs after the fields are set.

## 4. Ephemeris and forces

v1 bodies are Sun, Earth, and Mars. `ephemeris.py` exposes `state(body, t) -> (r_m, v_m_per_s)` in the integration frame and `mu(body) -> m^3/s^2`. Adding a body means a kernel lookup plus a `mu`. Callers do not know whether the backend is Astropy or Skyfield.

Gravity is a point mass at each body in `bodies`. No atmosphere, no oblateness, no solar radiation pressure, no relativistic terms.

## 5. Propagation

State vector `y`, length 8:

| Index | Component |
| --- | --- |
| 0:3 | heliocentric position, m |
| 3:6 | heliocentric velocity, m/s |
| 6 | spacecraft mass, kg, including dry mass, Starship, and all propellant |
| 7 | water mass, kg |

LH2 and LOX are extra state, so the implemented vector is length 10: position (3), velocity (3), total mass, water, LH2, LOX. Total mass stays consistent with dry mass plus Starship plus the three tanks plus landing propellant. Landing propellant is constant.

`dy/dt = f(t, y)`:

- `dr/dt = v`
- `dv/dt = sum_i -mu_i * r_rel / |r_rel|^3 + thrust_vector / mass`
- tank derivatives from the active segment and electrolysis, as in section 3.2
- `d(mass)/dt` equals the sum of tank rates (negative while burning, zero while only moving mass from water into LH2 and LOX)

Integrator: `scipy.integrate.solve_ivp`, method `DOP853`, with a max step small enough to sample plots (default 3600 s, overridable on the scenario later; v1 uses the default). Events stop a burn when a required tank reaches zero. The result series stores `t` and `y` at the solver's output points, plus derived columns: heliocentric radius, speed, thrust magnitude, and each tank mass.

Propellant is clamped at zero. A step that would drive a tank negative is rejected by the event and restarted with that engine off.

## 6. Acceptance checks

Tolerances are for v1 regression tests, not navigation.

**Coast.** A spacecraft with thrust and electrolysis off, initial state taken from a circular heliocentric orbit at 1 AU under solar gravity only (`bodies: [sun]`), propagated for 365.25 days. Specific mechanical energy and specific angular momentum each stay within 1e-6 relative of their initial values. The final radius stays within 1e-4 relative of 1 AU.

**Burn.** The same orbit, one in-track hydrolox burn with analytic constant thrust and Isp, no third body. Compare the integrated delta-v to `Isp * g0 * ln(m0 / m1)` within 1e-3 relative, and the propellant consumed to `(m0 - m1)` within 1e-6 relative. A second case runs the tank to empty and checks that thrust is zero afterward and the tank is exactly zero.

**Loader.** A craft that asks electrolysis to reject more waste heat than `radiator_reject_w`, or a scenario whose initial propellant exceeds tank capacity, fails validation before integration.

Patched-conic comparison against a hand-computed Earth-Mars Hohmann is a later check. v1 records the Hohmann numbers the user stores beside a run. It does not have to match them inside a tolerance yet.

## 7. GUI

PySide6 main window:

- File menu: new, open, save, and save-as for the active craft or scenario.
- Editors for the fields in section 3. Invalid documents show the validator message and cannot run.
- New outbound scenarios open on a circular equatorial Earth orbit. One altitude-above-ground field sets it. The window shows the computed speed and does not ask for a velocity. Starship starts docked.
- Four burn rows, each with duration and throttle. Preset buttons: Fastest, Return with no Ship, Return with an empty Ship, and Return with a loaded Ship. The loaded preset asks for cargo mass. Choosing a preset fills the duration and throttle fields. The user can edit those fields after. A departure-date pick list sits beside them. Accepting a refined epoch writes `epoch` and nothing else.
- Run action, with a busy indicator. Integration runs off the UI thread. Cancel sets a flag the integrator checks between segments.
- Recent files list.

2D: pyqtgraph. The user picks X and Y from the result columns (time, radius, speed, mass, each tank, thrust). A second cached run can be overlaid. The cursor time is shared with the 3D view.

3D: PyVista in the same heliocentric frame. Body paths over the scenario window are polylines. The spacecraft path is a polyline. A marker sits at the cursor time. The vehicle glyph is schematic: aft core, central tank cylinder from `geometry`, two radiator wings of span `radiator_span_m`, four hydrolox bells outside the core, six MHD bells on a hexagon plus three at the center, and a Starship cone docked on the forward face. No mesh import.

## 8. Assistant

A dock panel. The user types a question. The request to OpenRouter is chat completions on `https://openrouter.ai/api/v1`, model chosen in settings (a default is set at implementation). The system prompt says the assistant is an engineering aide, that it must not invent file fields outside section 3, and that edits are a JSON Patch in a fenced block. The user message includes the open craft YAML, the open scenario YAML, and a short result summary if a cache exists (duration, final mass, final radius, propellant used). It does not include the full trajectory.

The API key is `OPENROUTER_API_KEY` or a gitignored local settings file. It is never written into the repo, a craft file, or a scenario file. The panel has no tool that calls `propagate`. Applying a patch updates the in-memory document only.

## 9. Non-goals

- Coupled reactor, Brayton, radiator, or electrolysis thermodynamics.
- Atmosphere, entry, landing, oblateness, radiation pressure, or relativity.
- Science-grade ephemerides or flight-software guidance.
- The assistant commanding the integrator or saving files on its own.
- Docking or a rendezvous. A return may omit the Ship. It cannot meet one in flight.
- Splitting dry mass by component. v1 keeps a single dry mass.
- CAD, structural analysis, and radiation-dose transport. A water sheath around the crew cabin is a later mass and geometry note, not a v1 model.
