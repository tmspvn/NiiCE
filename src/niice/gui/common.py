"""Shared Qt widgets: app bootstrap, background worker, run panel, base dialog, thumbnails."""
from __future__ import annotations

import os
import sys
import threading
import traceback
from typing import Callable, Optional, Sequence

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .. import settings
from ..core import io
from ..core.proc import Cancelled, open_file, open_in_file_manager

Qt = QtCore.Qt

STYLE = """
QDialog, QWidget { font-size: 10pt; }
QLabel#title { font-size: 14pt; font-weight: 600; }
QLabel#subtitle { color: palette(mid); }
QLabel#hint { color: palette(mid); font-size: 9pt; }
QLabel#ok { color: #2e7d32; }
QLabel#error { color: #c62828; }
QFrame#card { border: 1px solid palette(mid); border-radius: 8px; }
QLabel#chip { border-radius: 6px; padding: 6px 10px; font-weight: 600; }
QLabel#chip[active="true"] { background: #1565c0; color: white; }
QLabel#chip[active="false"] { background: palette(window); color: palette(mid);
                              border: 1px dashed palette(mid); }
QPlainTextEdit#log { font-family: monospace; font-size: 9pt; }
"""


def app() -> QtWidgets.QApplication:
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        inst = QtWidgets.QApplication(sys.argv[:1])
        inst.setApplicationName("NiiCE")
        inst.setApplicationDisplayName("NiiCE")
        inst.setWindowIcon(icon())
        inst.setStyleSheet(STYLE)
    return inst


def icon() -> QtGui.QIcon:
    pm = QtGui.QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor("#1565c0"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QtGui.QColor("white"))
    f = QtGui.QFont()
    f.setPixelSize(30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "Nii")
    p.end()
    return QtGui.QIcon(pm)


# --------------------------------------------------------------------------- messages
def message(title: str, text: str, parent=None) -> None:
    app()
    QtWidgets.QMessageBox.information(parent, title, text)


def error(title: str, text: str, details: str = "", parent=None) -> None:
    app()
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Critical, title, text, parent=parent)
    if details:
        box.setDetailedText(details)
    box.exec()


def ask(title: str, text: str, parent=None) -> bool:
    app()
    r = QtWidgets.QMessageBox.question(parent, title, text)
    return r == QtWidgets.QMessageBox.Yes


# --------------------------------------------------------------------------- background work
class Report(QtCore.QObject):
    """Handed to background tasks: report.progress(0.4, 'msg'), report.log('line'), report.cancelled."""
    progressed = QtCore.Signal(float, str)
    logged = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.cancel_event = threading.Event()

    def progress(self, fraction: float, message: str = "") -> None:
        self.progressed.emit(float(fraction), message)

    def busy(self, message: str = "") -> None:
        self.progressed.emit(-1.0, message)

    def log(self, line: str) -> None:
        self.logged.emit(line)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()


class Worker(QtCore.QThread):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    was_cancelled = QtCore.Signal()

    def __init__(self, fn: Callable[[Report], object], report: Report):
        super().__init__()
        self.fn, self.report = fn, report

    def run(self):
        try:
            self.succeeded.emit(self.fn(self.report))
        except Cancelled:
            self.was_cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__, traceback.format_exc())


