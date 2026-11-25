"""cytoflowgui main window: builds Tools > Category > Tool from the installed plugins."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton

from . import __version__
from .api import ToolContext
from .registry import discover, load_tool
from .run_ui import RunLogDialog
from .services.runmanager import RunManager
from .services.settings import data_dir, plugins_dir, settings
from .services.steps import Step
from .services.updater import UpdateResult, schedule_app_update
from .services.workspace import Workspace, clear_recent, recent_workspaces
from .setup_wizard import SetupDialog
from .steps_panel import StepsPanel
from .theme import DEFAULT_THEME, apply_theme, style_menu_bar, style_status_bar
from .update_dialog import UpdateDialog, start_check
from .widgets import PreferencesDialog
from .workspace_ui import browse_flow, choose_workspace, new_workspace_flow, open_path, reopen_last


class MainWindow(QMainWindow):
    def __init__(self, startup: bool = True):
        super().__init__()
        self.setWindowTitle("cytoflowgui")
        self.resize(720, 460)
        self.setCentralWidget(QLabel("Choose a tool from the Tools menu.",
                                     alignment=Qt.AlignmentFlag.AlignCenter))
        self._update_result: UpdateResult | None = None
        self._checker = None
        self.workspace: Workspace | None = None
        self._tool_name: str | None = None
        self.runs = RunManager(self)
        self.ctx = ToolContext(lambda: self.workspace, self.runs)
        self._logs: dict[str, RunLogDialog] = {}
        self.runs.run_started.connect(self._on_run_started)
        self.runs.run_output.connect(lambda sid, text: self._logs[sid].append(text) if sid in self._logs else None)
        self.runs.run_finished.connect(self._on_run_finished)
        self.runs.changed.connect(self._refresh_runs_button)
        self.runs.registry_state.connect(self._on_registry_state)
        self.steps_panel = StepsPanel(self.runs, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.steps_panel)
        self.steps_panel.hide()
        self.steps_panel.use_as_input.connect(self._use_step_as_input)
        self.steps_panel.rerun_requested.connect(self._rerun_step)
        self.runs.reconciled.connect(self._on_reconciled)
        self.steps_panel.visibilityChanged.connect(self._remember_panel)
        QApplication.instance().aboutToQuit.connect(self._shutdown)

        self.update_btn = QPushButton()
        self.update_btn.setFlat(True)
        self.update_btn.hide()
        self.update_btn.clicked.connect(lambda: self.open_updates(self._update_result))
        self.ws_btn = QPushButton()                  # full path of the workspace, click = open the folder
        self.ws_btn.setFlat(True)
        self.ws_btn.clicked.connect(self.show_workspace_folder)
        self.runs_btn = QPushButton()                # "2 steps running": click for the list
        self.runs_btn.setFlat(True)
        self.runs_btn.hide()
        self.runs_btn.clicked.connect(self._show_runs_menu)
        self.statusBar().addPermanentWidget(self.runs_btn)
        self.statusBar().addPermanentWidget(self.ws_btn)
        self.statusBar().addPermanentWidget(self.update_btn)

        ws_menu = self.menuBar().addMenu("&Workspace")
        ws_menu.addAction("New workspace…", self.new_workspace)
        ws_menu.addAction("Open workspace…", self.open_workspace)
        self.recent_menu = ws_menu.addMenu("Recent workspaces")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        ws_menu.addSeparator()
        ws_menu.addSeparator()
        self.panel_act = self.steps_panel.toggleViewAction()
        self.panel_act.setText("Steps panel")
        ws_menu.addAction(self.panel_act)
        self.repair_act = ws_menu.addAction("Repair registry…", self.repair_registry)
        self.close_act = ws_menu.addAction("Close workspace", self.close_workspace)
        self.show_act = ws_menu.addAction("Show in file manager", self.show_workspace_folder)

        self.tools_menu = self.menuBar().addMenu("&Tools")
        self.tools_menu.setToolTipsVisible(True)

        help_menu = self.menuBar().addMenu("?")
        for text, slot in (("Check for updates…", lambda: self.open_updates()),
                           ("Check setup…", lambda: SetupDialog(self).exec()),
                           ("Preferences…", lambda: PreferencesDialog(self).exec()),
                           ("Reload plugins", self.build_tools_menu),
                           ("Open plugins folder", lambda: QDesktopServices.openUrl(
                               QUrl.fromLocalFile(str(plugins_dir())))),
                           ("About", lambda: QMessageBox.about(
                               self, "About", f"cytoflowgui {__version__}"))):
            a = QAction(text, self)
            a.setMenuRole(QAction.MenuRole.NoRole)
            a.triggered.connect(slot)
            help_menu.addAction(a)

        self.build_tools_menu()
        style_menu_bar(self.menuBar())
        style_status_bar(self.statusBar())
        self._refresh_workspace_ui()
        if startup:                                # tests pass startup=False
            QTimer.singleShot(0, self._startup)

    # ---- startup sequence
    def _startup(self):
        self._report_last_update()
        fresh = not any(m.error is None for m in discover(plugins_dir()))
        if fresh:                                  # first run: fetch the tools, then check R
            self.open_updates()
            if not self.isVisible():
                return
        if str(settings().value("setup/completed", "0")) != "1":
            SetupDialog(self).exec()
        self.set_workspace(reopen_last(self) or choose_workspace(self))
        if not fresh:
            self._background_check()

    def _report_last_update(self):
        f = data_dir() / "last_update.json"
        if not f.is_file():
            return
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
            f.unlink()
        except Exception:  # noqa: BLE001
            return
        if info.get("ok"):
            QMessageBox.information(self, "Updated", f"cytoflowgui was updated to {info.get('version')}.")
        else:
            QMessageBox.warning(self, "Update failed",
                                f"The application update could not be installed.\nDetails: {info.get('log')}")

    # ---- workspace
    def _release_workspace(self):
        if self.workspace:
            self.workspace.close()

    def _shutdown(self):
        """Application exit: cancel the running steps, close the books, release the workspace."""
        self.runs.shutdown()
        self._release_workspace()

    def _remember_panel(self, visible: bool):
        if self.workspace:                         # hidden because there is no workspace: not a choice
            settings().setValue("ui/steps_panel", "1" if visible else "0")

    def _on_reconciled(self, report: dict):
        n, m = len(report["interrupted"]), len(report["missing"])
        parts = ([f"{n} step(s) marked interrupted"] if n else []) + ([f"{m} step folder(s) missing"] if m else [])
        self.statusBar().showMessage("Workspace check: " + ", ".join(parts), 12000)

    def repair_registry(self):
        if QMessageBox.question(self, "Repair registry", "Rebuild steps.tsv from the step folders?\n"
                                "Folders with a step ID but no readable step.json are adopted.") != QMessageBox.StandardButton.Yes:
            return
        try:
            r = self.runs.repair()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Repair registry", str(e))
            return
        lines = [f"Adopted: {', '.join(r['adopted']) or 'none'}", f"Interrupted: {', '.join(r['interrupted']) or 'none'}",
                 f"Missing folders: {', '.join(r['missing']) or 'none'}"]
        if r["lineage_issues"]:
            lines.append("Lineage issues:\n  " + "\n  ".join(r["lineage_issues"]))
        QMessageBox.information(self, "Registry repaired", "\n".join(lines))

    def _rerun_step(self, step_id: str):
        reg = self.runs.registry
        step = reg.steps.get(step_id) if reg else None
        manifest = next((m for m in discover(plugins_dir()) if m.id == step.plugin), None) if step else None
        if not manifest or manifest.error:
            QMessageBox.warning(self, "Re-run", f"The tool '{step.plugin if step else '?'}' is not available.")
            return
        self.open_tool(manifest)
        tool = self.centralWidget()
        if not getattr(tool, "supports_rerun", False):
            QMessageBox.information(self, "Re-run", "This tool cannot restore its parameters yet.")
            return
        warns = tool.rerun(step)
        self.statusBar().showMessage(f"Parameters of step {step.id} restored: edit them and press Run", 10000)
        if warns:
            QMessageBox.warning(self, "Re-run", "\n".join(warns))

    def _use_step_as_input(self, step_id: str):
        tool = self.centralWidget()
        if not hasattr(tool, "use_step_as_input"):
            QMessageBox.information(self, "Use as input", "Open a tool first, then choose the step.")
        elif not tool.use_step_as_input(step_id):
            QMessageBox.information(self, "Use as input", "This tool has no input that takes a step output.")

    def _tool_running(self) -> bool:
        return bool(self.runs.running())

    def closeEvent(self, event):
        n = len(self.runs.running())
        if n and QMessageBox.question(
                self, "Steps running",
                f"{n} step{'s are' if n > 1 else ' is'} still running. Quit and cancel "
                f"{'them' if n > 1 else 'it'}?") != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        event.accept()

    def set_workspace(self, ws: Workspace | None):
        """Make `ws` the active workspace (None = no workspace). The new lock is taken before the old one is released."""
        if ws is None:
            return
        self.runs.set_workspace(ws)
        old, self.workspace = self.workspace, ws
        self._tool_name = None
        if old:
            old.close()
        self.setCentralWidget(QLabel("Choose a tool from the Tools menu.",
                                     alignment=Qt.AlignmentFlag.AlignCenter))
        self._refresh_workspace_ui()

    def _refresh_workspace_ui(self):
        ws = self.workspace
        mode = "  [read-only]" if ws and ws.read_only else ""
        tool = f" — {self._tool_name}" if self._tool_name else ""
        self.setWindowTitle(f"{ws.name}{mode}{tool} — cytoflowgui" if ws else "cytoflowgui")
        self.ws_btn.setText(f"Workspace: {ws.root}{mode}" if ws else "No workspace (Workspace menu)")
        self.ws_btn.setToolTip("Click to open the workspace folder" if ws else "")
        self.tools_menu.setEnabled(ws is not None)
        self.panel_act.setEnabled(ws is not None)
        self.repair_act.setEnabled(ws is not None)
        self.steps_panel.setVisible(ws is not None and str(settings().value("ui/steps_panel", "1")) != "0")
        self.close_act.setEnabled(ws is not None)
        self.show_act.setEnabled(ws is not None)

    def _can_leave_workspace(self) -> bool:
        if self._tool_running():
            QMessageBox.warning(self, "Workspace", "A tool is still running. Wait for it to finish "
                                                   "before changing workspace.")
            return False
        return True

    def new_workspace(self):
        if self._can_leave_workspace():
            self.set_workspace(new_workspace_flow(self))

    def open_workspace(self):
        if self._can_leave_workspace():
            self.set_workspace(choose_workspace(self))

    def open_workspace_path(self, path: str):
        if not self._can_leave_workspace():
            return
        if self.workspace and Path(path).resolve() == self.workspace.root:
            return
        self.set_workspace(open_path(self, path))

    def close_workspace(self):
        if not self.workspace or not self._can_leave_workspace():
            return
        self.runs.set_workspace(None)
        self._release_workspace()
        self.workspace = None
        self._tool_name = None
        self.setCentralWidget(QLabel("Open or create a workspace (Workspace menu).",
                                     alignment=Qt.AlignmentFlag.AlignCenter))
        self._refresh_workspace_ui()

    def show_workspace_folder(self):
        if self.workspace:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.root)))

    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.clear()
        recent = recent_workspaces()
        if not recent:
            menu.addAction("(none)").setEnabled(False)
            return
        for p in recent:
            act = menu.addAction(f"{Path(p).name}    —    {p}".replace("&", "&&"))
            act.setEnabled(Path(p).is_dir())
            act.setToolTip(p)
            act.triggered.connect(lambda _=False, p=p: self.open_workspace_path(p))
        menu.addSeparator()
        menu.addAction("Clear list", clear_recent)

    # ---- steps being run
    def _on_run_started(self, step: Step):
        dlg = RunLogDialog(step, self.workspace.root, self.runs, self)
        self._logs[step.id] = dlg
        dlg.finished.connect(lambda _=0, sid=step.id: self._logs.pop(sid, None))
        dlg.show()
        self.statusBar().showMessage(f"Step {step.id} started", 5000)

    def _on_run_finished(self, step: Step):
        if step.id in self._logs:
            self._logs[step.id].on_finished(step)
        self.statusBar().showMessage(f"Step {step.id} ({step.label}) finished: {step.status}", 10000)

    def _refresh_runs_button(self):
        n = len(self.runs.running())
        self.runs_btn.setVisible(n > 0)
        self.runs_btn.setText(f"{n} step{'s' if n > 1 else ''} running")

    def _show_runs_menu(self):
        menu = QMenu(self)
        for step in self.runs.running():
            sub = menu.addMenu(f"{step.id}  {step.label}".replace("&", "&&"))
            sub.addAction("Show log", lambda sid=step.id: self._logs[sid].show() if sid in self._logs else None)
            sub.addAction("Cancel step", lambda sid=step.id: self.runs.cancel(sid))
        menu.exec(self.runs_btn.mapToGlobal(self.runs_btn.rect().topLeft()))

    def _on_registry_state(self, ok: bool, message: str):
        if not ok:
            self.statusBar().showMessage(message, 8000)

    # ---- updates (notify, then confirm in the dialog)
    def _background_check(self):
        today = date.today().isoformat()
        if str(settings().value("update/last_check", "")) == today:
            return                                 # at most one automatic check per day
        self._checker = start_check(self)
        self._checker.result.connect(self._on_check)
        self._checker.start()

    def _on_check(self, res: UpdateResult):
        s = settings()
        s.setValue("update/last_check", date.today().isoformat())
        s.sync()
        n = sum(1 for i in res.items if not i.blocked)
        if n:
            self._update_result = res
            self.update_btn.setText(f"{n} update{'s' if n > 1 else ''} available")
            self.update_btn.show()

    def open_updates(self, result: UpdateResult | None = None):
        dlg = UpdateDialog(self, result)
        dlg.exec()
        if dlg.installed_any:
            self.build_tools_menu()
            self.update_btn.hide()
        if dlg.restart:
            wheel, version = dlg.restart
            if self.runs.running():
                QMessageBox.information(self, "Update", "The update is downloaded, but steps are running. "
                                        "Check for updates again once they have finished.")
                return
            try:
                schedule_app_update(wheel, version)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "Update", f"Cannot start the update:\n{e}")
                return
            self.hide()
            QApplication.instance().quit()

    # ---- tools menu
    def build_tools_menu(self):
        self.tools_menu.clear()
        manifests = discover(plugins_dir())
        if not manifests:
            self.tools_menu.addAction("No tool installed").setEnabled(False)
            return
        submenus = {}
        for m in manifests:
            sub = submenus.get(m.category)
            if sub is None:
                sub = submenus[m.category] = self.tools_menu.addMenu(m.category)
                sub.setToolTipsVisible(True)
            act = sub.addAction(m.name)
            if m.error:
                act.setEnabled(False)
                act.setToolTip(f"Unavailable: {m.error}")
            else:
                act.setToolTip(m.tooltip)
                act.setStatusTip(m.tooltip)
                act.triggered.connect(lambda _=False, m=m: self.open_tool(m))

    def open_tool(self, m):
        if not self.workspace:
            QMessageBox.information(self, "Workspace", "Open or create a workspace first.")
            return
        try:
            tool = load_tool(m, self.ctx, self)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Plugin error", f"Cannot load '{m.name}':\n{e}")
            return
        self.setCentralWidget(tool)
        self.resize(self.size().expandedTo(tool.sizeHint()))
        self._tool_name = m.name
        self._refresh_workspace_ui()


def main():
    app = QApplication(sys.argv)
    apply_theme(str(settings().value("ui/theme", DEFAULT_THEME)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
