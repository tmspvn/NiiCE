"""Plot > Visualise alignment: are two images in the same space, and how well do they line up?"""
from __future__ import annotations

import html
import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import io, space
from .common import Banner, Report, Worker, app, hint, preview_limit, show_modal, to_qimage

Qt = QtCore.Qt

MODES = [("Colour overlay", "overlay"), ("Checkerboard", "checker"),
         ("Edges of the 2nd image", "contour"), ("Blend (slider)", "blend")]
VIEW_NAMES = ("Sagittal", "Coronal", "Axial")
AXIS_LETTER = "xyz"


class SliceView(QtWidgets.QLabel):
    """Shows one plane; clicking reports the voxel position (col, row) in plane pixels."""
    clicked = QtCore.Signal(int, int, int)  # axis, col, row

    def __init__(self, axis: int, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.setMinimumSize(220, 220)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: black; color: #aaa; border-radius: 6px;")
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._rect = QtCore.QRect()
        self._plane_shape = (1, 1)
        self._image = None
        self._aspect = 1.0

    def set_image(self, rgb: np.ndarray, aspect_hw: float):
        """rgb: (H, W, 3) uint8; aspect_hw: physical height / width of the plane."""
        self._image = to_qimage(rgb)
        self._plane_shape = rgb.shape[:2]
        self._aspect = aspect_hw
        self._redraw()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._redraw()

    def _redraw(self):
        if self._image is None:
            return
        avail_w, avail_h = self.width() - 8, self.height() - 8
        w = avail_w
        h = int(round(w * self._aspect))
        if h > avail_h:
            h = avail_h
            w = int(round(h / self._aspect))
        pm = QtGui.QPixmap.fromImage(self._image).scaled(max(w, 1), max(h, 1), Qt.IgnoreAspectRatio,
                                                         Qt.SmoothTransformation)
        self._rect = QtCore.QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)
        self.setPixmap(pm)

    def mousePressEvent(self, ev):
        p = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        if self._image is None or not self._rect.contains(p):
            return
        rows, cols = self._plane_shape
        col = int((p.x() - self._rect.left()) / self._rect.width() * cols)
        row = int((p.y() - self._rect.top()) / self._rect.height() * rows)
        self.clicked.emit(self.axis, min(col, cols - 1), min(row, rows - 1))

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self.mousePressEvent(ev)