class RunPanel(QtWidgets.QWidget):
    """Progress bar, status, collapsible log and Run / Cancel / Close buttons."""
    run_clicked = QtCore.Signal()
    cancel_clicked = QtCore.Signal()

    def __init__(self, run_label: str = "Run", show_log: bool = False, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.log = QtWidgets.QPlainTextEdit(objectName="log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(140)
        self.log.setVisible(show_log)
        row = QtWidgets.QHBoxLayout()
        self.log_btn = QtWidgets.QToolButton(text="Show log" if not show_log else "Hide log")
        self.log_btn.clicked.connect(self.toggle_log)
        self.open_btn = QtWidgets.QPushButton("Open folder")
        self.open_btn.setVisible(False)
        self.open_file_btn = QtWidgets.QPushButton("Open file")
        self.open_file_btn.setVisible(False)
        self.close_btn = QtWidgets.QPushButton("Close")
        self.run_btn = QtWidgets.QPushButton(run_label)
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self._run_or_cancel)
        row.addWidget(self.log_btn)
        row.addStretch(1)
        row.addWidget(self.open_file_btn)
        row.addWidget(self.open_btn)
        row.addWidget(self.close_btn)
        row.addWidget(self.run_btn)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(self.log, 1)
        lay.addLayout(row)
        self.run_label = run_label
        self.running = False
        self._folder = None
        self._file = None
        self.open_btn.clicked.connect(lambda: self._folder and open_in_file_manager(self._folder))
        self.open_file_btn.clicked.connect(lambda: self._file and open_file(self._file))

    def toggle_log(self):
        vis = not self.log.isVisible()
        self.log.setVisible(vis)
        self.log_btn.setText("Hide log" if vis else "Show log")
        if vis and self.window().height() < 500:
            self.window().resize(self.window().width(), self.window().height() + 180)

    def _run_or_cancel(self):
        (self.cancel_clicked if self.running else self.run_clicked).emit()

    def set_running(self, running: bool):
        self.running = running
        self.run_btn.setText("Cancel" if running else self.run_label)
        self.close_btn.setEnabled(not running)
        if running:
            self.open_btn.setVisible(False)
            self.open_file_btn.setVisible(False)
            self.set_progress(0, "Starting...")

    def set_progress(self, fraction: float, message: str = ""):
        if fraction < 0:
            self.bar.setRange(0, 0)  # busy indicator
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * min(max(fraction, 0), 1)))
        if message:
            self.status.setText(message)
            self.status.setObjectName("")
            self.status.setStyleSheet("")

    def append_log(self, line: str):
        self.log.appendPlainText(line)

    def finished(self, message: str, outputs: Sequence[str] = (), ok: bool = True):
        self.set_running(False)
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000 if ok else 0)
        self.status.setText(message)
        self.status.setStyleSheet("color: #2e7d32;" if ok else "color: #c62828;")
        if outputs:
            self._folder = os.path.dirname(os.path.abspath(outputs[0]))
            self.open_btn.setVisible(True)
            if len(outputs) == 1 and outputs[0].lower().endswith((".gif", ".png", ".txt", ".details")):
                self._file = outputs[0]
                self.open_file_btn.setVisible(True)


class ToolDialog(QtWidgets.QDialog):
    """Base dialog: header, a body built by `build()`, and a RunPanel running `task()` in a thread.

    Subclasses implement build(layout), optionally validate() -> error string, and
    task(report) -> list of output paths (or a message string).
    """
    title = "NiiCE"
    subtitle = ""
    run_label = "Run"
    show_log = False

    def __init__(self, files: Sequence[str], parent=None):
        super().__init__(parent)
        self.files = list(files)
        self.settings = settings.load()
        self.setWindowTitle(f"NiiCE - {self.title}")
        self.setMinimumWidth(520)
        outer = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel(self.title, objectName="title")
        outer.addWidget(head)
        if self.subtitle:
            sub = QtWidgets.QLabel(self.subtitle, objectName="subtitle")
            sub.setWordWrap(True)
            outer.addWidget(sub)
        self.body_widget = QtWidgets.QWidget()
        self.body = QtWidgets.QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(0, 6, 0, 6)
        outer.addWidget(self.body_widget, 1)
        self.panel = RunPanel(self.run_label, self.show_log)
        outer.addWidget(self.panel)
        self.panel.run_clicked.connect(self.start)
        self.panel.cancel_clicked.connect(self.cancel)
        self.panel.close_btn.clicked.connect(self.close)
        self.worker: Optional[Worker] = None
        self.report: Optional[Report] = None
        self.build(self.body)

    # -- to override
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        pass

    def validate(self) -> Optional[str]:
        return None

    def task(self, report: Report):
        raise NotImplementedError

    def done_message(self, result) -> str:
        if isinstance(result, str):
            return result
        result = list(result or [])
        if len(result) == 1:
            return f"Saved {os.path.basename(result[0])}"
        return f"Saved {len(result)} files"

    # -- helpers
    def out_name(self, src: str, suffix: str) -> str:
        return io.out_path(src, suffix, ext=self.settings.get("output_ext", "same"))

    def start(self):
        err = self.validate()
        if err:
            self.panel.finished(err, ok=False)
            return
        self.report = Report()
        self.report.progressed.connect(self.panel.set_progress)
        self.report.logged.connect(self.panel.append_log)
        self.worker = Worker(self.task, self.report)
        self.worker.succeeded.connect(self._ok)
        self.worker.failed.connect(self._failed)
        self.worker.was_cancelled.connect(lambda: self._end("Cancelled", ok=False))
        self.body_widget.setEnabled(False)
        self.panel.set_running(True)
        self.worker.start()

    def cancel(self):
        if self.report:
            self.report.cancel_event.set()
            self.panel.set_progress(-1, "Cancelling...")

    def _ok(self, result):
        outputs = [] if isinstance(result, str) else [r for r in (result or []) if isinstance(r, str)]
        self._end(self.done_message(result), outputs)
        if outputs and self.settings.get("open_folder_when_done"):
            open_in_file_manager(outputs[0])

    def _failed(self, msg, tb):
        self.panel.append_log(tb)
        self._end(f"Error: {msg}", ok=False)

    def _end(self, msg, outputs=(), ok=True):
        self.body_widget.setEnabled(True)
        self.panel.finished(msg, outputs, ok)

    def closeEvent(self, ev):
        if self.worker and self.worker.isRunning():
            if not ask("Still running", "A task is running. Cancel it and close?", self):
                ev.ignore()
                return
            self.cancel()
            self.worker.wait(5000)
        super().closeEvent(ev)


