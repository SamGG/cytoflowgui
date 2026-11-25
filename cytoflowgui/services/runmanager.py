"""Runs of R steps: several can run at the same time. Owns the processes, the console log of each
step and the registry updates (once at Run, once at the end, whatever happens in R)."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .. import API_VERSION
from ..manifest import Manifest
from .lineage import resolve_inputs
from .runner import RScriptRunner
from .steps import CANCELLED, FAILED, OK, StepError, StepRegistry, Step, iso_utc
from .workspace import REGISTRY_FILE, Workspace

PARAMS_FILE, LOG_FILE = "run_parameters.json", "console.log"
HELPERS_DIR = Path(__file__).resolve().parents[1] / "rhelpers"      # helpers.R, footer.Rmd


class RunError(Exception):
    pass


class _ActiveRun:
    def __init__(self, step: Step, runner: RScriptRunner, log):
        self.step, self.runner, self.log, self.cancelled = step, runner, log, False


class RunManager(QObject):
    run_started = Signal(object)            # Step
    run_output = Signal(str, str)           # step id, text
    run_finished = Signal(object)           # Step, final state
    changed = Signal()                      # set of running steps or registry changed
    registry_state = Signal(bool, str)      # steps.tsv up to date?, message
    reconciled = Signal(object)             # report of the check made when a workspace is opened

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registry: StepRegistry | None = None
        self._runs: dict[str, _ActiveRun] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    # ---- workspace
    def set_workspace(self, ws: Workspace | None) -> None:
        self.flush_now()
        self.registry = StepRegistry(ws) if ws else None
        if self.registry and not ws.read_only:
            report = self.registry.reconcile(set())
            if report["interrupted"] or report["missing"]:
                self._schedule_flush()
                self.reconciled.emit(report)
        self.changed.emit()

    def repair(self) -> dict:
        if not self.registry or self.registry.ws.read_only:
            raise RunError("Open a writable workspace first")
        if self._runs:
            raise RunError("Wait for the running steps to finish")
        report = self.registry.repair(set())
        self.flush_now()
        self.changed.emit()
        return report

    def delete_step(self, step_id: str) -> None:
        if not self.registry or self.registry.ws.read_only:
            raise RunError("Open a writable workspace first")
        if step_id in self._runs:
            raise RunError("A running step cannot be deleted")
        try:
            self.registry.delete(step_id)
        except (StepError, OSError) as e:
            raise RunError(str(e)) from e
        self._schedule_flush()
        self.changed.emit()

    def running(self) -> list[Step]:
        return [r.step for r in self._runs.values()]

    # ---- starting
    def start(self, rscript: str, manifest: Manifest, params: dict, label: str = "",
              comment: str = "", rerun_of: str = "") -> Step:
        reg = self.registry
        if reg is None:
            raise RunError("Open or create a workspace first")
        if reg.ws.read_only:
            raise RunError("This workspace is open read-only")
        refs = resolve_inputs(reg.ws.root, reg, manifest, params)
        parents = list(dict.fromkeys(r.step for r in refs if r.step))      # steps used as input
        try:
            step = reg.create(manifest.id, manifest.version, label, comment, dict(params),
                              rerun_of=rerun_of, inputs=parents, input_files=[r.to_json() for r in refs])
        except StepError as e:
            raise RunError(str(e)) from e
        d = reg.step_dir(step)
        try:
            cfg = dict(params)
            cfg.update(                      # what the plugin's parameters are completed with
                api_version=API_VERSION, plugin_id=manifest.id, plugin_version=manifest.version,
                step_id=step.id, label=step.label, comment=step.comment,
                workspace_dir=str(reg.ws.root), step_dir=str(d),
                data_dir=str(d / "data"), reports_dir=str(d / "reports"), helpers_dir=str(HELPERS_DIR),
                input_step_ids=parents,
                input_steps={r.param: r.step for r in refs})
            pfile = d / PARAMS_FILE
            pfile.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            log = open(d / LOG_FILE, "a", encoding="utf-8", buffering=1)
        except OSError as e:
            reg.finish(step, -1, FAILED)
            self._schedule_flush()
            raise RunError(f"Cannot prepare the step: {e}") from e

        runner = RScriptRunner(self)
        run = _ActiveRun(step, runner, log)
        runner.output.connect(lambda text, sid=step.id: self._on_output(sid, text))
        runner.finished.connect(lambda code, sid=step.id: self._on_finished(sid, code))
        self._runs[step.id] = run
        self._schedule_flush()
        self.run_started.emit(step)
        self.changed.emit()
        runner.start(rscript, str((manifest.dir / manifest.rmd).resolve()), str(pfile.resolve()),
                     str(d.resolve()), f"{manifest.id}_report.html")
        return step

    # ---- during and after
    def _on_output(self, sid: str, text: str) -> None:
        run = self._runs.get(sid)
        if run:
            run.log.write(text)
            self.run_output.emit(sid, text)

    def _on_finished(self, sid: str, code: int) -> None:
        run = self._runs.pop(sid, None)
        if run is None:
            return
        status = CANCELLED if run.cancelled else OK if code == 0 else FAILED
        run.log.write(f"\n--- {status}: exit code {code}, {iso_utc()} ---\n")
        run.log.close()
        step = self.registry.finish(run.step, code, status) if self.registry else run.step
        self._schedule_flush()
        self.run_finished.emit(step)
        self.changed.emit()

    def set_comment(self, step_id: str, text: str) -> None:
        """Edit the comment of a step (the label is part of the folder name and stays fixed)."""
        if self.registry and step_id in self.registry.steps and not self.registry.ws.read_only:
            self.registry.set_comment(step_id, text)
            self._schedule_flush()
            self.changed.emit()

    def cancel(self, step_id: str) -> None:
        run = self._runs.get(step_id)
        if run:
            run.cancelled = True
            run.runner.cancel()

    def shutdown(self) -> None:
        """Cancel everything still running and close the books (application exit)."""
        for sid in list(self._runs):
            self.cancel(sid)
        for run in list(self._runs.values()):
            run.runner.wait(5000)
        self.flush_now()

    # ---- steps.tsv (a failure, e.g. file open in Excel, never loses anything: sidecars are saved)
    def _schedule_flush(self, delay: int = 300) -> None:
        if not self._timer.isActive():
            self._timer.start(delay)

    def _flush(self) -> None:
        reg = self.registry
        if reg is None or not reg.dirty:
            return
        try:
            reg.write_tsv()
        except OSError as e:
            self.registry_state.emit(
                False, f"{REGISTRY_FILE} cannot be updated ({e}). Is it open in another program? "
                       "New attempt in 5 seconds.")
            self._timer.start(5000)
        else:
            self.registry_state.emit(True, "")

    def flush_now(self) -> None:
        self._timer.stop()
        if self.registry and self.registry.dirty:
            try:
                self.registry.write_tsv()
                self.registry_state.emit(True, "")
            except OSError as e:
                self.registry_state.emit(False, f"{REGISTRY_FILE} could not be updated: {e}")