class AlignmentWindow(QtWidgets.QDialog):
    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.files = list(files)
        self.setWindowTitle("NiiCE - Visualise alignment")
        self.resize(1100, 760)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Visualise alignment", objectName="title"))
        lay.addWidget(hint("Shows two images on top of each other in world coordinates (as placed by "
                           "their headers), to check whether they are in the same space."))

        pick = QtWidgets.QHBoxLayout()
        self.ref_box, self.mov_box = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for box in (self.ref_box, self.mov_box):
            for f in self.files:
                box.addItem(os.path.basename(f), f)
        self.mov_box.setCurrentIndex(min(1, len(self.files) - 1))
        swap = QtWidgets.QPushButton("Swap")
        add = QtWidgets.QPushButton("Add image...")
        pick.addWidget(QtWidgets.QLabel("<b style='color:#ad1457'>Reference</b>"))
        pick.addWidget(self.ref_box, 1)
        pick.addWidget(swap)
        pick.addWidget(QtWidgets.QLabel("<b style='color:#2e7d32'>Compared</b>"))
        pick.addWidget(self.mov_box, 1)
        pick.addWidget(add)
        lay.addLayout(pick)

        self.banner = Banner("info", "")
        lay.addWidget(self.banner)

        ctl = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        for label, key in MODES:
            self.mode.addItem(label, key)
        self.alpha = QtWidgets.QSlider(Qt.Horizontal)
        self.alpha.setRange(0, 100)
        self.alpha.setValue(50)
        self.alpha.setMaximumWidth(160)
        self.alpha.setToolTip("Balance: left shows only the reference, right only the compared image, "
                              "middle both")
        self.by = QtWidgets.QComboBox()
        self.by.addItem("World coordinates (use headers)", "world")
        self.by.addItem("Voxel indices (ignore headers)", "voxel")
        self.by.setToolTip("Voxel indices = what voxel-wise tools (Math, Subtract) assume: voxel (i, j, k) "
                           "of one image lies on voxel (i, j, k) of the other.")
        self.fov = QtWidgets.QCheckBox("Field-of-view outlines")
        self.fov.setChecked(True)
        for w in (QtWidgets.QLabel("Display"), self.mode, QtWidgets.QLabel("Reference"), self.alpha,
                  QtWidgets.QLabel("Compared"), QtWidgets.QLabel("   Align by"),
                  self.by, self.fov):
            ctl.addWidget(w)
        ctl.addStretch(1)
        lay.addLayout(ctl)

        views = QtWidgets.QHBoxLayout()
        self.views, self.sliders, self.pos_labels = [], [], []
        for axis in range(3):
            col = QtWidgets.QVBoxLayout()
            col.addWidget(QtWidgets.QLabel(f"<b>{VIEW_NAMES[axis]}</b>"))
            v = SliceView(axis)
            v.clicked.connect(self._clicked)
            sl = QtWidgets.QSlider(Qt.Horizontal)
            sl.valueChanged.connect(self._render)
            lbl = hint("")
            col.addWidget(v, 1)
            col.addWidget(sl)
            col.addWidget(lbl)
            views.addLayout(col, 1)
            self.views.append(v)
            self.sliders.append(sl)
            self.pos_labels.append(lbl)
        lay.addLayout(views, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.legend = hint("")
        bottom.addWidget(self.legend, 1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        self.pair = None
        self._worker = None
        self._pending = False
        swap.clicked.connect(self._swap)
        add.clicked.connect(self._add)
        self.ref_box.currentIndexChanged.connect(self._load)
        self.mov_box.currentIndexChanged.connect(self._load)
        self.by.currentIndexChanged.connect(self._load)
        self.mode.currentIndexChanged.connect(self._render)
        self.alpha.valueChanged.connect(self._render)
        self.fov.toggled.connect(self._render)
        self._load()

    # ------------------------------------------------------------------ data
    def _swap(self):
        r, m = self.ref_box.currentIndex(), self.mov_box.currentIndex()
        for box, i in ((self.ref_box, m), (self.mov_box, r)):
            box.blockSignals(True)
            box.setCurrentIndex(i)
            box.blockSignals(False)
        self._load()

    def _add(self):
        start = os.path.dirname(self.files[0]) if self.files else ""
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add image", start, "NIfTI (*.nii *.nii.gz)")
        if p:
            self.files.append(p)
            for box in (self.ref_box, self.mov_box):
                box.addItem(os.path.basename(p), p)
            self.mov_box.setCurrentIndex(self.mov_box.count() - 1)

    def _load(self, *_):
        ref, mov = self.ref_box.currentData(), self.mov_box.currentData()
        if not ref or not mov:
            return
        if self._worker and self._worker.isRunning():
            self._pending = True  # reload when the current computation ends
            return
        self._update_banner(ref, mov)
        for v in self.views:
            v._image = None
            v.clear()
            v.setText("computing...")
        by = self.by.currentData()
        for path in (ref, mov):
            big = io.preview_skip_reason(path, preview_limit())
            if big:  # do not load: it would be slow and use a lot of memory
                self.pair = None
                for v in self.views:
                    v.setText("too big to load")
                self.legend.setText(f"{os.path.basename(path)} is too big to load here: "
                                    + big.replace("preview skipped: ", "") + ". Raise the limit in "
                                    "Settings > General > Skip previews above.")
                return
        self._worker = Worker(lambda r: space.resample_pair(ref, mov, by=by, max_dim=192), Report())
        self._worker.succeeded.connect(self._loaded)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _update_banner(self, ref, mov):
        if ref == mov:
            self.banner.set("info", "Reference and compared image are the same file.")
            return
        r = space.compare(io.load(ref), io.load(mov))
        details = [html.escape(r.summary)] + [html.escape(i) for i in r.issues]
        if not r.same_grid:
            details.append(f"Field-of-view overlap: {100 * r.overlap:.0f}% of the smaller image")
        title = {"same": "Same voxel grid", "grid": "Same world space, different voxel grid",
                 "warning": "Not in the same affine space"}[r.level]
        self.banner.set(r.level, title)  # just the verdict; details on hover
        self.banner.setToolTip("<br>".join(details))

    def _failed(self, msg, tb):
        for v in self.views:
            v.setText(f"failed:\n{msg}")
        self._after_worker()

    def _loaded(self, pair):
        self.pair = pair
        shape = pair["ref"].shape
        # start at the centre of the reference image
        ref = io.load(self.ref_box.currentData())
        c = np.linalg.inv(pair["affine"]) @ np.r_[space.centre(ref.shape, ref.affine), 1]
        for axis, sl in enumerate(self.sliders):
            sl.blockSignals(True)
            sl.setRange(0, shape[axis] - 1)
            sl.setValue(int(np.clip(round(c[axis]), 0, shape[axis] - 1)))
            sl.blockSignals(False)
        by = self.by.currentData()
        self.legend.setText(
            "Magenta = reference, green = compared image; grey/white where both are bright, so "
            "colour fringes mean misalignment. Outlines: each image's field of view. Click a view to "
            "move the other slices. "
            + ("Placed by the headers (world coordinates)." if by == "world" else
               "Headers ignored: voxel (i, j, k) on voxel (i, j, k)."))
        self._render()
        self._after_worker()

    def _after_worker(self):
        if self._pending:
            self._pending = False
            QtCore.QTimer.singleShot(0, self._load)

    # ------------------------------------------------------------------ drawing
    def _render(self, *_):
        if self.pair is None:
            return
        p = self.pair
        idx = [s.value() for s in self.sliders]
        mode = self.mode.currentData()
        self.alpha.setEnabled(mode != "checker")
        for axis, view in enumerate(self.views):
            r = space.plane(p["ref"], axis, idx[axis])
            m = space.plane(p["mov"], axis, idx[axis])
            fov = self.fov.isChecked()
            rgb = space.composite(r, m, mode, self.alpha.value() / 100, tile=max(4, max(r.shape) // 10),
                                  ref_fov=space.plane(p["ref_fov"], axis, idx[axis]) if fov else None,
                                  mov_fov=space.plane(p["mov_fov"], axis, idx[axis]) if fov else None)
            rgb = (rgb * 255).astype(np.uint8)
            # crosshair at the other two slices
            a, b = [i for i in range(3) if i != axis]
            col, row = idx[a], p["ref"].shape[b] - 1 - idx[b]
            rgb[row, :] = (rgb[row, :] * 0.5 + 127).astype(np.uint8)
            rgb[:, col] = (rgb[:, col] * 0.5 + 127).astype(np.uint8)
            rz, cz = space.plane_zooms(p["zooms"], axis)
            view.set_image(np.ascontiguousarray(rgb), (rgb.shape[0] * rz) / (rgb.shape[1] * cz))
            world = p["affine"] @ np.r_[idx, 1]
            self.pos_labels[axis].setText(f"{AXIS_LETTER[axis]} = {world[axis]:.1f} mm")
        world = p["affine"] @ np.r_[idx, 1]
        self.setWindowTitle(f"NiiCE - Visualise alignment - cursor at ({world[0]:.1f}, {world[1]:.1f}, "
                            f"{world[2]:.1f}) mm")

    def _clicked(self, axis, col, row):
        if self.pair is None:
            return
        a, b = [i for i in range(3) if i != axis]
        n_b = self.pair["ref"].shape[b]
        self.sliders[a].setValue(col)
        self.sliders[b].setValue(n_b - 1 - row)

    def closeEvent(self, ev):
        if self._worker and self._worker.isRunning():
            self._worker.wait()
        super().closeEvent(ev)


def run(files):
    app()
    return show_modal(AlignmentWindow(files))
