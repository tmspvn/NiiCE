"""Plot: animated GIF maker with live preview, and intensity histograms."""
from __future__ import annotations

import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import io, plotting
from . import theme
from .common import (PathEdit, ToolDialog, app, file_list_with_buttons, hint,
                     run_dialog, show_modal, to_qimage)

Qt = QtCore.Qt
PREVIEW_VOXELS = 2_000_000  # previews of bigger volumes use every k-th voxel
CMAPS = ["grey", "inferno", "viridis", "magma", "plasma", "hot", "jet", "coolwarm", "gray_r"]


# --------------------------------------------------------------------------- GIF
class GifDialog(ToolDialog):
    title = "Make GIF"
    subtitle = ("Animates slices of a 3D image, or time of a 4D image at one slice. "
                "Several images are placed side by side.")
    run_label = "Save GIF"

    def build(self, lay):
        split = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        box, self.flist = file_list_with_buttons(self.files)
        left.addWidget(box)
        form = QtWidgets.QFormLayout()
        o = plotting.GifOptions()
        self.view = QtWidgets.QComboBox()
        self.view.addItems(plotting.VIEWS)
        form.addRow("View", self.view)
        self.slice = QtWidgets.QSpinBox()
        self.slice.setRange(-1, 4096)
        self.slice.setSpecialValueText("middle")
        self.slice.setValue(-1)
        form.addRow("4D: slice", self.slice)
        self.step = QtWidgets.QSpinBox()
        self.step.setRange(1, 50)
        form.addRow("Use every n-th frame", self.step)
        self.fps = QtWidgets.QSpinBox()
        self.fps.setRange(1, plotting.MAX_GIF_FPS)
        self.fps.setValue(o.fps)
        form.addRow("Frames per second", self.fps)
        self.scale = QtWidgets.QDoubleSpinBox()
        self.scale.setRange(0.25, 8)
        self.scale.setSingleStep(0.5)
        self.scale.setValue(o.scale)
        form.addRow("Magnification", self.scale)
        wrow = QtWidgets.QHBoxLayout()
        self.wlo, self.whi = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for s, v in ((self.wlo, o.winsorize[0]), (self.whi, o.winsorize[1])):
            s.setRange(0, 100)
            s.setValue(v)
            wrow.addWidget(s)
        form.addRow("Intensity window (%)", wrow)
        self.cmap = QtWidgets.QComboBox()
        self.cmap.setEditable(True)
        self.cmap.addItems(CMAPS)
        form.addRow("Colormap", self.cmap)
        self.rotate = QtWidgets.QComboBox()
        self.rotate.addItems(["0", "90", "180", "270"])
        form.addRow("Rotate (deg)", self.rotate)
        self.flip = QtWidgets.QComboBox()
        self.flip.addItems(["none", "horizontal", "vertical"])
        form.addRow("Flip", self.flip)
        self.layout_ = QtWidgets.QComboBox()
        self.layout_.addItems(["horizontal", "vertical"])
        form.addRow("Several images", self.layout_)
        self.xhair = QtWidgets.QLineEdit()
        self.xhair.setPlaceholderText("voxel x, y, z (optional)")
        form.addRow("Crosshair", self.xhair)
        checks = QtWidgets.QGridLayout()
        self.reorient = QtWidgets.QCheckBox("Upright (reorient to RAS)")
        self.crop = QtWidgets.QCheckBox("Crop empty border")
        self.rewind = QtWidgets.QCheckBox("Rewind (play back and forth)")
        self.timebar = QtWidgets.QCheckBox("Time bar")
        self.frame_norm = QtWidgets.QCheckBox("Normalise each frame")
        for i, (c, v) in enumerate(((self.reorient, o.reorient), (self.crop, o.crop), (self.rewind, o.rewind),
                                    (self.timebar, o.timebar), (self.frame_norm, o.frame_norm))):
            c.setChecked(v)
            checks.addWidget(c, i // 2, i % 2)
        form.addRow(checks)
        first = self.files[0] if self.files else "image.nii.gz"
        self.out = PathEdit(io.unique_path(io.split_ext(first)[0] + "_NiiCE.gif"), save=True, filter="GIF (*.gif)")
        form.addRow("Save as", self.out)
        left.addLayout(form)
        split.addLayout(left, 3)

        right = QtWidgets.QVBoxLayout()
        self.preview = QtWidgets.QLabel("Preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(340, 340)
        self.preview.setStyleSheet("background: black; color: grey; border-radius: 6px;")
        right.addWidget(self.preview, 1)
        self.preview_info = hint("")
        right.addWidget(self.preview_info)
        split.addLayout(right, 2)
        lay.addLayout(split)

        self.frames = None
        self.frame_i = 0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.debounce = QtCore.QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(350)
        self.debounce.timeout.connect(self._render_preview)
        for w in (self.view, self.rotate, self.flip, self.layout_, self.cmap):
            w.currentIndexChanged.connect(self.debounce.start)
        self.cmap.editTextChanged.connect(self.debounce.start)
        for w in (self.slice, self.step, self.scale, self.wlo, self.whi, self.fps):
            w.valueChanged.connect(self.debounce.start)
        for c in (self.reorient, self.crop, self.rewind, self.timebar, self.frame_norm):
            c.toggled.connect(self.debounce.start)
        self.xhair.editingFinished.connect(self.debounce.start)
        self.flist.changed.connect(self.debounce.start)
        self.resize(980, 660)
        self.debounce.start()

    def options(self, preview=False) -> plotting.GifOptions:
        xh = None
        if self.xhair.text().strip():
            parts = [p for p in self.xhair.text().replace(";", ",").replace(" ", ",").split(",") if p]
            xh = [int(float(p)) for p in parts]
            if len(xh) != 3:
                raise ValueError("Crosshair needs three voxel coordinates x, y, z")
        cmap = self.cmap.currentText().strip()
        return plotting.GifOptions(
            view=self.view.currentText(), reorient=self.reorient.isChecked(),
            slice_index=None if self.slice.value() < 0 else self.slice.value(),
            rotate=self.rotate.currentIndex(), flip=self.flip.currentText(),
            rewind=self.rewind.isChecked(), winsorize=(self.wlo.value(), self.whi.value()),
            frame_norm=self.frame_norm.isChecked(), timebar=self.timebar.isChecked(), crosshair=xh,
            scale=1.0 if preview else self.scale.value(), cmap=None if cmap in ("", "grey") else cmap,
            crop=self.crop.isChecked(), fps=self.fps.value(),
            step=max(self.step.value(), 2) if preview and self.step.value() == 1 else self.step.value(),
            max_voxels=PREVIEW_VOXELS if preview else None,
            layout=self.layout_.currentText())

    def _render_preview(self):
        paths = self.flist.paths()
        if not paths:
            return
        # Big images are previewed from a reduced copy (every k-th voxel); the saved GIF uses
        # the full image.
        reduced = any(io.voxel_counts(p)[0] > PREVIEW_VOXELS for p in paths)
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.frames = plotting.render_frames(paths, self.options(preview=True))
            n = len(self.frames)
            self.preview_info.setText(f"Preview: {n} frames (every 2nd shown, magnification 1"
                                      + (", reduced resolution" if reduced else "") + "). "
                                      f"Final: about {self._final_frames(n)} frames.")
            self.frame_i = 0
            self.timer.start(int(1000 / self.fps.value()))
        except Exception as e:
            self.timer.stop()
            self.frames = None
            self.preview.setText(f"Preview failed:\n{e}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _final_frames(self, n_preview):
        return n_preview * (2 if self.step.value() == 1 else 1)

    def _tick(self):
        if self.frames is None or not len(self.frames):
            return
        f = self.frames[self.frame_i % len(self.frames)]
        pm = QtGui.QPixmap.fromImage(to_qimage(f))
        self.preview.setPixmap(pm.scaled(self.preview.size() - QtCore.QSize(8, 8), Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        self.frame_i += 1

    def validate(self):
        if not self.flist.count():
            return "Add at least one image"
        try:
            self.options()
        except ValueError as e:
            return str(e)
        if not self.out.path():
            return "Choose where to save the GIF"
        if not self.out.path().lower().endswith(".gif"):
            self.out.setPath(self.out.path() + ".gif")
        return None

    def task(self, report):
        return [plotting.make_gif(self.flist.paths(), self.out.path(), self.options(), report.progress)]


# --------------------------------------------------------------------------- histogram
class HistogramWindow(QtWidgets.QDialog):
    def __init__(self, files, parent=None):
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure

        self.files = list(files)
        self.setWindowTitle("NiiCE - Histogram")
        self.resize(900, 680)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Histogram", objectName="title"))

        controls = QtWidgets.QHBoxLayout()
        # Bins: automatic (Freedman-Diaconis rule) or a number typed by hand
        self.bins_auto = QtWidgets.QRadioButton("Auto bins")
        self.bins_auto.setChecked(True)
        self.bins_auto.setToolTip("Freedman-Diaconis rule: bin width = 2 x IQR / N^(1/3), "
                                  "IQR = inter-quartile range, N = number of voxels")
        self.bins_manual = QtWidgets.QRadioButton("Manual:")
        self.bins = QtWidgets.QSpinBox()
        self.bins.setRange(2, 5000)
        self.bins.setValue(100)
        self.bins.setEnabled(False)
        self.bins_used = QtWidgets.QLabel("", objectName="hint")
        self.bins_manual.toggled.connect(self.bins.setEnabled)
        self.zeros = QtWidgets.QCheckBox("Ignore zeros")
        self.zeros.setChecked(True)
        self.logy = QtWidgets.QCheckBox("Log counts")
        self.density = QtWidgets.QCheckBox("Normalise (density)")
        self.density.setChecked(len(self.files) > 1)
        self.lo, self.hi = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for s, v in ((self.lo, 0), (self.hi, 100)):
            s.setRange(0, 100)
            s.setValue(v)
            s.setSuffix(" %")
        self.mask = QtWidgets.QComboBox()
        self.mask.addItem("no mask", "")
        for f in self.files:
            self.mask.addItem("mask: " + os.path.basename(f), f)
        self.mask.addItem("choose mask...", "__browse__")
        for w in (self.bins_auto, self.bins_manual, self.bins, self.bins_used, self.zeros, self.logy,
                  self.density, QtWidgets.QLabel("range"), self.lo, self.hi, self.mask):
            controls.addWidget(w)
        controls.addStretch(1)
        lay.addLayout(controls)

        self.fig = Figure(figsize=(7, 4), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay.addWidget(NavigationToolbar2QT(self.canvas, self))
        lay.addWidget(self.canvas, 1)
        self.stats = QtWidgets.QTableWidget()
        self.stats.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.stats.setMaximumHeight(40 + 30 * len(self.files))
        lay.addWidget(self.stats)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(hint("Use the toolbar to zoom or save the figure (PNG, PDF, SVG)."), 1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

        self._cache = {}
        for w in (self.bins, self.lo, self.hi):
            w.valueChanged.connect(self.redraw)
        for w in (self.zeros, self.logy, self.density, self.bins_auto):
            w.toggled.connect(self.redraw)
        self.mask.currentIndexChanged.connect(self._mask_changed)
        self.redraw()

    def _mask_changed(self, i):
        if self.mask.itemData(i) == "__browse__":
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Mask image", os.path.dirname(self.files[0]),
                                                         "NIfTI (*.nii *.nii.gz)")
            if p:
                self.mask.insertItem(i, "mask: " + os.path.basename(p), p)
                self.mask.setCurrentIndex(i)
            else:
                self.mask.setCurrentIndex(0)
            return
        self.redraw()

    def _values(self, f, mask, zeros):
        key = (f, mask, zeros)
        if key not in self._cache:
            self._cache[key] = plotting.histogram_values(f, mask or None, zeros)
        return self._cache[key]

    def redraw(self, *_):
        import matplotlib
        from . import theme
        style = theme.matplotlib_style()
        with matplotlib.rc_context(style):
            self.fig.set_facecolor(style.get("figure.facecolor", "white"))
            self._redraw()

    def _redraw(self):
        mask = self.mask.currentData() or ""
        if mask == "__browse__":
            return
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        rows = []
        targets = [f for f in self.files if f != mask]
        try:
            data = [self._values(f, mask, self.zeros.isChecked()) for f in targets]
        except Exception as e:
            ax.text(0.5, 0.5, str(e), ha="center", va="center", wrap=True, transform=ax.transAxes)
            self.canvas.draw_idle()
            return
        allv = np.concatenate([d for d in data if d.size]) if any(d.size for d in data) else np.array([0.0])
        lo, hi = np.percentile(allv, [self.lo.value(), self.hi.value()])
        if hi <= lo:
            hi = lo + 1
        bins = "auto" if self.bins_auto.isChecked() else self.bins.value()
        if bins == "auto":
            # Freedman-Diaconis rule on the shown range, capped so huge images stay responsive
            edges = np.histogram_bin_edges(allv[(allv >= lo) & (allv <= hi)][:2_000_000], bins="fd")
            bins = int(np.clip(len(edges) - 1, 10, 512))
            self.bins_used.setText(f"({bins})")
        else:
            self.bins_used.setText("")
        for f, d in zip(targets, data):
            d = d[(d >= lo) & (d <= hi)]
            colour = {"color": theme.accent()} if len(targets) == 1 else {}
            ax.hist(d, bins=bins, range=(lo, hi), histtype="stepfilled" if len(targets) == 1 else "step", **colour,
                    alpha=0.85 if len(targets) == 1 else 1, linewidth=1.5, density=self.density.isChecked(),
                    label=os.path.basename(f))
            rows.append((os.path.basename(f), plotting.describe(d)))
        ax.set_xlabel("intensity")
        ax.set_ylabel("density" if self.density.isChecked() else "voxels")
        if self.logy.isChecked():
            ax.set_yscale("log")
        if len(targets) > 1:
            ax.legend(fontsize=8)
        title = "within mask " + os.path.basename(mask) if mask else ""
        ax.set_title(title, fontsize=9)
        ax.grid(alpha=0.25)
        self.canvas.draw_idle()
        self._fill_stats(rows)

    def _fill_stats(self, rows):
        keys = ["N", "Mean", "SD", "Min", "P2", "Q1", "Median", "Q3", "P98", "Max"]
        t = self.stats
        t.setColumnCount(len(keys) + 1)
        t.setRowCount(len(rows))
        t.setHorizontalHeaderLabels(["image"] + keys)
        for r, (name, st) in enumerate(rows):
            t.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
            for c, k in enumerate(keys, 1):
                v = st.get(k, "")
                t.setItem(r, c, QtWidgets.QTableWidgetItem(f"{v:.4g}" if isinstance(v, (float, np.floating)) else str(v)))
        t.resizeColumnsToContents()


def gif(files):
    return run_dialog(GifDialog, files)


def histogram(files):
    app()
    return show_modal(HistogramWindow(files))