# --------------------------------------------------------------------------- small widgets
class FileList(QtWidgets.QListWidget):
    """List of files; optional letters (a, b, ...) and drag-and-drop reordering."""
    changed = QtCore.Signal()

    def __init__(self, files: Sequence[str], letters: bool = False, reorder: bool = True,
                 removable: bool = True, parent=None):
        super().__init__(parent)
        self.letters, self.removable = letters, removable
        if reorder:
            self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.model().rowsMoved.connect(lambda *a: self._relabel())
        for f in files:
            self.add(f)
        self.setMaximumHeight(max(70, min(220, 26 * (len(files) + 1))))

    def add(self, path: str):
        it = QtWidgets.QListWidgetItem(os.path.basename(path))
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        self.addItem(it)
        self._relabel()

    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def keyPressEvent(self, ev):
        if self.removable and ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            for it in self.selectedItems():
                self.takeItem(self.row(it))
            self._relabel()
            return
        super().keyPressEvent(ev)

    def _relabel(self):
        for i in range(self.count()):
            it = self.item(i)
            name = os.path.basename(it.data(Qt.UserRole))
            it.setText(f"{chr(97 + i)}  =  {name}" if self.letters and i < 26 else name)
        self.changed.emit()


def file_list_with_buttons(files, letters=False, title="Images") -> tuple[QtWidgets.QGroupBox, FileList]:
    box = QtWidgets.QGroupBox(title)
    lay = QtWidgets.QVBoxLayout(box)
    fl = FileList(files, letters=letters)
    lay.addWidget(fl)
    row = QtWidgets.QHBoxLayout()
    add = QtWidgets.QPushButton("Add...")
    rem = QtWidgets.QPushButton("Remove")
    hint = QtWidgets.QLabel("Drag to reorder" if len(files) > 1 or letters else "", objectName="hint")
    row.addWidget(hint)
    row.addStretch(1)
    row.addWidget(add)
    row.addWidget(rem)
    lay.addLayout(row)

    def on_add():
        start = os.path.dirname(fl.paths()[0]) if fl.count() else ""
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(box, "Add images", start, NIFTI_FILTER)
        for p in paths:
            fl.add(p)

    def on_rem():
        for it in fl.selectedItems():
            fl.takeItem(fl.row(it))
        fl._relabel()

    add.clicked.connect(on_add)
    rem.clicked.connect(on_rem)
    return box, fl


NIFTI_FILTER = "NIfTI (*.nii *.nii.gz);;All files (*)"


