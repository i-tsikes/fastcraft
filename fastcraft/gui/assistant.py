"""OpenRouter side panel. Edits are a JSON Patch; this panel never integrates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fastcraft.patch import PatchError, extract_patch
from fastcraft.settings import Settings

_SYSTEM = """\
You are an engineering aide for the fastcraft simulator.
Edit only fields in the fastcraft.craft/v1 and fastcraft.scenario/v1 documents.
Craft fields: schema, name, dry_mass_kg, tanks (water_kg, lh2_kg, lox_kg),
starship (dry_mass_kg, landing_propellant_kg), power (reactor_thermal_w,
electric_w, radiator_reject_w), electrolysis.max_kg_per_s, engines.hydrolox,
engines.mhd, optional engines.oxygen, geometry (tank_length_m, tank_radius_m,
radiator_span_m), notes.
electric_w must be below reactor_thermal_w. Waste heat, reactor_thermal_w
minus electric_w, must be within radiator_reject_w.
Scenario fields: schema, name, craft, epoch, frame (heliocentric_ecliptic_j2000),
bodies (sun, earth, mars), initial, arrival, ship (docked, none, empty, loaded),
cargo_kg only when ship is loaded, exactly four burns in order (departure,
accelerate, decelerate, insertion), electrolysis (on or off), notes.
MHD burns include oxygen: auto or off. Hydrolox burns do not have oxygen.
Do not invent fields. Do not claim you ran the integrator.
When you propose an edit, put one JSON Patch (RFC 6902) in a fenced json block.
Use {"target": "craft" or "scenario", "patch": [operations]}.
A bare JSON array is applied to the scenario.
"""


class AssistantDock(QDockWidget):
    """Question box, reply, and an Apply button for a proposed patch."""

    patch_ready = Signal(str, object)

    def __init__(self, context: Callable[[], str], parent: QWidget | None = None) -> None:
        super().__init__("Assistant", parent)
        self._context = context
        self._thread: _ChatThread | None = None
        self._patch: tuple[str, list[dict[str, Any]]] | None = None
        self._question = QPlainTextEdit()
        self._question.setPlaceholderText("Ask about the open craft or scenario.")
        self._question.setFixedHeight(80)
        self._reply = QPlainTextEdit()
        self._reply.setReadOnly(True)
        self._send = QPushButton("Send")
        self._send.clicked.connect(self._send_question)
        self._apply = QPushButton("Apply patch")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._emit_patch)
        self._note = QLabel("Apply changes memory only. Save writes the file.")
        self._note.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self._send)
        row.addWidget(self._apply)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self._question)
        layout.addLayout(row)
        layout.addWidget(self._reply)
        layout.addWidget(self._note)
        self.setWidget(body)

    def _send_question(self) -> None:
        settings = _load_settings()
        key = settings.resolved_api_key()
        if not key:
            self._reply.setPlainText(
                "No OpenRouter key. Set OPENROUTER_API_KEY, or save a key in Settings. "
                "The key is not stored in a craft or scenario file."
            )
            return
        question = self._question.toPlainText().strip()
        if not question:
            return
        user = f"{self._context()}\n\nQuestion:\n{question}"
        self._send.setEnabled(False)
        self._apply.setEnabled(False)
        self._patch = None
        self._reply.setPlainText("Waiting for OpenRouter…")
        self._thread = _ChatThread(key, settings.model, user)
        self._thread.finished_ok.connect(self._on_reply)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_reply(self, text: str) -> None:
        self._send.setEnabled(True)
        self._reply.setPlainText(text)
        try:
            found = extract_patch(text)
        except PatchError as exc:
            self._reply.appendPlainText(f"\n\nPatch was not usable: {exc}")
            return
        self._patch = found
        self._apply.setEnabled(found is not None)

    def _on_failed(self, message: str) -> None:
        self._send.setEnabled(True)
        self._reply.setPlainText(message)

    def _emit_patch(self) -> None:
        if self._patch is None:
            return
        target, operations = self._patch
        self.patch_ready.emit(target, operations)


def _load_settings() -> Settings:
    from fastcraft.settings import load_settings

    return load_settings()


class _ChatThread(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, model: str, user: str) -> None:
        super().__init__()
        self._api_key = api_key
        self._model = model
        self._user = user

    def run(self) -> None:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self._api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://github.com/i-tsikes/fastcraft",
                    "X-Title": "fastcraft",
                },
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": self._user},
                ],
            )
            content = response.choices[0].message.content or ""
            self.finished_ok.emit(content)
        except Exception as exc:
            self.failed.emit(str(exc))
