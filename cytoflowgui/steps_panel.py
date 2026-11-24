"""Dockable panel listing the steps of the workspace, with filters, lineage and a detail pane."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .services.runmanager import LOG_FILE, RunError, RunManager
from .services.steps import RUNNING, Step, duration_text, local_time

COLUMNS = [("ID", "id"), ("Label", "label"), ("Plugin", "plugin"), ("Status", "status"),
           ("Started", "started"), ("Duration", "duration"), ("Comment", "comment"),
           ("Inputs", "inputs"), ("Exit code", "exit_code"), ("Versions", "versions")]
HIDDEN = {"exit_code", "versions"}
STATUS_COLORS = {"running": "#757575", "ok": "#2e7d32", "failed": "#c62828", "cancelled": "#b26a00",
                 "interrupted": "#b26a00", "missing": "#c62828", "deleted": "#9e9e9e", "unknown": "#757575"}
LOG_TAIL_BYTES = 200_000


class StepsPanel(QDockWidget):
    use_as_input = Signal(str)                       # step id: fill an input of the open tool
    rerun_requested = Signal(str)                    # step id: reopen its tool with the same parameters

    def __init__(self, runs: RunManager, parent=None):
        super().__init__("Steps", parent)
        self.runs = runs
        self._live: dict[str, list[str]] = {}        # console text of running steps
        self._focus: str | None = None               # step whose lineage is shown, if any
        self._current: str | None = None

        # ---- filters
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search label, comment, plugin, ID…")
        self.search.setClearButtonEnabled(True)
        self.status_box, self.plugin_box = QComboBox(), QComboBox()
        self.status_box.addItems(["All statuses", "running", "ok", "failed", "cancelled", "interrupted", "missing",
                                  "deleted", "unknown"])
        self.plugin_box.addItem("All plugins")
        self.tree_check = QCheckBox("Lineage tree")
        self.tree_check.setToolTip("Show each step under the step it uses as input")
        self.banner = QLabel()
        self.clear_btn = QPushButton("Show all")
        self.clear_btn.clicked.connect(lambda: self.set_focus(None))
        banner_row = QWidget()
        bl = QHBoxLayout(banner_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.banner, 1)
        bl.addWidget(self.clear_btn)
        self.banner_row = banner_row
        banner_row.hide()
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.status_box)
        top.addWidget(self.plugin_box)
        top.addWidget(self.tree_check)

        # ---- table
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([c[0] for c in COLUMNS])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.currentItemChanged.connect(lambda cur, _: self._select(cur))
        head = self.tree.header()
        head.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        head.customContextMenuRequested.connect(self._header_menu)
        for i, (_, key) in enumerate(COLUMNS):
            self.tree.setColumnHidden(i, key in HIDDEN)
        self.tree.setColumnWidth(0, 120)

        # ---- details
        self.d_title, self.d_status, self.d_inputs = QLabel(), QLabel(), QLabel()
        for lab in (self.d_title, self.d_status, self.d_inputs):
            lab.setWordWrap(True)
        self.d_inputs.setTextFormat(Qt.TextFormat.RichText)
        self.d_inputs.linkActivated.connect(self._link)
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("comment")
        self.comment.editingFinished.connect(self._save_comment)
        self.params_view = QPlainTextEdit(readOnly=True)
        self.params_view.setMaximumHeight(90)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.report_btn, self.data_btn = QPushButton("Report"), QPushButton("Data folder")
        self.reports_btn, self.folder_btn = QPushButton("Reports folder"), QPushButton("Step folder")
        self.cancel_btn = QPushButton("Cancel step")
        self.rerun_btn = QPushButton("Re-run…")
        self.rerun_btn.setToolTip("Open the tool with the parameters of this step, to change and run again")
        self.rerun_btn.clicked.connect(lambda: self._current and self.rerun_requested.emit(self._current))
        for btn, rel in ((self.report_btn, "html_report"), (self.data_btn, "data_folder"),
                         (self.reports_btn, "report_folder")):
            btn.clicked.connect(lambda _=False, r=rel: self._open_rel(r))
        self.folder_btn.clicked.connect(lambda: self._open_rel("folder"))
        self.cancel_btn.clicked.connect(lambda: self._current and self.runs.cancel(self._current))
        buttons = QHBoxLayout()
        for b in (self.report_btn, self.data_btn, self.reports_btn, self.folder_btn, self.rerun_btn, self.cancel_btn):
            buttons.addWidget(b)
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 4, 0, 0)
        for w in (self.d_title, self.d_status, self.comment, self.d_inputs, self.params_view):
            dl.addWidget(w)
        dl.addLayout(buttons)
        dl.addWidget(QLabel("Console"))
        dl.addWidget(self.log_view, 1)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.addLayout(top)
        bv.addWidget(self.banner_row)
        bv.addWidget(split, 1)
        self.setWidget(body)
        self.setMinimumWidth(460)

        # ---- wiring
        for sig in (self.search.textChanged, self.status_box.currentIndexChanged,
                    self.plugin_box.currentIndexChanged, self.tree_check.toggled):
            sig.connect(lambda *_: self.refresh())
        runs.changed.connect(self.refresh)
        runs.run_output.connect(self._on_output)
        runs.run_finished.connect(lambda step: self._current == step.id and self._show(step.id))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.refresh()

    # ---- data
    def _registry(self):
        return self.runs.registry

    def _steps(self) -> dict[str, Step]:
        reg = self._registry()
        return reg.steps if reg else {}

    def set_focus(self, sid: str | None) -> None:
        """Show only a step with its ancestors and descendants (None = all steps)."""
        self._focus = sid
        self.refresh()

    def _visible_ids(self) -> list[str]:
        steps, reg = self._steps(), self._registry()
        ids = set(steps)
        if self._focus and self._focus in steps:
            ids = {self._focus, *reg.ancestors(self._focus), *reg.descendants(self._focus)}
        status = self.status_box.currentText()
        plugin = self.plugin_box.currentText()
        needle = self.search.text().strip().lower()
        out = []
        for sid in ids:
            s = steps[sid]
            if status != "All statuses" and s.status != status:
                continue
            if plugin != "All plugins" and s.plugin != plugin:
                continue
            if needle and needle not in f"{s.id} {s.label} {s.plugin} {s.comment}".lower():
                continue
            out.append(sid)
        return sorted(out, reverse=True)           # newest first

    def refresh(self) -> None:
        steps = self._steps()
        keep = self._current
        plugins = sorted({s.plugin for s in steps.values()})
        current_plugin = self.plugin_box.currentText()
        self.plugin_box.blockSignals(True)
        self.plugin_box.clear()
        self.plugin_box.addItem("All plugins")
        self.plugin_box.addItems(plugins)
        i = self.plugin_box.findText(current_plugin)
        self.plugin_box.setCurrentIndex(max(0, i))
        self.plugin_box.blockSignals(False)

        visible = self._visible_ids()
        self.banner_row.setVisible(bool(self._focus and self._focus in steps))
        if self._focus:
            self.banner.setText(f"Lineage of step {self._focus} (ancestors and descendants)")

        self.tree.blockSignals(True)
        self.tree.clear()
        items: dict[str, QTreeWidgetItem] = {}
        if self.tree_check.isChecked():
            vis = set(visible)
            first_parent = {sid: next((p for p in steps[sid].inputs if p in vis), None) for sid in visible}
            kids: dict[str | None, list[str]] = {}
            for sid in visible:                        # visible is newest first
                kids.setdefault(first_parent[sid], []).append(sid)

            def add(sid: str, parent_item) -> None:
                item = self._make_item(steps[sid])
                (parent_item or self.tree.invisibleRootItem()).addChild(item)
                items[sid] = item
                for child in kids.get(sid, []):
                    add(child, item)

            for root in kids.get(None, []):
                add(root, None)
            self.tree.expandAll()
        else:
            for sid in visible:
                item = self._make_item(steps[sid])
                self.tree.addTopLevelItem(item)
                items[sid] = item
        self.tree.blockSignals(False)
        if keep in items:
            self.tree.setCurrentItem(items[keep])
            self._show(keep)
        elif items:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self._current = None
            self._show(None)

    def _make_item(self, s: Step) -> QTreeWidgetItem:
        cells = {
            "id": s.id, "label": s.label, "plugin": s.plugin, "status": s.status,
            "started": local_time(s.started_at), "duration": duration_text(s.started_at, s.finished_at),
            "comment": s.comment, "inputs": ", ".join(s.inputs),
            "exit_code": "" if s.exit_code is None else str(s.exit_code),
            "versions": f"app {s.app_version} / {s.plugin} {s.plugin_version}",
        }
        item = QTreeWidgetItem([cells[key] for _, key in COLUMNS])
        item.setData(0, Qt.ItemDataRole.UserRole, s.id)
        item.setForeground(3, QBrush(QColor(STATUS_COLORS.get(s.status, "gray"))))
        return item

    def _tick(self) -> None:
        """Running steps: update their elapsed time once a second."""
        steps = self._steps()
        for item in self._all_items():
            s = steps.get(item.data(0, Qt.ItemDataRole.UserRole))
            if s and s.status == RUNNING:
                item.setText(5, duration_text(s.started_at))

    # ---- selection and details
    def _select(self, item) -> None:
        self._current = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._show(self._current)

    def _all_items(self) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []

        def walk(parent: QTreeWidgetItem) -> None:
            for i in range(parent.childCount()):
                out.append(parent.child(i))
                walk(parent.child(i))

        walk(self.tree.invisibleRootItem())
        return out

    def _focus_item(self, sid: str) -> bool:
        for item in self._all_items():
            if item.data(0, Qt.ItemDataRole.UserRole) == sid:
                self.tree.setCurrentItem(item)
                return True
        return False

    def select_step(self, sid: str) -> None:
        if self._focus_item(sid):
            return
        self.search.clear()                          # hidden by a filter: show everything and retry
        self.status_box.setCurrentIndex(0)
        self.plugin_box.setCurrentIndex(0)
        self._focus = None
        self.refresh()
        self._focus_item(sid)

    def _show(self, sid: str | None) -> None:
        s = self._steps().get(sid) if sid else None
        ro = bool(self._registry() and self._registry().ws.read_only)
        self.comment.setReadOnly(ro or s is None)
        for b in (self.report_btn, self.data_btn, self.reports_btn, self.folder_btn, self.cancel_btn, self.rerun_btn):
            b.setEnabled(False)
        if s is None:
            for w in (self.d_title, self.d_status, self.d_inputs):
                w.setText("")
            self.comment.clear()
            self.params_view.clear()
            self.log_view.clear()
            return
        self.d_title.setText(f"<b>{s.id}</b>  {s.label} — {s.plugin} {s.plugin_version}")
        color = STATUS_COLORS.get(s.status, "gray")
        code = "" if s.exit_code is None else f", exit code {s.exit_code}"
        self.d_status.setText(f"<b style='color:{color}'>{s.status}</b>{code} — started "
                              f"{local_time(s.started_at)}"
                              + (f", finished {local_time(s.finished_at)}" if s.finished_at else "")
                              + f" ({duration_text(s.started_at, s.finished_at)})")
        if self.comment.text() != s.comment and not self.comment.hasFocus():
            self.comment.setText(s.comment)
        self.d_inputs.setText(self._inputs_html(s))
        self.params_view.setPlainText("\n".join(f"{k}: {self._short(v)}" for k, v in s.params.items()))
        self.report_btn.setEnabled(bool(s.html_report))
        self.data_btn.setEnabled(bool(s.data_folder))
        self.reports_btn.setEnabled(bool(s.report_folder))
        self.folder_btn.setEnabled(bool(s.folder))
        self.cancel_btn.setEnabled(s.status == RUNNING)
        self.rerun_btn.setEnabled(bool(s.params) and not ro and s.status != RUNNING)
        self._load_log(s)

    @staticmethod
    def _short(v) -> str:
        text = ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)
        return text if len(text) < 90 else text[:40] + " … " + text[-40:]

    def _inputs_html(self, s: Step) -> str:
        if not s.input_files:
            return "<i>No input file recorded</i>"
        lines = []
        for f in s.input_files:
            where = (f"<a href='step:{f['step']}'>step {f['step']}</a> / {f['rel']}" if f.get("step")
                     else f"workspace / {f['rel']}" if f.get("inside") else f"external: {f['path']}")
            lines.append(f"<b>{f['param']}</b> ← {where}")
        return "<br>".join(lines)

    def _link(self, href: str) -> None:
        if href.startswith("step:"):
            self.select_step(href[5:])

    def _load_log(self, s: Step) -> None:
        if s.id in self._live:
            self.log_view.setPlainText("".join(self._live[s.id]))
        else:
            f = self._registry().step_dir(s) / LOG_FILE
            try:
                with open(f, "rb") as fh:
                    fh.seek(0, 2)
                    size = fh.tell()
                    fh.seek(max(0, size - LOG_TAIL_BYTES))
                    self.log_view.setPlainText(fh.read().decode("utf-8", "replace"))
            except OSError:
                self.log_view.setPlainText("")
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_output(self, sid: str, text: str) -> None:
        self._live.setdefault(sid, []).append(text)
        if sid == self._current:
            self.log_view.appendPlainText(text.rstrip("\n"))

    def _save_comment(self) -> None:
        s = self._steps().get(self._current) if self._current else None
        if s and not self.comment.isReadOnly() and self.comment.text().strip() != s.comment:
            self.runs.set_comment(s.id, self.comment.text())

    # ---- opening things
    def _open_rel(self, what: str) -> None:
        s = self._steps().get(self._current) if self._current else None
        if not s:
            return
        root = self._registry().ws.root
        target = root / (s.folder if what == "folder" else getattr(s, what))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def delete_step(self, sid: str) -> None:
        """Ask, then send the step folder to the Recycle Bin. A tombstone row stays in the registry."""
        reg = self._registry()
        s = reg.steps.get(sid) if reg else None
        if not s:
            return
        later = [c for c in reg.children(sid) if reg.steps[c].status != "deleted"]
        text = f"Delete step {s.id} ({s.label}) and its folder?\nThe folder goes to the Recycle Bin."
        if later:
            text += f"\n\nIt is used as input by {len(later)} later step(s): {', '.join(later)}."
        if QMessageBox.question(self, "Delete step", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.runs.delete_step(sid)
        except RunError as e:
            QMessageBox.warning(self, "Delete step", str(e))

    # ---- menus
    def _header_menu(self, pos) -> None:
        menu = QMenu(self)
        for i, (title, _) in enumerate(COLUMNS):
            act = menu.addAction(title)
            act.setCheckable(True)
            act.setChecked(not self.tree.isColumnHidden(i))
            act.toggled.connect(lambda on, i=i: self.tree.setColumnHidden(i, not on))
        menu.exec(self.tree.header().mapToGlobal(pos))

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        s = self._steps().get(item.data(0, Qt.ItemDataRole.UserRole)) if item else None
        if not s:
            return
        menu = QMenu(self)
        menu.addAction("Use as input in the open tool", lambda: self.use_as_input.emit(s.id))
        again = menu.addAction("Re-run with the same parameters…", lambda: self.rerun_requested.emit(s.id))
        again.setEnabled(bool(s.params) and s.status != RUNNING and not self._registry().ws.read_only)
        menu.addAction("Show lineage (ancestors and descendants)", lambda: self.set_focus(s.id))
        menu.addSeparator()
        for text, rel in (("Open report", s.html_report), ("Open data folder", s.data_folder),
                          ("Open reports folder", s.report_folder)):
            if rel:
                menu.addAction(text, lambda rel=rel: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(self._registry().ws.root / rel))))
        if s.folder:
            menu.addAction("Open step folder", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._registry().step_dir(s)))))
        menu.addAction("Copy ID", lambda: QGuiApplication.clipboard().setText(s.id))
        if s.status == RUNNING:
            menu.addSeparator()
            menu.addAction("Cancel step", lambda: self.runs.cancel(s.id))
        elif s.folder and not self._registry().ws.read_only:
            menu.addSeparator()
            menu.addAction("Delete step…", lambda: self.delete_step(s.id))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