class PathEdit(QtWidgets.QWidget):
    """Line edit + Browse button for choosing a file (open or save)."""
    changed = QtCore.Signal(str)

    def __init__(self, path: str = "", save: bool = False, filter: str = NIFTI_FILTER,
                 placeholder: str = "", directory: bool = False, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit(path)
        self.edit.setPlaceholderText(placeholder)
        btn = QtWidgets.QPushButton("Browse...")
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)
        self.save, self.filter, self.directory = save, filter, directory
        btn.clicked.connect(self.browse)
        self.edit.textChanged.connect(self.changed)

    def browse(self):
        start = self.path() or ""
        if self.directory:
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", start)
        elif self.save:
            p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save as", start, self.filter)
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose file", start, self.filter)
        if p:
            self.edit.setText(p)

    def path(self) -> str:
        return self.edit.text().strip()

    def setPath(self, p: str):
        self.edit.setText(p)


def hline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setFrameShadow(QtWidgets.QFrame.Sunken)
    return f


def hint(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text, objectName="hint")
    lbl.setWordWrap(True)
    return lbl


# --------------------------------------------------------------------------- thumbnails
def to_qimage(arr: np.ndarray) -> QtGui.QImage:
    """uint8 (H, W) grey or (H, W, 3) RGB array -> QImage (copied)."""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        return QtGui.QImage(arr.data, w, h, w, QtGui.QImage.Format_Grayscale8).copy()
    return QtGui.QImage(arr.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()


def ortho_slices(path_or_img, size: int = 120) -> list[np.ndarray]:
    """Three middle slices (sagittal, coronal, axial) of the first volume, uint8, RAS-upright."""
    import nibabel as nib
    from scipy.ndimage import zoom
    img = io.load(path_or_img) if isinstance(path_or_img, str) else path_or_img
    img = nib.as_closest_canonical(img)
    obj = img.dataobj
    vol = np.asanyarray(obj[..., 0] if len(img.shape) > 3 else obj).astype(np.float32)
    while vol.ndim > 3:
        vol = vol[..., 0]
    vol = np.nan_to_num(vol)
    nz = vol[vol != 0]
    lo, hi = (np.percentile(nz, [1, 99]) if nz.size else (0, 1))
    vol = np.clip((vol - lo) / max(hi - lo, 1e-6), 0, 1)
    zooms = img.header.get_zooms()[:3]
    out = []
    c = [s // 2 for s in vol.shape]
    for sl, (rz, cz) in ((vol[c[0], :, :], (zooms[2], zooms[1])),
                         (vol[:, c[1], :], (zooms[2], zooms[0])),
                         (vol[:, :, c[2]], (zooms[1], zooms[0]))):
        sl = np.flipud(sl.T)
        f = size / max(sl.shape[0] * rz, sl.shape[1] * cz)
        sl = zoom(sl, (f * rz, f * cz), order=1)
        out.append((np.clip(sl, 0, 1) * 255).astype(np.uint8))
    return out


class Thumbs(QtWidgets.QWidget):
    """Row of three orthogonal thumbnails for an image."""

    def __init__(self, path: str = "", size: int = 110, parent=None):
        super().__init__(parent)
        self.size_px = size
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.labels = []
        for _ in range(3):
            lbl = QtWidgets.QLabel()
            lbl.setFixedSize(size, size)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background: black; border-radius: 4px;")
            lay.addWidget(lbl)
            self.labels.append(lbl)
        if path:
            self.set_path(path)

    def set_path(self, path: str):
        try:
            for lbl, arr in zip(self.labels, ortho_slices(path, self.size_px)):
                lbl.setPixmap(QtGui.QPixmap.fromImage(to_qimage(arr)))
        except Exception as exc:  # unreadable image: show why instead of crashing
            for lbl in self.labels:
                lbl.clear()
            self.labels[0].setText("?")
            self.setToolTip(str(exc))


def show_modal(window: QtWidgets.QDialog) -> int:
    """Show a window until it closes, then delete it.

    Deleting explicitly (before the QApplication is torn down at interpreter exit) avoids a
    PySide crash on shutdown when Python frees the widgets in the wrong order.
    """
    import shiboken6
    window.show()
    window.raise_()
    window.activateWindow()
    result = window.exec()
    if shiboken6.isValid(window):
        shiboken6.delete(window)
    return result


def run_dialog(dialog_cls, files, *args, **kwargs):
    app()
    return show_modal(dialog_cls(files, *args, **kwargs))
