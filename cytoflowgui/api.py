"""Public plugin API (version 3).

A plugin GUI subclasses ToolPlugin and implements build_ui(), validate() and
collect_parameters(). The core provides the step label and comment, the Help and Run buttons,
the step folder inside the workspace, the JSON parameters (the plugin's own plus the step context),
the R pre-flight check, the process, the console log and the registry updates.

Step context added by the core to the JSON read by the Rmd:
  step_id, label, comment, workspace_dir, step_dir, data_dir, reports_dir, helpers_dir,
  input_step_ids, input_steps, api_version, plugin_id, plugin_version.
The Rmd writes FCS/RDS to continue the analysis in data_dir and end products (XLSX, text) in
reports_dir, creating them when needed. Plugins no longer choose an output folder.
helpers_dir holds the R helpers shared by the scripts: helpers.R (to source) and footer.Rmd (a
child document); see the Rmd contract in CLAUDE.md. Version 3 requires the scripts to use them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from . import API_VERSION
from .manifest import Manifest
from .services import stepid
from .input_ui import InputPicker
from .services import lineage
from .services.rinfo import find_rscript
from .services.runmanager import RunError, RunManager
from .services.runner import missing_r_packages
from .services.settings import settings
from .services.workspace import Workspace
from .widgets import HelpDialog

__all__ = ["ToolPlugin", "ToolContext", "API_VERSION"]


class ToolContext:
    """What the application gives to a tool: the active workspace and the run manager."""

    def __init__(self, get_workspace, runs: RunManager):
        self._get_workspace, self.runs = get_workspace, runs

    @property
    def workspace(self) -> Workspace | None:
        return self._get_workspace()

    @property
    def registry(self):
        return self.runs.registry


class ToolPlugin(QWidget):
    def __init__(self, manifest: Manifest, ctx: ToolContext | None = None, parent=None):
        super().__init__(parent)
        self.manifest, self.ctx = manifest, ctx
        self._r_checked = False
        self._pickers: dict[str, InputPicker] = {}
        self._rerun_of = ""

        self.intro = QLabel()                  # plugins set the short explanation text
        self.intro.setWordWrap(True)

        self.label_edit = QLineEdit()          # step label and comment: provided for every tool
        self.label_edit.setPlaceholderText(f"optional, default: {manifest.id}")
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("optional")
        self.folder_preview = QLabel()
        self.folder_preview.setStyleSheet("color: gray")
        self.label_edit.textChanged.connect(self._update_preview)
        self._update_preview()
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFrameShadow(QFrame.Shadow.Sunken)

        self.form = QFormLayout()              # the step strip first, then the plugin's own rows
        self.form.addRow("Step label:", self.label_edit)
        self.form.addRow("Comment:", self.comment_edit)
        self.form.addRow("", self.folder_preview)
        self.form.addRow(rule)
        self.help_btn = QPushButton("Help")
        self.run_btn = QPushButton("Run")
        self.run_btn.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.help_btn)
        row.addWidget(self.run_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.intro)
        lay.addLayout(self.form)
        lay.addStretch(1)
        lay.addLayout(row)

        self.help_btn.clicked.connect(self._show_help)
        self.run_btn.clicked.connect(self._run)
        self.build_ui()

    # ---- to implement in plugins
    def build_ui(self) -> None:
        raise NotImplementedError

    def validate(self) -> str | None:
        """Return an error message, or None if parameters are valid."""
        return None

    def collect_parameters(self) -> dict:
        """Return the JSON-serialisable parameters of the tool (not the step context)."""
        raise NotImplementedError

    def apply_parameters(self, params: dict) -> list[str]:
        """Fill the form from saved parameters (the inverse of collect_parameters). Return warnings for
        values that cannot be restored. Needed for re-run; unknown keys must be ignored, missing ones
        left at their defaults."""
        raise NotImplementedError

    # ---- provided by the core
    def setting(self, key: str, default=""):
        """Read a value remembered for this plugin (stored in the INI file)."""
        return settings().value(f"plugins/{self.manifest.id}/{key}", default)

    def set_setting(self, key: str, value) -> None:
        s = settings()
        s.setValue(f"plugins/{self.manifest.id}/{key}", value)
        s.sync()

    def input_picker(self, param: str, filter_: str = "All files (*)", start_dir=None) -> InputPicker:
        """File field for an input declared in plugin.toml ([[inputs]]): shows where the file comes from,
        offers 'From step…' for lineage inputs, accepts drag and drop."""
        spec = next((i for i in self.manifest.inputs if i.param == param), None)
        if spec is None:
            raise ValueError(f"'{param}' is not declared in [[inputs]] of plugin.toml")
        picker = InputPicker(self.ctx, spec, filter_, start_dir, self)
        self._pickers[param] = picker
        return picker

    @property
    def supports_rerun(self) -> bool:
        return type(self).apply_parameters is not ToolPlugin.apply_parameters

    def rerun(self, step) -> list[str]:
        """Prefill the tool from a previous step. Inputs inside the workspace are re-resolved from their
        relative path; external inputs are checked against their recorded size and date."""
        params, warns = dict(step.params), []
        ws = self.ctx.workspace
        for f in step.input_files:
            name = f.get("param")
            if f.get("inside") and f.get("rel") and ws:
                params[name] = str(ws.root / f["rel"])
            path = Path(params.get(name) or "")
            if not path.exists():
                warns.append(f"{name}: file not found ({path})")
            elif f.get("size") is not None and path.is_file():
                st = path.stat()
                when = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if st.st_size != f["size"] or when != f.get("mtime"):
                    warns.append(f"{name}: the file changed since step {step.id}")
        warns += self.apply_parameters(params)
        if step.plugin_version and step.plugin_version != self.manifest.version:
            warns.append(f"Step {step.id} used plugin version {step.plugin_version}, "
                         f"this is {self.manifest.version}: check the parameters")
        self.label_edit.setText("" if step.label == step.plugin else step.label)
        self.comment_edit.setText(step.comment)
        self._rerun_of = step.id
        return warns

    def use_step_as_input(self, step_id: str) -> bool:
        """Fill the first lineage input with a file of the given step (asks which one)."""
        for spec in self.manifest.inputs:
            if spec.lineage and spec.param in self._pickers:
                return self._pickers[spec.param].choose_from_step(step_id)
        return False

    def _input_warnings(self) -> list[str]:
        out = []
        for param, picker in self._pickers.items():
            ref = picker.ref()
            if ref:
                level, text = lineage.describe(ref, self.ctx.registry)
                if level == "warn":
                    out.append(f"{param}: {text}")
        return out

    def _update_preview(self):
        name = stepid.sanitize_label(self.label_edit.text(), self.manifest.id)
        self.folder_preview.setText(f"Step folder: <id>_{name}")

    def _show_help(self):
        f = self.manifest.dir / self.manifest.help if self.manifest.help else None
        html = f.read_text(encoding="utf-8") if f and f.is_file() else f"<p>{self.manifest.tooltip}</p>"
        HelpDialog(html, self).exec()

    def _warn(self, title: str, text: str):
        QMessageBox.warning(self, title, text)

    def _run(self):
        if (err := self.validate()):
            return self._warn("Missing parameter", err)
        ws = self.ctx.workspace if self.ctx else None
        if ws is None:
            return self._warn("Workspace", "Open or create a workspace first (Workspace menu).")
        if ws.read_only:
            return self._warn("Workspace", "This workspace is open read-only: steps cannot be run.")
        rscript = find_rscript()
        if not rscript:
            return self._warn("R not found", "Open ? > Check setup… to detect or install R.")
        m = self.manifest
        if not self._r_checked and (m.requires_r or m.requires_bioc):
            try:
                missing = missing_r_packages(rscript, m.requires_r + m.requires_bioc)
            except Exception as e:  # noqa: BLE001
                return self._warn("R error", f"Cannot query R packages:\n{e}")
            if missing:
                return self._warn("Missing R packages", "Missing: " + ", ".join(missing) +
                                  "\n\nOpen ? > Check setup… to install them.")
            self._r_checked = True

        warns = self._input_warnings()
        if warns and QMessageBox.question(
                self, "Input warning", "\n".join(warns) + "\n\nRun anyway?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctx.runs.start(rscript, m, self.collect_parameters(),
                                self.label_edit.text(), self.comment_edit.text(), self._rerun_of)
        except RunError as e:
            return self._warn("Cannot start the step", str(e))
        self._rerun_of = ""
        self.run_btn.setEnabled(False)         # avoid an accidental double click: runs are concurrent
        QTimer.singleShot(1500, lambda: self.run_btn.setEnabled(True))
