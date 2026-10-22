"""Registration with ANTs: guided registration dialog and apply-transforms dialog."""
from __future__ import annotations

import os
import shlex
import time

import nibabel as nib
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from scipy import ndimage

from ..core import ants, io
from ..core.proc import run_streaming
from .common import (PathEdit, Report, Thumbs, ToolDialog, Worker, error, hint, run_dialog,
                     to_qimage)

Qt = QtCore.Qt


# --------------------------------------------------------------------------- overlap preview
def _first_volume(img):
    data = np.asanyarray(img.dataobj[..., 0] if len(img.shape) > 3 else img.dataobj)
    while data.ndim > 3:
        data = data[..., 0]
    return np.nan_to_num(data.astype(np.float32))


def _norm(x):
    nz = x[x != 0]
    if not nz.size:
        return np.zeros_like(x)
    lo, hi = np.percentile(nz, [1, 99])
    return np.clip((x - lo) / max(hi - lo, 1e-6), 0, 1)


def overlap_image(fixed: str, moving: str, size: int = 150) -> np.ndarray:
    """Three orthogonal slices: fixed in magenta, moving in green, both placed by their headers.

    The moving image is resampled into a coarse copy of the fixed grid using only the
    voxel-to-world matrices (no registration), which is the starting point ANTs sees.
    Where they overlap well the picture looks grey/white; misplacement shows as colour fringes.
    """
    f_img = nib.as_closest_canonical(io.load(fixed))
    m_img = io.load(moving)
    f = _first_volume(f_img)
    factor = max(1, int(np.ceil(max(f.shape) / 128)))
    f = f[::factor, ::factor, ::factor]
    grid = f_img.affine @ np.diag([factor, factor, factor, 1])
    m = np.linalg.inv(m_img.affine) @ grid  # coarse fixed voxel -> moving voxel
    mov = ndimage.affine_transform(_first_volume(m_img), m[:3, :3], offset=m[:3, 3],
                                   output_shape=f.shape, order=1)
    f, mov = _norm(f), _norm(mov)
    zooms = np.array(f_img.header.get_zooms()[:3]) * factor
    c = [s // 2 for s in f.shape]
    panels = []
    for sl_f, sl_m, (rz, cz) in (
        (f[c[0]], mov[c[0]], (zooms[2], zooms[1])),
        (f[:, c[1]], mov[:, c[1]], (zooms[2], zooms[0])),
        (f[:, :, c[2]], mov[:, :, c[2]], (zooms[1], zooms[0])),
    ):
        a, b = np.flipud(sl_f.T), np.flipud(sl_m.T)
        k = size / max(a.shape[0] * rz, a.shape[1] * cz)
        a, b = ndimage.zoom(a, (k * rz, k * cz), order=1), ndimage.zoom(b, (k * rz, k * cz), order=1)
        rgb = np.stack([a, b, a], axis=-1)  # R,B = fixed (magenta), G = moving (green)
        panel = np.zeros((size, size, 3), np.float32)
        y0, x0 = (size - rgb.shape[0]) // 2, (size - rgb.shape[1]) // 2
        panel[y0:y0 + rgb.shape[0], x0:x0 + rgb.shape[1]] = rgb[:size, :size]
        panels.append(panel)
    gap = np.zeros((size, 4, 3), np.float32)
    out = np.concatenate([panels[0], gap, panels[1], gap, panels[2]], axis=1)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


# --------------------------------------------------------------------------- registration dialog
class ImageCard(QtWidgets.QFrame):
    def __init__(self, role: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel(f"<b style='color:{color}'>{role}</b>")
        lay.addWidget(head)
        self.thumbs = Thumbs("", 92)
        lay.addWidget(self.thumbs)
        self.name = QtWidgets.QLabel("")
        self.name.setWordWrap(True)
        self.grid = hint("")
        lay.addWidget(self.name)
        lay.addWidget(self.grid)

    def set_path(self, path: str):
        if not path:
            return
        self.thumbs.set_path(path)
        self.name.setText(os.path.basename(path))
        self.name.setToolTip(path)
        try:
            img = io.load(path)
            self.grid.setText(" x ".join(map(str, img.shape)) + "  |  "
                              + " x ".join(f"{z:.3g}" for z in img.header.get_zooms()[:3]) + " mm  |  "
                              + "".join(nib.aff2axcodes(img.affine)))
        except Exception as e:
            self.grid.setText(str(e))


class RegisterDialog(ToolDialog):
    title = "Registration (ANTs)"
    subtitle = ("Moves the MOVING image onto the FIXED image. The fixed image defines the target "
                "space; it is not changed.")
    run_label = "Start registration"

    def build(self, lay):
        files = self.files + [""] * (2 - len(self.files))
        self.fixed_path, self.moving_path = files[0], files[1]
        self.reg_id = ants.new_id()

        # --- images
        cards = QtWidgets.QHBoxLayout()
        self.moving_card = ImageCard("MOVING  (will be transformed)", "#2e7d32")
        self.fixed_card = ImageCard("FIXED  (target space)", "#ad1457")
        mid = QtWidgets.QVBoxLayout()
        arrow = QtWidgets.QLabel("<span style='font-size:28pt'>&#10230;</span>")
        arrow.setAlignment(Qt.AlignCenter)
        swap = QtWidgets.QPushButton("Swap")
        swap.setToolTip("Exchange fixed and moving")
        swap.clicked.connect(self._swap)
        mid.addStretch(1)
        mid.addWidget(arrow)
        mid.addWidget(swap)
        mid.addStretch(1)
        cards.addWidget(self.moving_card, 1)
        cards.addLayout(mid)
        cards.addWidget(self.fixed_card, 1)
        lay.addLayout(cards)
        pick = QtWidgets.QHBoxLayout()
        self.moving_edit = PathEdit(self.moving_path, placeholder="moving image")
        self.fixed_edit = PathEdit(self.fixed_path, placeholder="fixed image")
        pick.addWidget(self.moving_edit, 1)
        pick.addSpacing(40)
        pick.addWidget(self.fixed_edit, 1)
        lay.addLayout(pick)

        # --- options
        opts = QtWidgets.QHBoxLayout()
        self.type_group = QtWidgets.QButtonGroup(self)
        tbox = QtWidgets.QGroupBox("Transformation")
        tl = QtWidgets.QVBoxLayout(tbox)
        for i, (key, (label, _, _)) in enumerate(ants.TRANSFORMS.items()):
            rb = QtWidgets.QRadioButton(label)
            rb.setProperty("key", key)
            self.type_group.addButton(rb, i)
            tl.addWidget(rb)
        self.type_group.button(2).setChecked(True)
        opts.addWidget(tbox)
        sbox = QtWidgets.QGroupBox("Quality")
        sl = QtWidgets.QFormLayout(sbox)
        self.quick = QtWidgets.QComboBox()
        self.quick.addItems(["Quick (antsRegistrationSyNQuick.sh)", "Thorough (antsRegistrationSyN.sh)"])
        sl.addRow(self.quick)
        self.threads = QtWidgets.QSpinBox()
        self.threads.setRange(1, max(1, os.cpu_count() or 1))
        self.threads.setValue(min(int(self.settings.get("ants_threads", 3)), self.threads.maximum()))
        sl.addRow("CPU threads", self.threads)
        opts.addWidget(sbox)
        obox = QtWidgets.QGroupBox("Output")
        ol = QtWidgets.QFormLayout(obox)
        self.outdir = PathEdit(directory=True)
        ol.addRow("Folder", self.outdir)
        self.id_label = hint("")
        ol.addRow(self.id_label)
        opts.addWidget(obox, 1)
        lay.addLayout(opts)

        # --- infographic
        info = QtWidgets.QFrame(objectName="card")
        il = QtWidgets.QGridLayout(info)
        il.addWidget(QtWidgets.QLabel("<b>What will happen</b>"), 0, 0, 1, 2)
        chips = QtWidgets.QHBoxLayout()
        self.chips = {}
        for i, stage in enumerate(("Rigid", "Affine", "SyN")):
            if i:
                chips.addWidget(QtWidgets.QLabel("\u2192"))
            chip = QtWidgets.QLabel(f"{stage}<br><span style='font-weight:normal;font-size:8pt'>"
                                    f"{ants.STAGE_HELP[stage]}</span>", objectName="chip")
            chip.setAlignment(Qt.AlignCenter)
            chips.addWidget(chip)
            self.chips[stage] = chip
        chips.addStretch(1)
        il.addLayout(chips, 1, 0)
        self.explain = QtWidgets.QLabel()
        self.explain.setWordWrap(True)
        il.addWidget(self.explain, 2, 0)
        self.outputs_label = hint("")
        self.outputs_label.setTextFormat(Qt.RichText)
        il.addWidget(self.outputs_label, 3, 0)
        ov = QtWidgets.QVBoxLayout()
        ov.addWidget(QtWidgets.QLabel("<b>Starting overlap</b> <span style='color:#ad1457'>fixed</span> / "
                                      "<span style='color:#2e7d32'>moving</span>"))
        self.overlap = QtWidgets.QLabel("computing...")
        self.overlap.setMinimumSize(300, 100)
        self.overlap.setAlignment(Qt.AlignCenter)
        ov.addWidget(self.overlap)
        self.overlap_hint = hint("Placed by the image headers only. Large offsets can make the "
                                 "registration fail; ANTs first aligns the centres of mass.")
        ov.addWidget(self.overlap_hint)
        il.addLayout(ov, 1, 1, 3, 1)
        il.setColumnStretch(0, 1)
        lay.addWidget(info)

        cmd_row = QtWidgets.QHBoxLayout()
        cmd_row.addWidget(QtWidgets.QLabel("Command"))
        self.cmd = QtWidgets.QLineEdit()
        self.cmd.setReadOnly(True)
        self.cmd.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        cmd_row.addWidget(self.cmd, 1)
        lay.addLayout(cmd_row)

        if not ants.ants_available():
            warn = QtWidgets.QLabel("ANTs was not found on PATH (antsRegistrationSyNQuick.sh, "
                                    "antsApplyTransforms). Install ANTs or set $ANTSPATH.", objectName="error")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        self.type_group.idClicked.connect(lambda *_: self._refresh())
        self.quick.currentIndexChanged.connect(lambda *_: self._refresh())
        self.threads.valueChanged.connect(lambda *_: self._refresh())
        self.outdir.changed.connect(lambda *_: self._refresh(images=False))
        self.fixed_edit.changed.connect(lambda p: self._set_images(p, self.moving_edit.path()))
        self.moving_edit.changed.connect(lambda p: self._set_images(self.fixed_edit.path(), p))
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._ow = None
        self._set_images(self.fixed_path, self.moving_path)
        self.resize(940, 900)

    # -- state
    def transform(self) -> str:
        return self.type_group.checkedButton().property("key")

    def prefix(self) -> str:
        return os.path.join(self.outdir.path(), f"coreg.{self.reg_id}.")

    def command(self):
        return ants.registration_command(self.fixed_path, self.moving_path, self.prefix(), self.transform(),
                                         quick=self.quick.currentIndex() == 0, threads=self.threads.value())

    def _swap(self):
        f, m = self.fixed_edit.path(), self.moving_edit.path()
        self.fixed_edit.blockSignals(True)
        self.moving_edit.blockSignals(True)
        self.fixed_edit.setPath(m)
        self.moving_edit.setPath(f)
        self.fixed_edit.blockSignals(False)
        self.moving_edit.blockSignals(False)
        self._set_images(m, f)

    def _set_images(self, fixed, moving):
        changed_fixed = fixed != getattr(self, "_last_fixed", None)
        self.fixed_path, self.moving_path = fixed, moving
        self._last_fixed = fixed
        self.fixed_card.set_path(fixed)
        self.moving_card.set_path(moving)
        if fixed and (changed_fixed or not self.outdir.path()):
            self.outdir.setPath(os.path.join(os.path.dirname(os.path.abspath(fixed)), f"coreg.{self.reg_id}"))
        self._refresh()

    def _refresh(self, images=True):
        t = self.transform()
        label, stages, text = ants.TRANSFORMS[t]
        for name, chip in self.chips.items():
            chip.setProperty("active", "true" if name in stages else "false")
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        quick = self.quick.currentIndex() == 0
        speed = ("Quick mode: fewer iterations and the MI metric; usually minutes."
                 if quick else "Thorough mode: more iterations (and the CC metric for SyN); can take an hour or more.")
        self.explain.setText(f"<b>{label}.</b> {text}<br>{speed}")
        outs = "".join(f"<br>&nbsp;&nbsp;<code>{os.path.basename(p)}</code> - {d}"
                       for p, d in ants.outputs(self.prefix(), t))
        self.outputs_label.setText(f"Files written to <code>{self.outdir.path()}</code>:{outs}"
                                   f"<br>&nbsp;&nbsp;<code>coreg.{self.reg_id}.details</code> - "
                                   "what was run + ready-made apply-transform commands")
        self.id_label.setText(f"Registration ID: {self.reg_id}")
        self.cmd.setText(" ".join(shlex.quote(c) for c in self.command()))
        self.cmd.setCursorPosition(0)
        if images and self.fixed_path and self.moving_path:
            self._start_overlap()

    def _start_overlap(self):
        key = (self.fixed_path, self.moving_path)
        if getattr(self, "_overlap_key", None) == key:
            return
        self._overlap_key = key
        self.overlap.setText("computing...")
        rep = Report()
        w = Worker(lambda r, k=key: (k, overlap_image(*k)), rep)
        w.succeeded.connect(self._show_overlap)
        w.failed.connect(lambda m, tb: self.overlap.setText(f"preview failed: {m}"))
        w.start()
        self._ow = (w, rep)  # keep a reference while it runs

    def _show_overlap(self, result):
        key, arr = result
        if key != self._overlap_key:
            return
        self.overlap.setPixmap(QtGui.QPixmap.fromImage(to_qimage(arr)))

    # -- running
    def validate(self):
        for p, n in ((self.fixed_path, "fixed"), (self.moving_path, "moving")):
            if not os.path.isfile(p):
                return f"Choose the {n} image"
        if os.path.abspath(self.fixed_path) == os.path.abspath(self.moving_path):
            return "Fixed and moving are the same file"
        if not ants.ants_available():
            return "ANTs not found"
        return None

    def start(self):
        self.t0 = time.time()
        super().start()
        if self.worker:
            self.report.progressed.connect(lambda *_: self._tick())
            self.timer.start(1000)

    def _end(self, msg, outputs=(), ok=True):
        self.timer.stop()
        super()._end(msg, outputs, ok)
        if ok:  # next run gets a fresh ID and folder
            self.reg_id = ants.new_id()
            self.outdir.setPath(os.path.join(os.path.dirname(os.path.abspath(self.fixed_path)),
                                             f"coreg.{self.reg_id}"))

    def _tick(self):
        self.panel.status.setText(self._status_text())

    def _status_text(self):
        prog = getattr(self, "progress", None)
        el = time.time() - self.t0
        txt = prog.status if prog else "Starting"
        txt += f"   |   elapsed {_hms(el)}"
        if prog and prog.fraction > 0.03:
            remaining = el * (1 - prog.fraction) / prog.fraction
            txt += f"   |   ~{_hms(remaining)} left (rough estimate)"
        return txt

    def task(self, report):
        os.makedirs(self.outdir.path(), exist_ok=True)
        cmd = self.command()
        t = self.transform()
        details = os.path.join(self.outdir.path(), f"coreg.{self.reg_id}.details")
        with open(details, "w") as f:
            f.write(ants.details_text(self.reg_id, self.fixed_path, self.moving_path, self.prefix(), t, cmd))
        self.progress = prog = ants.RegistrationProgress()
        report.log("$ " + " ".join(shlex.quote(c) for c in cmd))

        def on_line(line):
            report.log(line)
            prog.feed(line)
            report.progressed.emit(prog.fraction, "")

        code = run_streaming(cmd, on_line, report.cancel_event, cwd=self.outdir.path())
        if code != 0:
            raise RuntimeError(f"ANTs exited with code {code}; see the log")
        outs = [p for p, _ in ants.outputs(self.prefix(), t) if os.path.exists(p)]
        return outs + [details]

    def done_message(self, result):
        return (f"Done in {_hms(time.time() - self.t0)} - registration {self.reg_id}. "
                f"Check {os.path.basename(self.prefix())}Warped.nii.gz over the fixed image.")


def _hms(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------- apply transforms
INTERPOLATIONS = ["Linear", "NearestNeighbor", "GenericLabel", "BSpline", "LanczosWindowedSinc"]


class ApplyDialog(ToolDialog):
    title = "Apply transforms (ANTs)"
    subtitle = ("Brings an image into another space with saved transforms. ANTs applies the list "
                "from the bottom up: the last transform is applied first.")
    run_label = "Apply"
    show_log = False

    def build(self, lay):
        nifti = [f for f in self.files if io.is_nifti(f)]
        details = [f for f in self.files if f.endswith(".details")]
        form = QtWidgets.QFormLayout()
        self.moving = PathEdit(nifti[0] if nifti else "", placeholder="image to transform")
        self.ref = PathEdit(nifti[1] if len(nifti) > 1 else "", placeholder="reference image (defines the output grid)")
        form.addRow("Input image", self.moving)
        form.addRow("Reference", self.ref)
        lay.addLayout(form)

        box = QtWidgets.QGroupBox("Transforms")
        bl = QtWidgets.QVBoxLayout(box)
        load_row = QtWidgets.QHBoxLayout()
        load = QtWidgets.QPushButton("Load from a registration...")
        load.setToolTip("Pick a coreg.<id>.details file made by NiiCE or neuro-nautilus")
        self.direction = QtWidgets.QComboBox()
        self.direction.addItems(["moving -> fixed (direct)", "fixed -> moving (inverse)"])
        load_row.addWidget(load)
        load_row.addWidget(self.direction)
        load_row.addStretch(1)
        bl.addLayout(load_row)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Transform file", "Invert"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        bl.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        for name, fn in (("Add...", self._add), ("Remove", self._remove), ("Up", lambda: self._move(-1)),
                         ("Down", lambda: self._move(1))):
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        bl.addWidget(hint("Invert works for linear transforms (.mat). For warps use the "
                          "InverseWarp file instead."))
        lay.addWidget(box)

        form2 = QtWidgets.QFormLayout()
        self.interp = QtWidgets.QComboBox()
        self.interp.addItems(INTERPOLATIONS)
        form2.addRow("Interpolation", self.interp)
        form2.addRow("", hint("Use GenericLabel or NearestNeighbor for masks and atlases."))
        self.timeseries = QtWidgets.QCheckBox("4D time series (transform every volume)")
        form2.addRow(self.timeseries)
        self.out = PathEdit(save=True)
        form2.addRow("Save as", self.out)
        lay.addLayout(form2)

        load.clicked.connect(self._load_details)
        self.direction.currentIndexChanged.connect(lambda *_: self._details and self._fill(self._details))
        self.moving.changed.connect(self._moving_changed)
        self._details = None
        self._moving_changed(self.moving.path())
        if details:
            self._fill(ants.parse_details(details[0]))
        if not ants.find("antsApplyTransforms"):
            warn = QtWidgets.QLabel("antsApplyTransforms was not found on PATH.", objectName="error")
            lay.addWidget(warn)
        self.resize(760, 640)

    def _moving_changed(self, p):
        if p and os.path.isfile(p):
            base, ext = io.split_ext(p)
            self.out.setPath(io.unique_path(base + ".newspace" + (ext or ".nii.gz")))
            try:
                self.timeseries.setChecked(len(io.load(p).shape) > 3)
            except Exception:
                pass

    def _row(self, path, inv=False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QtWidgets.QTableWidgetItem(os.path.basename(path))
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        self.table.setItem(r, 0, it)
        chk = QtWidgets.QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if inv else Qt.Unchecked)
        self.table.setItem(r, 1, chk)

    def transforms(self):
        return [(self.table.item(r, 0).data(Qt.UserRole), self.table.item(r, 1).checkState() == Qt.Checked)
                for r in range(self.table.rowCount())]

    def _set_transforms(self, items):
        self.table.setRowCount(0)
        for p, inv in items:
            self._row(p, inv)

    def _add(self):
        start = os.path.dirname(self.moving.path())
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add transforms", start, "Transforms (*.mat *.txt *.h5 *.nii *.nii.gz);;All files (*)")
        for p in paths:
            self._row(p)

    def _remove(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _move(self, d):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if len(rows) != 1:
            return
        r = rows[0]
        items = self.transforms()
        if 0 <= r + d < len(items):
            items[r], items[r + d] = items[r + d], items[r]
            self._set_transforms(items)
            self.table.selectRow(r + d)

    def _load_details(self):
        start = os.path.dirname(self.moving.path())
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Registration details", start,
                                                     "Registration details (*.details);;All files (*)")
        if p:
            try:
                self._fill(ants.parse_details(p))
            except Exception as e:
                error("Could not read", str(e), parent=self)

    def _fill(self, d):
        self._details = d
        inverse = self.direction.currentIndex() == 1
        self._set_transforms(ants.transform_chain(d["prefix"], d.get("transform", "s"), inverse))
        ref = d.get("moving" if inverse else "fixed")
        if ref:
            self.ref.setPath(ref)

    def validate(self):
        if not os.path.isfile(self.moving.path()):
            return "Choose the input image"
        if not os.path.isfile(self.ref.path()):
            return "Choose a reference image"
        tr = self.transforms()
        if not tr:
            return "Add at least one transform"
        missing = [p for p, _ in tr if not os.path.exists(p)]
        if missing:
            return f"Transform not found: {missing[0]}"
        if not self.out.path():
            return "Choose an output file"
        return None

    def task(self, report):
        cmd = ants.apply_command(self.moving.path(), self.ref.path(), self.out.path(), self.transforms(),
                                 self.interp.currentText(), timeseries=self.timeseries.isChecked())
        report.log("$ " + " ".join(shlex.quote(c) for c in cmd))
        report.busy("Applying transforms...")
        code = run_streaming(cmd, report.log, report.cancel_event)
        if code != 0:
            raise RuntimeError(f"antsApplyTransforms exited with code {code}; see the log")
        return [self.out.path()]


def register(files):
    return run_dialog(RegisterDialog, files)


def apply(files):
    return run_dialog(ApplyDialog, files)
