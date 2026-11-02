# Coding Agent Guidelines & Project Context

You are an expert Python Software Engineering Agent. Your role is to implement features, fix bugs, and refactor code adhering strictly to the constraints, stack rules, and quality gates defined below.

## 1. Tech Stack & Environment
- **Runtime:** Python 3.14. Python 3.15 is not used yet because VTK has no 3.15 wheels.
- **Package & Environment Manager:** `uv` (Never use raw `pip`, `poetry`, or `venv` directly)
- **Code Style & Linting:** `ruff`
- **Type Checking:** `mypy` in strict mode for the pure-Python core only. Strict mode does not apply to `fastcraft/gui`. PySide6 and PyVista stubs are weak.
- **Testing:** `pytest`
- **Execution model:** The ODE is CPU-bound and the GUI is Qt. Integration runs on a Qt worker thread. `asyncio` is not the default. Do not prefer `asyncio`.

## 2. Mandatory Verification Commands
Before declaring a task complete, you MUST execute these commands in order and ensure they pass with zero errors. Aim for zero warnings. Unavoidable warnings from dependencies (SciPy, Qt, and similar) are allowed. Do not treat those warnings as a failed task.

- **Format check:** `uv run ruff format --check .`
- **Lint check:** `uv run ruff check .` (this is the lint check, including import sorting)
- **Type check:** `uv run mypy fastcraft --exclude fastcraft/gui` (strict `mypy` on the pure-Python core only; this is the type check)
- **Test suite:** `uv run pytest`

## 3. Python Coding Conventions
- **Type Hints:** In the pure-Python core, all function signatures, variable declarations, and return types must be fully and explicitly typed. No implicit `Any`. Strict `mypy` does not apply to `fastcraft/gui`.
- **Modern Standards:** Use `pathlib.Path` for all file and directory operations. Do not use legacy `os.path`.
- **Validation:** Use `Pydantic` v2 for data parsing, schemas, and configuration models. Craft and scenario YAML files are an across-the-board exception to Pydantic. Do not use Pydantic to parse, validate, or write those YAML documents. Use `ruamel.yaml` so comments round-trip.
- **Project Structure:** Maintain a flat, modular directory structure. Do not deeply nest packages unless explicitly instructed.
- **Concurrency:** The ODE is CPU-bound and the GUI is Qt. Run integration on a Qt worker thread. `asyncio` is not the default.

## 4. Operational Boundaries

### ALWAYS DO
- Base all work on the "dev" branch unless instructed otherwise, and submit pull requests against that branch.
- Write tests in the `tests/` directory for new core logic and for bug fixes. Do not require a unit test for every GUI glue function.
- Run the full test suite before proposing a code modification or pull request. Aim for zero warnings. Unavoidable warnings from dependencies (SciPy, Qt, and similar) are allowed and are not a failed task.
- Ensure all tool calls are idempotent.
- Check `.gitignore` rules before creating any new files or directories.

### ASK FIRST
- Before introducing a new third-party dependency (always ask before running `uv add`).
- Before changing the craft or scenario YAML schema. Craft and scenario YAML files are an across-the-board exception to Pydantic: do not parse, validate, or write them with Pydantic; use `ruamel.yaml` so comments round-trip.
- Before refactoring code outside the scope of your current assigned issue.

### NEVER DO
- NEVER use hardcoded secrets, API keys, or environment-specific credentials. Load them from the environment or from gitignored settings.
- NEVER commit changes if `ruff`, core `mypy`, or `pytest` checks are failing. Unavoidable warnings from dependencies (SciPy, Qt, and similar) are allowed and are not a failed task.
- NEVER use Pydantic to parse, validate, or write craft or scenario YAML documents. Those files are an across-the-board exception to Pydantic. Use `ruamel.yaml` so comments round-trip.
- NEVER skip tests for new core logic or bug fixes. Do not require a unit test for every GUI glue function.
- NEVER drop or delete failing tests—fix the underlying regression or explicitly update the test if requirements changed.
