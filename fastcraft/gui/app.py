"""Desktop window: craft and scenario editors, Initiate, and the assistant."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fastcraft.craft import Craft, parse_craft
from fastcraft.ephemeris import open_ephemeris
from fastcraft.gui.assistant import AssistantDock
from fastcraft.gui.forms import CraftForm, ScenarioForm, new_craft, new_scenario
from fastcraft.gui.mission import MissionWindow
from fastcraft.gui.worker import PropagateThread, RefineThread
from fastcraft.patch import PatchError, apply_patch
from fastcraft.phasing import departure_candidates
from fastcraft.presets import plan_preset
from fastcraft.results import RunResult
from fastcraft.scenario import Scenario, parse_scenario
from fastcraft.settings import Settings, load_settings, save_settings
from fastcraft.units import CRAFT_SCHEMA, SCENARIO_SCHEMA
from fastcraft.yamlio import DocumentError, dump_yaml, load_yaml, yaml_rt

_RECENT_LIMIT = 8


def find_default_craft() -> Path | None:
    """`default.craft.yaml` in the working directory, or beside a source checkout."""
    candidates = [
        Path.cwd() / "default.craft.yaml",
        Path(__file__).resolve().parents[2] / "default.craft.yaml",
    ]
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _dump(data: object) -> str:
    buffer = StringIO()
    yaml_rt().dump(data, buffer)
    return buffer.getvalue()


def launch(scenario: Path | None = None) -> None:
    """Open the desktop app. Imported only when the CLI is asked for a window."""
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    window = MainWindow()
    if scenario is not None:
        window.open_path(scenario)
    window.show()
    app.exec()


class MainWindow(QMainWindow):
    """File commands, both editors, validation, and Initiate."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("fastcraft")
        self.resize(980, 820)
        self._craft_path: Path | None = None
        self._scenario_path: Path | None = None
        self._craft_dirty = False
        self._scenario_dirty = False
        self._missions: list[MissionWindow] = []
        self._refine: RefineThread | None = None
        self._startup_note = ""
        self.craft_form = CraftForm()
        self.scenario_form = ScenarioForm()
        self.scenario_form.load_data(new_scenario())
        self.craft_form.load_requested.connect(self._load_craft_request)
        self.craft_form.changed.connect(self._craft_edited)
        self.scenario_form.changed.connect(self._scenario_edited)
        self.scenario_form.preset_requested.connect(self._preset)
        self.scenario_form.find_dates_requested.connect(self._find_dates)
        self.scenario_form.refine_requested.connect(self._refine_date)
        self.scenario_form.accept_epoch_requested.connect(self._accept_epoch)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.craft_form, "Craft")
        self.tabs.addTab(self.scenario_form, "Scenario")
        self.tabs.currentChanged.connect(lambda _index: self._retitle())
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        initiate = QPushButton("Initiate")
        initiate.clicked.connect(self._initiate)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(initiate)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.tabs)
        layout.addWidget(self.status)
        layout.addLayout(row)
        self.setCentralWidget(central)
        self._assistant = AssistantDock(self._assistant_context)
        self._assistant.patch_ready.connect(self._apply_assistant_patch)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._assistant)
        self._build_menu()
        self._load_startup_craft()
        if self._craft_path is None:
            self._refresh_status()
        self._retitle()

    def open_path(self, path: Path) -> None:
        """Open a craft, scenario, or playback file."""
        suffix = path.name
        if suffix.endswith(".npz"):
            self._show_playback(path)
            return
        try:
            data = load_yaml(path)
        except DocumentError as exc:
            QMessageBox.warning(self, "Open", str(exc))
            return
        schema = data.get("schema") if isinstance(data, dict) else None
        if schema == CRAFT_SCHEMA or suffix.endswith(".craft.yaml"):
            self._set_craft(data, path)
        elif schema == SCENARIO_SCHEMA or suffix.endswith(".scenario.yaml"):
            self._set_scenario(data, path)
        else:
            QMessageBox.warning(
                self, "Open", f"{path.name} is not a craft, scenario, or playback file."
            )
        self._remember(path)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction("New craft", self._new_craft)
        file_menu.addAction("New scenario", self._new_scenario)
        file_menu.addAction("Open…", self._open)
        file_menu.addAction("Open playback…", self._open_playback)
        file_menu.addSeparator()
        file_menu.addAction("Save", self._save)
        file_menu.addAction("Save as…", self._save_as)
        file_menu.addSeparator()
        self._recent = file_menu.addMenu("Recent")
        self._rebuild_recent()
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)
        settings_menu = self.menuBar().addMenu("Settings")
        settings_menu.addAction("Model, kernel, API key…", self._edit_settings)

    def _new_craft(self) -> None:
        if not self._confirm_discard(craft=True):
            return
        self._set_craft(new_craft(), None)
        self._craft_dirty = True

    def _new_scenario(self) -> None:
        if not self._confirm_discard(craft=False):
            return
        self._set_scenario(new_scenario(), None)
        self._scenario_dirty = True

    def _open(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Open",
            str(Path.cwd()),
            "fastcraft (*.craft.yaml *.scenario.yaml *.yaml *.npz)",
        )
        if path:
            self.open_path(Path(path))

    def _open_playback(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Open playback",
            str(Path.cwd()),
            "Playback (*.npz)",
        )
        if path:
            self._show_playback(Path(path))

    def _save(self) -> bool:
        if self.tabs.currentIndex() == 0:
            return self._save_craft(self._craft_path)
        return self._save_scenario(self._scenario_path)

    def _save_as(self) -> bool:
        if self.tabs.currentIndex() == 0:
            return self._save_craft(None)
        return self._save_scenario(None)

    def _save_craft(self, path: Path | None) -> bool:
        target = path
        if target is None:
            chosen, _selected = QFileDialog.getSaveFileName(
                self,
                "Save craft",
                "hauler.craft.yaml",
                "Craft (*.craft.yaml)",
            )
            if not chosen:
                return False
            target = Path(chosen)
        errors = self.craft_form.apply()
        if errors:
            QMessageBox.warning(self, "Save craft", "\n".join(errors))
            return False
        dump_yaml(self.craft_form.document(), target)
        self._craft_path = target
        self._craft_dirty = False
        self.craft_form.set_file_path(str(target))
        self._sync_craft_row()
        self._remember(target)
        self._retitle()
        self._refresh_status()
        return True

    def _save_scenario(self, path: Path | None) -> bool:
        target = path
        if target is None:
            chosen, _selected = QFileDialog.getSaveFileName(
                self,
                "Save scenario",
                "earth-mars.scenario.yaml",
                "Scenario (*.scenario.yaml)",
            )
            if not chosen:
                return False
            target = Path(chosen)
        errors = self.scenario_form.apply()
        if errors:
            QMessageBox.warning(self, "Save scenario", "\n".join(errors))
            return False
        dump_yaml(self.scenario_form.document(), target)
        self._scenario_path = target
        self._scenario_dirty = False
        self._remember(target)
        self._retitle()
        self._refresh_status()
        return True

    def _load_startup_craft(self) -> None:
        path = find_default_craft()
        if path is None:
            self._craft_path = None
            self.craft_form.load_data(new_craft())
            self.craft_form.set_file_path("")
            self._sync_craft_row()
            self._startup_note = "default.craft.yaml was not found."
            return
        try:
            data = load_yaml(path)
        except DocumentError as exc:
            self.craft_form.load_data(new_craft())
            self._startup_note = str(exc)
            return
        self._startup_note = ""
        self._set_craft(data, path)

    def _load_craft_request(self, text: str) -> None:
        path = Path(text).expanduser()
        if not path.is_file():
            QMessageBox.warning(self, "Open craft", f"Craft file not found:\n{path}")
            self.craft_form.set_file_path(str(self._craft_path) if self._craft_path else "")
            return
        try:
            data = load_yaml(path)
        except DocumentError as exc:
            QMessageBox.warning(self, "Open craft", str(exc))
            return
        self._startup_note = ""
        self._set_craft(data, path)

    def _set_craft(self, data: Any, path: Path | None) -> None:
        self._craft_path = path
        self.craft_form.load_data(data)
        self._craft_dirty = False
        if self._scenario_path is not None:
            self._scenario_dirty = True
        self.craft_form.set_file_path("" if path is None else str(path))
        self._sync_craft_row()
        self.tabs.setCurrentWidget(self.craft_form)
        self._retitle()
        self._refresh_status()

    def _set_scenario(self, data: Any, path: Path | None) -> None:
        self.scenario_form.load_data(data)
        self._scenario_path = path
        self._scenario_dirty = False
        self._sync_craft_row()
        self.tabs.setCurrentWidget(self.scenario_form)
        self._retitle()
        self._refresh_status()

    def _collect(self) -> tuple[Craft | None, Scenario | None, list[str]]:
        errors: list[str] = []
        try:
            errors.extend(self.craft_form.apply())
            errors.extend(self.scenario_form.apply())
        except (KeyError, TypeError, AttributeError) as exc:
            errors.append(str(exc))
            return None, None, errors
        craft: Craft | None = None
        try:
            craft = parse_craft(self.craft_form.document())
        except DocumentError as exc:
            errors.extend(exc.errors)
        scenario: Scenario | None = None
        if self._craft_path is None:
            errors.append(
                "Load a craft file on the Craft tab. Startup looks for default.craft.yaml."
            )
        try:
            scenario = parse_scenario(
                self.scenario_form.document(),
                source=self._scenario_path,
                craft=craft,
            )
        except DocumentError as exc:
            errors.extend(exc.errors)
        return craft, scenario, errors

    def _refresh_status(self) -> None:
        _craft, _scenario, errors = self._collect()
        if self._startup_note:
            errors = [self._startup_note, *errors]
        if errors:
            self.status.setText("\n".join(errors))
        else:
            self.status.setText("Craft and scenario are valid.")

    def _initiate(self) -> None:
        craft, scenario, errors = self._collect()
        if craft is None or scenario is None or errors:
            QMessageBox.warning(
                self,
                "Cannot initiate",
                "\n".join(errors) or "The craft or scenario is not valid.",
            )
            return
        if self._scenario_path is None:
            answer = QMessageBox.question(
                self,
                "Save scenario",
                "Save the scenario so the playback file has a folder.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save or not self._save_scenario(None):
                return
            craft, scenario, errors = self._collect()
            if craft is None or scenario is None or errors:
                QMessageBox.warning(self, "Cannot initiate", "\n".join(errors))
                return
        self.tabs.setCurrentWidget(self.scenario_form)
        ephemeris = open_ephemeris(scenario.epoch)
        window = MissionWindow(scenario, craft, ephemeris, self._scenario_path)
        thread = PropagateThread(scenario, craft, ephemeris)
        window.start(thread)
        self._missions.append(window)
        window.destroyed.connect(self._forget_mission)
        window.show()

    def _preset(self, kind: str) -> None:
        craft, scenario, errors = self._collect()
        if craft is None or scenario is None or errors:
            QMessageBox.warning(self, "Preset", "\n".join(errors) or "Fix the documents first.")
            return
        cargo = 0.0
        if kind == "return_loaded":
            cargo, ok = QInputDialog.getDouble(
                self,
                "Cargo",
                "Cargo mass, kg",
                10000.0,
                0.0,
                1.0e12,
                1,
            )
            if not ok:
                return
        ephemeris = open_ephemeris(scenario.epoch)
        plan = plan_preset(kind, scenario, craft, ephemeris, cargo_kg=cargo)
        if not plan.applied:
            QMessageBox.warning(self, "Preset", plan.message)
            return
        self.scenario_form.apply_plan(plan, craft)
        self._scenario_dirty = True
        QMessageBox.information(self, "Preset", plan.message)

    def _find_dates(self) -> None:
        _craft, scenario, errors = self._collect()
        if scenario is None or errors:
            QMessageBox.warning(self, "Dates", "\n".join(errors) or "The scenario is not valid.")
            return
        choices = departure_candidates(scenario, open_ephemeris(scenario.epoch))
        self.scenario_form.set_dates(choices)

    def _refine_date(self) -> None:
        craft, scenario, errors = self._collect()
        if craft is None or scenario is None or errors:
            QMessageBox.warning(self, "Refine", "\n".join(errors) or "Fix the documents first.")
            return
        center = self.scenario_form.selected_epoch()
        if center is None:
            QMessageBox.information(self, "Refine", "Find dates and select one first.")
            return
        if self._refine is not None and self._refine.isRunning():
            self._refine.cancel()
            return
        self.status.setText("Refining the selected epoch…")
        thread = RefineThread(scenario, craft, open_ephemeris(center), center)
        thread.finished_ok.connect(self._refined)
        thread.failed.connect(self._refine_failed)
        self._refine = thread
        thread.start()

    def _refined(self, epoch: object, score: float) -> None:
        if not isinstance(epoch, datetime):
            return
        self.scenario_form.set_refined(epoch, score)
        self.status.setText("Refined epoch is ready. Accept writes the epoch and nothing else.")

    def _refine_failed(self, message: str) -> None:
        self.status.setText(message)
        QMessageBox.warning(self, "Refine", message)

    def _accept_epoch(self) -> None:
        epoch = self.scenario_form.take_refined()
        if epoch is None:
            QMessageBox.information(self, "Epoch", "Refine a date before accepting it.")
            return
        stamp = epoch if epoch.tzinfo is not None else epoch.replace(tzinfo=UTC)
        self.scenario_form.set_epoch(stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self._scenario_dirty = True
        self._refresh_status()

    def _assistant_context(self) -> str:
        self.craft_form.apply()
        self.scenario_form.apply()
        parts = [
            "Craft YAML:",
            _dump(self.craft_form.document()),
            "Scenario YAML:",
            _dump(self.scenario_form.document()),
        ]
        summary = self._cached_summary()
        parts.append("Result summary:")
        parts.append(summary or "none")
        return "\n".join(parts)

    def _cached_summary(self) -> str:
        path = self._scenario_path
        if path is None:
            return ""
        stem = path.name.removesuffix(".scenario.yaml")
        if stem == path.name:
            stem = path.stem
        matches = sorted(path.parent.glob(f"{stem}-*.npz"))
        if not matches:
            return ""
        try:
            return RunResult.load(matches[-1]).summary()
        except OSError, KeyError, ValueError:
            return ""

    def _apply_assistant_patch(self, target: str, operations: object) -> None:
        if not isinstance(operations, list):
            return
        try:
            if target == "craft":
                self.craft_form.apply()
                updated = apply_patch(self.craft_form.document(), operations)
                self.craft_form.load_data(updated)
                self._craft_dirty = True
            else:
                self.scenario_form.apply()
                updated = apply_patch(self.scenario_form.document(), operations)
                self.scenario_form.load_data(updated)
                self._scenario_dirty = True
        except PatchError as exc:
            QMessageBox.warning(self, "Patch", str(exc))
            return
        self._refresh_status()

    def _show_playback(self, path: Path) -> None:
        try:
            result = RunResult.load(path)
        except (OSError, KeyError, ValueError) as exc:
            QMessageBox.warning(self, "Playback", str(exc))
            return
        craft, scenario, _errors = self._collect()
        if craft is None or scenario is None:
            try:
                craft = parse_craft(new_craft())
                data = new_scenario()
                data["name"] = result.name
                data["craft"] = "playback"
                scenario = parse_scenario(data, source=None, craft=craft)
            except DocumentError as exc:
                QMessageBox.warning(self, "Playback", str(exc))
                return
        text = result.epoch.replace("Z", "+00:00")
        try:
            epoch = datetime.fromisoformat(text)
        except ValueError:
            epoch = scenario.epoch
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=UTC)
        window = MissionWindow(scenario, craft, open_ephemeris(epoch), None)
        window.show_playback(result)
        self._missions.append(window)
        window.destroyed.connect(self._forget_mission)
        window.show()

    def _edit_settings(self) -> None:
        current = load_settings()
        dialog = _SettingsDialog(current, self)
        if dialog.exec() != _SettingsDialog.DialogCode.Accepted:
            return
        save_settings(dialog.settings())

    def _sync_craft_row(self) -> None:
        """Scenario row: the craft's name, then the loaded file name."""
        filename = "" if self._craft_path is None else self._craft_path.name
        if not filename:
            self.scenario_form.set_loaded_craft("", "")
            return
        self.scenario_form.set_loaded_craft(self.craft_form.name.text(), filename)

    def _craft_edited(self) -> None:
        self._craft_dirty = True
        self._sync_craft_row()
        self._refresh_status()

    def _scenario_edited(self) -> None:
        self._scenario_dirty = True
        self._refresh_status()

    def _confirm_discard(self, *, craft: bool) -> bool:
        dirty = self._craft_dirty if craft else self._scenario_dirty
        if not dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Discard edits",
            "Discard unsaved edits?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _remember(self, path: Path) -> None:
        from PySide6.QtCore import QSettings

        settings = QSettings("fastcraft", "fastcraft")
        recent = settings.value("recent", [])
        if not isinstance(recent, list):
            recent = []
        text = str(path)
        items = [text, *[str(item) for item in recent if str(item) != text]]
        settings.setValue("recent", items[:_RECENT_LIMIT])
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        from PySide6.QtCore import QSettings

        self._recent.clear()
        recent = QSettings("fastcraft", "fastcraft").value("recent", [])
        if not isinstance(recent, list) or not recent:
            action = self._recent.addAction("(empty)")
            action.setEnabled(False)
            return
        for item in recent:
            path = Path(str(item))
            self._recent.addAction(
                path.name, lambda _checked=False, name=path: self.open_path(name)
            )

    def _forget_mission(self, obj: object) -> None:
        self._missions = [window for window in self._missions if window is not obj]

    def _retitle(self) -> None:
        if self.tabs.currentIndex() == 0:
            name = "untitled craft" if self._craft_path is None else self._craft_path.name
        else:
            name = "untitled scenario" if self._scenario_path is None else self._scenario_path.name
        self.setWindowTitle(f"fastcraft — {name}")

    def closeEvent(self, event: object) -> None:  # noqa: N802
        if self._refine is not None and self._refine.isRunning():
            self._refine.cancel()
        if self._craft_dirty or self._scenario_dirty:
            answer = QMessageBox.question(
                self,
                "Quit",
                "Close with unsaved edits?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()  # type: ignore[attr-defined]
                return
        super().closeEvent(event)  # type: ignore[arg-type]


class _SettingsDialog(QDialog):
    """Small settings form. The API key stays out of the project tree."""

    def __init__(self, current: Settings, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        note = QLabel(
            "Saved in ~/.config/fastcraft/settings.yaml. OPENROUTER_API_KEY overrides "
            "the key. Neither is written into a craft or scenario."
        )
        note.setWordWrap(True)
        self.model = QLineEdit(current.model)
        self.kernel = QLineEdit(current.kernel)
        self.api_key = QLineEdit(current.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        browse = QPushButton("Browse kernel…")
        browse.clicked.connect(self._browse)
        form = QFormLayout()
        form.addRow("Model", self.model)
        form.addRow("Kernel", self.kernel)
        form.addRow("", browse)
        form.addRow("API key", self.api_key)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def settings(self) -> Settings:
        return Settings(
            model=self.model.text().strip() or Settings().model,
            api_key=self.api_key.text().strip(),
            kernel=self.kernel.text().strip(),
        )

    def _browse(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "JPL kernel", "", "Kernel (*.bsp)")
        if path:
            self.kernel.setText(path)
