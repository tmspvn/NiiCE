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
QLabel#ok { color: @ok; }
QLabel#error { color: @error; }
QFrame#card { border: 1px solid palette(mid); border-radius: 8px; }
QLabel#chip { border-radius: 6px; padding: 6px 10px; font-weight: 600; }
QLabel#chip[active="true"] { background: @accent; color: white; }
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
        apply_theme(inst)
    return inst


def apply_theme(inst=None, theme_name=None, accent=None):
    """Apply the theme from Settings (or the given one) to the running app."""
    from . import theme
    inst = inst or QtWidgets.QApplication.instance()
    s = settings.load()
    theme_name = theme_name or s.get("theme", "system")
    accent = accent or s.get("accent", theme.DEFAULT_ACCENT)
    # first pass sets the palette so ok/error colours can follow light/dark
    theme.apply(inst, theme_name, accent, "")
    css = STYLE.replace("@ok", theme.ok_color()).replace("@error", theme.error_color())
    theme.apply(inst, theme_name, accent, css)
    inst.setWindowIcon(icon())


def icon() -> QtGui.QIcon:
    pm = QtGui.QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    from . import theme
    p.setBrush(QtGui.QColor(theme.accent()))
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
        self.run_btn.clicked.connect(self._run_or_cancel)
        # Plain Enter must not run or close the dialog (it is used inside text fields);
        # Ctrl+Enter runs (see ToolDialog.run_and_close).
        for b in (self.run_btn, self.close_btn, self.open_btn, self.open_file_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
        row.addWidget(self.log_btn)
        row.addStretch(1)
        self.shortcut_hint = QtWidgets.QLabel("Ctrl+Enter: run and close", objectName="hint")
        row.addWidget(self.shortcut_hint)
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
        from . import theme
        self.status.setStyleSheet(f"color: {theme.ok_color() if ok else theme.error_color()};")
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
        self.auto_close = False
        self.quick_result = None  # (message, outputs) shown in a pop-up after a Ctrl+Enter run
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QtGui.QShortcut(QtGui.QKeySequence(keys), self, activated=self.run_and_close)
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
        return saved_text(list(result or []))

    # -- helpers
    def out_name(self, src: str, suffix: str) -> str:
        return io.out_path(src, suffix, ext=self.settings.get("output_ext", "same"))

    def run_and_close(self):
        """Ctrl+Enter: run with the current settings; on success close and show the result."""
        if self.panel.running:
            return
        self.auto_close = True
        self.start()

    def start(self):
        err = self.validate()
        if err:
            self.auto_close = False
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
        if self.auto_close:
            self.quick_result = (self.done_message(result), outputs)
            self.accept()

    def _failed(self, msg, tb):
        self.auto_close = False  # keep the window open so the error can be read
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
        self.setToolTip("Drag to reorder; Delete removes the selected files")
        self.fit_height()

    def fit_height(self):
        """Exactly as tall as its rows (2 to 8 rows visible), so dialogs have no empty space."""
        row = self.sizeHintForRow(0) if self.count() else self.fontMetrics().height() + 6
        rows = min(max(self.count(), 2), 8)
        h = row * rows + 2 * self.frameWidth() + 4
        self.setFixedHeight(h)

    def add(self, path: str):
        it = QtWidgets.QListWidgetItem(os.path.basename(path))
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        self.addItem(it)
        self._relabel()
        if hasattr(self, "fit_height"):
            self.fit_height()

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
    """Compact image list: the list with small Add / Remove buttons beside it."""
    box = QtWidgets.QGroupBox(title)
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(6, 4, 6, 6)
    fl = FileList(files, letters=letters)
    lay.addWidget(fl, 1)
    col = QtWidgets.QVBoxLayout()
    col.setSpacing(2)
    add = QtWidgets.QToolButton(text="+")
    add.setToolTip("Add images")
    rem = QtWidgets.QToolButton(text="\u2212")
    rem.setToolTip("Remove the selected images")
    for b in (add, rem):
        b.setFixedWidth(28)
        col.addWidget(b)
    col.addStretch(1)
    lay.addLayout(col)

    def on_add():
        start = os.path.dirname(fl.paths()[0]) if fl.count() else ""
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(box, "Add images", start, NIFTI_FILTER)
        for p in paths:
            fl.add(p)

    def on_rem():
        for it in fl.selectedItems():
            fl.takeItem(fl.row(it))
        fl._relabel()
        fl.fit_height()

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


BANNER_COLOURS = {  # background, border
    "same": ("rgba(46,125,50,0.12)", "#2e7d32"),
    "ok": ("rgba(46,125,50,0.12)", "#2e7d32"),
    "grid": ("rgba(21,101,192,0.12)", "#1565c0"),
    "info": ("rgba(21,101,192,0.12)", "#1565c0"),
    "warning": ("rgba(255,160,0,0.18)", "#ef6c00"),
}


class Banner(QtWidgets.QLabel):
    """Coloured message box: level 'ok'/'same' green, 'info'/'grid' blue, 'warning' amber."""

    def __init__(self, level: str = "info", title: str = "", lines=(), parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.set(level, title, lines)

    def set(self, level: str, title: str, lines=()):
        bg, border = BANNER_COLOURS.get(level, BANNER_COLOURS["info"])
        self.setStyleSheet(f"QLabel {{ background: {bg}; border: 1px solid {border}; "
                           "border-radius: 6px; padding: 6px 8px; }")
        icon = "&#9888; " if level == "warning" else ""
        body = "".join(f"<br>&bull; {line}" for line in lines)
        self.setText(f"<b>{icon}{title}</b>{body}")
        self.setVisible(bool(title))
        if title:
            QtCore.QTimer.singleShot(0, self._grow_window)

    def _grow_window(self):
        """Make the window taller if the message no longer fits (never shrinks it)."""
        w = self.window()
        if w is not None and w.isVisible():
            need = w.sizeHint().height()
            if w.height() < need:
                w.resize(w.width(), need)


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
    """Three middle slices (sagittal, coronal, axial) of the first volume, uint8, RAS-upright.

    Only every k-th voxel is read (k chosen so the longest axis has ~2x `size` voxels), which
    keeps memory and time low for large images.
    """
    import nibabel as nib
    from scipy.ndimage import zoom
    data, aff = io.load_strided(path_or_img, max_dim=2 * size)
    img = nib.as_closest_canonical(nib.Nifti1Image(data, aff))
    vol = np.asanyarray(img.dataobj)
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


def preview_limit() -> float:
    return float(settings.get("preview_max_mvox") or 0)


class Thumbs(QtWidgets.QWidget):
    """Row of three orthogonal thumbnails for an image (skipped for very large images)."""

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
            lbl.setWordWrap(True)
            lbl.setStyleSheet("background: black; color: #aaa; border-radius: 4px; font-size: 8pt;")
            lay.addWidget(lbl)
            self.labels.append(lbl)
        if path:
            self.set_path(path)

    def _placeholder(self, text: str, tip: str):
        for lbl in self.labels:
            lbl.clear()
        self.labels[0].setText(text)
        self.setToolTip(tip)

    def set_path(self, path: str):
        try:
            reason = io.preview_skip_reason(path, preview_limit())
            if reason:
                self._placeholder("too large\nfor preview", reason)
                return
            for lbl, arr in zip(self.labels, ortho_slices(path, self.size_px)):
                lbl.setPixmap(QtGui.QPixmap.fromImage(to_qimage(arr)))
            self.setToolTip("")
        except Exception as exc:  # unreadable image: show why instead of crashing
            self._placeholder("?", str(exc))


def saved_text(paths, limit: int = 6) -> str:
    """'Saved:' followed by the full path of each output (at most `limit`, then a count)."""
    if not paths:
        return "Done"
    shown = "\n".join(paths[:limit])
    more = f"\n... and {len(paths) - limit} more" if len(paths) > limit else ""
    return f"Saved:\n{shown}{more}"


class Toast(QtWidgets.QWidget):
    """Small window in the screen corner: 'Running...' while working, then the saved paths.

    On success it closes by itself after a few seconds (hovering keeps it open); on error it
    stays open with an explanation and the log.
    """
    finished = QtCore.Signal()

    def __init__(self, title: str, seconds: float = 6.0, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setWindowTitle(f"NiiCE - {title}")
        self.setMinimumWidth(460)
        self.seconds = seconds
        lay = QtWidgets.QVBoxLayout(self)
        self.title = QtWidgets.QLabel(f"<b>{title}</b>")
        self.text = QtWidgets.QLabel("Running...")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 0)  # busy until done
        self.bar.setTextVisible(False)
        self.log = QtWidgets.QPlainTextEdit(objectName="log")
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMinimumHeight(160)
        self.buttons = QtWidgets.QHBoxLayout()
        self.buttons.addStretch(1)
        self.open_btn = QtWidgets.QPushButton("Open folder")
        self.open_btn.setVisible(False)
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        self.buttons.addWidget(self.open_btn)
        self.buttons.addWidget(self.close_btn)
        for w in (self.title, self.text, self.bar, self.log):
            lay.addWidget(w)
        lay.addLayout(self.buttons)
        self._timer = QtCore.QTimer(self, singleShot=True, timeout=self.close)
        self._folder = None
        self.open_btn.clicked.connect(lambda: self._folder and open_in_file_manager(self._folder))

    def add_button(self, label: str, callback) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(label)
        b.clicked.connect(callback)
        self.buttons.insertWidget(1, b)
        return b

    def show_corner(self):
        self.adjustSize()
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.move(g.right() - self.width() - 24, g.bottom() - self.height() - 48)
        self.show()
        self.raise_()

    def running(self, message: str):
        self.text.setText(message)

    def success(self, message: str, outputs=()):
        from . import theme
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.text.setText(message)
        self.text.setStyleSheet(f"color: {theme.ok_color()};")
        if outputs:
            self._folder = os.path.dirname(os.path.abspath(outputs[0]))
            self.open_btn.setVisible(True)
        self.adjustSize()
        if self.seconds > 0:
            self._timer.start(int(self.seconds * 1000))

    def failure(self, message: str, details: str = ""):
        from . import theme
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.text.setText(message)
        self.text.setStyleSheet(f"color: {theme.error_color()};")
        if details:
            self.log.setPlainText(details)
            self.log.setVisible(True)
        self.adjustSize()

    def enterEvent(self, ev):  # keep it open while the mouse is over it
        self._timer.stop()
        super().enterEvent(ev)

    def closeEvent(self, ev):
        self._timer.stop()
        self.finished.emit()
        super().closeEvent(ev)


def _popup_seconds() -> float:
    try:
        return float(settings.get("popup_seconds"))
    except (TypeError, ValueError):
        return 6.0


def wait_for(toast: Toast) -> None:
    """Block (with a running event loop) until the toast is closed, then delete it."""
    import shiboken6
    loop = QtCore.QEventLoop()
    toast.finished.connect(loop.quit)
    loop.exec()
    if shiboken6.isValid(toast):
        shiboken6.delete(toast)


def show_result(title: str, message: str, outputs=()) -> None:
    """Pop-up with a finished result (used after a Ctrl+Enter run closes its window)."""
    app()
    t = Toast(title, _popup_seconds())
    t.success(message, outputs)
    t.show_corner()
    wait_for(t)


def explain_error(msg: str) -> str:
    """Add a plain-language explanation to common error messages."""
    low = msg.lower()
    if "shape" in low and ("differ" in low or "broadcast" in low):
        return msg + "\nThe images have different dimensions: resample one onto the other first."
    if "no such file" in low or "not found" in low:
        return msg + "\nCheck that the file still exists and that its name was not changed."
    if "permission denied" in low:
        return msg + "\nNiiCE cannot write in that folder: copy the images somewhere you own."
    return msg


def run_quick(title: str, task, running: str = "Running...", buttons=()) -> Optional[object]:
    """Run task(report) at once, without a dialog, and show the outcome in a corner pop-up.

    buttons: (label, callback(toast)) pairs added to the pop-up, e.g. 'Options...' or 'Swap'.
    Returns the task's result (or None if it failed).
    """
    app()
    toast = Toast(title, _popup_seconds())
    for label, cb in buttons:
        toast.add_button(label, lambda _=False, cb=cb: cb(toast))
    toast.running(running)
    report = Report()
    report.progressed.connect(lambda f, m: m and toast.running(m))
    log_lines: list[str] = []
    report.logged.connect(log_lines.append)
    worker = Worker(task, report)
    outcome = {}

    def ok(result):
        outcome["result"] = result
        outputs = [] if isinstance(result, str) else [r for r in (result or []) if isinstance(r, str)]
        toast.success(result if isinstance(result, str) else saved_text(outputs), outputs)
        if outputs and settings.get("open_folder_when_done"):
            open_in_file_manager(outputs[0])

    def failed(msg, tb):
        toast.failure("Error: " + explain_error(msg), "\n".join(log_lines + [tb]))

    worker.succeeded.connect(ok)
    worker.failed.connect(failed)
    toast.show_corner()
    worker.start()
    wait_for_worker_and_toast(worker, toast)
    return outcome.get("result")


def wait_for_worker_and_toast(worker, toast) -> None:
    wait_for(toast)
    if worker.isRunning():  # closed while running: let the work finish before exiting
        worker.wait()


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
    quick = getattr(window, "quick_result", None)
    title = getattr(window, "title", "NiiCE")
    if shiboken6.isValid(window):
        shiboken6.delete(window)
    if quick:  # Ctrl+Enter run: the window closed itself, show where the results went
        show_result(title, *quick)
    return result


def run_dialog(dialog_cls, files, *args, **kwargs):
    app()
    return show_modal(dialog_cls(files, *args, **kwargs))
