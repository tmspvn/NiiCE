"""Operations: binarise, threshold, extract ROI / volume, resample, subtract, skull strip."""
from __future__ import annotations

import os
import shutil

from PySide6 import QtCore, QtWidgets

from ..core import io, ops, space, synthstrip
from ..core.proc import run_streaming
from .. import settings as user_settings
from .common import (Banner, PathEdit, ToolDialog, app, ask, file_list_with_buttons, hint, run_dialog,
                     run_quick)

Qt = QtCore.Qt


def dspin(value=0.0, lo=-1e9, hi=1e9, decimals=4, step=1.0):
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setMinimumWidth(110)
    return s


class BatchDialog(ToolDialog):
    """Dialog that applies the same operation to every selected image."""
    suffix = "_out"

    def add_files(self, lay):
        box, self.flist = file_list_with_buttons(self.files, title="Images")
        lay.addWidget(box)

    def add_suffix(self, form, default=None):
        self.suffix_edit = QtWidgets.QLineEdit(default or self.suffix)
        form.addRow("Output suffix", self.suffix_edit)
        self.suffix_edit.textChanged.connect(self._update_example)
        self.example = hint("")
        form.addRow("", self.example)
        self._update_example()

    def _update_example(self):
        if getattr(self, "flist", None) and self.flist.count():
            name = io.out_path(self.flist.paths()[0], self.suffix_edit.text(),
                               ext=self.settings.get("output_ext", "same"), unique=False)
            self.example.setText(f"e.g. {os.path.basename(name)}  (existing files are not overwritten)")

    def validate(self):
        if not self.flist.count():
            return "Add at least one image"
        return None

    def task(self, report):
        paths = self.flist.paths()
        outs = []
        for i, p in enumerate(paths):
            report.check()
            report.progress(i / len(paths), f"{os.path.basename(p)} ({i + 1}/{len(paths)})")
            outs.extend(self.process(p, report) or [])
        return outs

    def process(self, path, report) -> list:
        raise NotImplementedError


# --------------------------------------------------------------------------- binarise
class BinariseDialog(BatchDialog):
    title = "Binarise"
    subtitle = "Voxels above the threshold become 1, everything else (and NaN) 0."
    suffix = "_mask"

    def build(self, lay):
        self.add_files(lay)
        form = QtWidgets.QFormLayout()
        self.thr = dspin(0.0)
        form.addRow("Voxel value >", self.thr)
        self.add_suffix(form)
        lay.addLayout(form)

    def process(self, path, report):
        return [ops.binarise(path, self.out_name(path, self.suffix_edit.text()), self.thr.value())]


# --------------------------------------------------------------------------- threshold
class ThresholdDialog(BatchDialog):
    title = "Threshold"
    subtitle = "Zero voxels outside the chosen range (bounds included)."

    def build(self, lay):
        self.add_files(lay)
        try:
            q = ops.percentiles(self.files[0])
            self.stats = hint(f"{os.path.basename(self.files[0])} (non-zero voxels):  min {q[0]:.4g} | "
                              f"P2 {q[1]:.4g} | median {q[2]:.4g} | P98 {q[3]:.4g} | max {q[4]:.4g}")
        except Exception:
            q = [0, 0, 0, 1, 1]
            self.stats = hint("")
        lay.addWidget(self.stats)
        form = QtWidgets.QFormLayout()
        self.unit = QtWidgets.QComboBox()
        self.unit.addItems(["image values", "percentiles of non-zero voxels (0-100)"])
        form.addRow("Bounds are", self.unit)
        self.use_lo = QtWidgets.QCheckBox("Lower")
        self.use_lo.setChecked(True)
        self.lo = dspin(q[2])
        self.use_hi = QtWidgets.QCheckBox("Upper")
        self.hi = dspin(q[4])
        r1 = QtWidgets.QHBoxLayout()
        r1.addWidget(self.use_lo)
        r1.addWidget(self.lo)
        r1.addSpacing(20)
        r1.addWidget(self.use_hi)
        r1.addWidget(self.hi)
        r1.addStretch(1)
        form.addRow("Keep voxels", r1)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["keep their values", "binarise (0/1 mask)"])
        form.addRow("Kept voxels", self.mode)
        self.use_lo.toggled.connect(self.lo.setEnabled)
        self.use_hi.toggled.connect(self.hi.setEnabled)
        self.hi.setEnabled(False)
        self.add_suffix(form, "_thr")
        lay.addLayout(form)
        self._q = q
        self.unit.currentIndexChanged.connect(self._unit_changed)

    def _unit_changed(self, i):
        if i == 1:
            self.lo.setValue(50)
            self.hi.setValue(100)
        else:
            self.lo.setValue(self._q[2])
            self.hi.setValue(self._q[4])

    def validate(self):
        if not (self.use_lo.isChecked() or self.use_hi.isChecked()):
            return "Enable a lower and/or an upper bound"
        return super().validate()

    def process(self, path, report):
        lo = self.lo.value() if self.use_lo.isChecked() else None
        hi = self.hi.value() if self.use_hi.isChecked() else None
        return [ops.threshold(path, self.out_name(path, self.suffix_edit.text()), lo, hi,
                              binarise=self.mode.currentIndex() == 1,
                              percentile=self.unit.currentIndex() == 1)]


# --------------------------------------------------------------------------- extract ROI
class ExtractRoiDialog(BatchDialog):
    title = "Extract ROI"
    subtitle = "Pick labels of a label/atlas image. Each label becomes its own mask, or all go in one."

    def build(self, lay):
        self.add_files(lay)
        self.labels = QtWidgets.QListWidget()
        try:
            found = ops.labels(self.files[0])
            for v, n in found:
                txt = f"{v:g}" if v != int(v) else f"{int(v)}"
                it = QtWidgets.QListWidgetItem(f"label {txt}    ({n} voxels)")
                it.setData(Qt.UserRole, v)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked)
                self.labels.addItem(it)
            info = hint(f"{len(found)} labels found in {os.path.basename(self.files[0])}.")
        except Exception as e:
            info = hint(f"Could not list labels: {e}")
        lay.addWidget(info)
        filt = QtWidgets.QLineEdit()
        filt.setPlaceholderText("Type labels to tick them: 17, 53  or  17 53  or a range 10-20")
        filt.textChanged.connect(self._tick)
        self._typed = set()  # labels ticked by typing, so they can be unticked when deleted
        lay.addWidget(filt)
        lay.addWidget(self.labels, 1)
        self.selected = QtWidgets.QLabel("Selected: none")
        self.selected.setWordWrap(True)
        self.labels.itemChanged.connect(self._show_selected)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.selected, 1)
        for name, state in (("All", Qt.Checked), ("None", Qt.Unchecked)):
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(lambda _=False, s=state: self._set_all(s))
            row.addWidget(b)
        lay.addLayout(row)
        form = QtWidgets.QFormLayout()
        self.combine = QtWidgets.QCheckBox("Save all ticked labels in one mask")
        self.keep = QtWidgets.QCheckBox("Keep label values instead of 1")
        form.addRow(self.combine)
        form.addRow(self.keep)
        lay.addLayout(form)

    def _tick(self, text):
        """Tick the labels typed so far (live); untick ones that were typed and then deleted."""
        wanted, ranges = [], []
        for t in text.replace(";", ",").replace(" ", ",").split(","):
            t = t.strip()
            if "-" in t[1:]:  # range such as 10-20
                lo, hi = t[0] + t[1:].split("-", 1)[0], t[1:].split("-", 1)[1]
                try:
                    ranges.append((float(lo), float(hi)))
                except ValueError:
                    pass
                continue
            try:
                wanted.append(float(t))
            except ValueError:
                pass
        now = set()
        first = None
        for i in range(self.labels.count()):
            it = self.labels.item(i)
            v = it.data(Qt.UserRole)
            if any(abs(v - w) < 1e-4 for w in wanted) or any(lo - 1e-4 <= v <= hi + 1e-4 for lo, hi in ranges):
                now.add(v)
                it.setCheckState(Qt.Checked)
                first = first or it
            elif v in self._typed:
                it.setCheckState(Qt.Unchecked)
        self._typed = now
        if first is not None:
            self.labels.scrollToItem(first)

    def _show_selected(self, *_):
        vals = self.chosen()
        names = ", ".join(f"{int(v)}" if v == int(v) else f"{v:g}" for v in vals[:30])
        more = f" ... (+{len(vals) - 30})" if len(vals) > 30 else ""
        self.selected.setText(f"Selected: {names}{more}  ({len(vals)} label{'s' if len(vals) != 1 else ''})"
                              if vals else "Selected: none")

    def _set_all(self, state):
        for i in range(self.labels.count()):
            self.labels.item(i).setCheckState(state)

    def chosen(self):
        return [self.labels.item(i).data(Qt.UserRole) for i in range(self.labels.count())
                if self.labels.item(i).checkState() == Qt.Checked]

    def validate(self):
        if not self.chosen():
            return "Tick at least one label"
        return super().validate()

    def process(self, path, report):
        vals = self.chosen()
        keep = self.keep.isChecked()
        name = lambda v: f"{int(v)}" if v == int(v) else f"{v:g}"
        if self.combine.isChecked():
            suffix = "_roi-" + "+".join(name(v) for v in vals[:6]) + ("+more" if len(vals) > 6 else "")
            return [ops.extract_roi(path, vals, self.out_name(path, suffix), keep)]
        outs = []
        for v in vals:
            report.check()
            outs.append(ops.extract_roi(path, [v], self.out_name(path, f"_roi-{name(v)}"), keep))
        return outs


# --------------------------------------------------------------------------- extract volume
class ExtractVolumeDialog(BatchDialog):
    title = "Extract volume"
    subtitle = "Pick volumes of a 4D image by index (the first volume is 0)."

    def build(self, lay):
        self.add_files(lay)
        try:
            self.nvol = io.n_volumes(io.load(self.files[0]))
        except Exception:
            self.nvol = 1
        form = QtWidgets.QFormLayout()
        form.addRow("", hint("Examples:  0   |   0, 5, 10-20   |   -1 (= last volume)"))
        self.idx = QtWidgets.QLineEdit("0")
        count = QtWidgets.QLabel(f"of <b>{self.nvol}</b> volumes (0-{self.nvol - 1})")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.idx, 1)
        row.addWidget(count)
        form.addRow("Volumes", row)
        self.separate = QtWidgets.QCheckBox("One file per volume (otherwise one 4D file)")
        form.addRow(self.separate)
        self.preview = hint("")
        form.addRow("", self.preview)
        self.idx.textChanged.connect(self._preview)
        lay.addLayout(form)
        self._preview()

    def _preview(self):
        try:
            idx = ops.parse_indices(self.idx.text(), self.nvol)
            self.preview.setText(f"{len(idx)} volume(s): " + ", ".join(map(str, idx[:20]))
                                 + (" ..." if len(idx) > 20 else ""))
        except ValueError as e:
            self.preview.setText(str(e))

    def validate(self):
        try:
            ops.parse_indices(self.idx.text(), self.nvol)
        except ValueError as e:
            return str(e)
        return super().validate()

    def process(self, path, report):
        n = io.n_volumes(io.load(path))
        idx = ops.parse_indices(self.idx.text(), n)
        if self.separate.isChecked() or len(idx) == 1:
            return [ops.extract_volumes(path, [i], self.out_name(path, f"_vol-{i}")) for i in idx]
        tag = f"{idx[0]}-{idx[-1]}" if idx == list(range(idx[0], idx[-1] + 1)) else f"{len(idx)}vols"
        return [ops.extract_volumes(path, idx, self.out_name(path, f"_vol-{tag}"))]


# --------------------------------------------------------------------------- resample
class ResampleDialog(BatchDialog):
    title = "Resample"
    subtitle = "Change voxel size or matrix size (keeping the field of view), or match another image's grid."

    def build(self, lay):
        self.add_files(lay)
        self.img = io.load(self.files[0])
        shape, zooms = self.img.shape[:3], self.img.header.get_zooms()[:3]
        lay.addWidget(hint(f"Current grid of {os.path.basename(self.files[0])}: "
                           + ops.describe_grid(shape, self.img.affine)))
        form = QtWidgets.QFormLayout()
        self.form = form
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["Voxel size (mm)", "Dimensions (voxels)", "Match a reference image"])
        form.addRow("Target", self.mode)
        self.xyz = []
        row = QtWidgets.QHBoxLayout()
        for i in range(3):
            s = dspin(zooms[i], 0.01, 10000, 3, 0.5)
            s.valueChanged.connect(self._preview)
            self.xyz.append(s)
            row.addWidget(QtWidgets.QLabel("xyz"[i]))
            row.addWidget(s)
        self.iso = QtWidgets.QCheckBox("isotropic")
        self.iso.toggled.connect(lambda on: on and self._iso(self.xyz[0].value()))
        self.xyz[0].valueChanged.connect(lambda v: self.iso.isChecked() and self._iso(v))
        row.addWidget(self.iso)
        self.xyz_row = QtWidgets.QWidget()
        self.xyz_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Values", self.xyz_row)
        self.ref = PathEdit(placeholder="reference image")
        self.ref.changed.connect(self._preview)
        form.addRow("Reference", self.ref)
        self.interp = QtWidgets.QComboBox()
        self.interp.addItems(["linear", "nearest neighbour (labels/masks)", "cubic B-spline"])
        form.addRow("Interpolation", self.interp)
        self.result = hint("")
        form.addRow("New grid", self.result)
        self.add_suffix(form, "_resampled")
        lay.addLayout(form)
        self.mode.currentIndexChanged.connect(self._mode)
        self._mode(0)
        if len(self.files) >= 2:
            self.ref.setPath(self.files[1])

    def _iso(self, v):
        for s in self.xyz[1:]:
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self._preview()

    def _mode(self, i):
        shape, zooms = self.img.shape[:3], self.img.header.get_zooms()[:3]
        self.form.setRowVisible(self.xyz_row, i < 2)
        self.form.setRowVisible(self.ref, i == 2)
        for k, s in enumerate(self.xyz):
            s.blockSignals(True)
            if i == 0:
                s.setDecimals(3)
                s.setValue(zooms[k])
            elif i == 1:
                s.setDecimals(0)
                s.setValue(shape[k])
            s.blockSignals(False)
        self._preview()

    def _grid(self, img):
        i = self.mode.currentIndex()
        if i == 2:
            return ops.target_grid(img, "reference", ref=self.ref.path())
        vals = [s.value() for s in self.xyz]
        return ops.target_grid(img, "spacing" if i == 0 else "shape", vals)

    def _preview(self, *_):
        try:
            shape, aff = self._grid(self.img)
            self.result.setText(ops.describe_grid(shape, aff))
        except Exception as e:
            self.result.setText(str(e) if self.mode.currentIndex() != 2 or self.ref.path() else "choose a reference")

    def validate(self):
        if self.mode.currentIndex() == 2 and not os.path.isfile(self.ref.path()):
            return "Choose a reference image"
        return super().validate()

    def process(self, path, report):
        order = {0: 1, 1: 0, 2: 3}[self.interp.currentIndex()]
        i = self.mode.currentIndex()
        mode = ("spacing", "shape", "reference")[i]
        vals = [s.value() for s in self.xyz]
        return [ops.resample(path, self.out_name(path, self.suffix_edit.text()), mode, vals,
                             ref=self.ref.path() or None, order=order, progress=None)]


# --------------------------------------------------------------------------- subtract
class SubtractDialog(ToolDialog):
    title = "Subtract"
    subtitle = "A minus B, voxel by voxel. Both images must be on the same voxel grid."

    def build(self, lay):
        files = self.files + [""] * (2 - len(self.files))
        form = QtWidgets.QFormLayout()
        self.a = PathEdit(files[0])
        self.b = PathEdit(files[1], placeholder="choose the image to subtract")
        form.addRow("A", self.a)
        form.addRow("B", self.b)
        swap = QtWidgets.QPushButton("Swap A and B")
        swap.clicked.connect(self._swap)
        form.addRow("", swap)
        self.formula = QtWidgets.QLabel()
        self.formula.setObjectName("subtitle")
        form.addRow("Result", self.formula)
        self.out = PathEdit(save=True)
        form.addRow("Save as", self.out)
        lay.addLayout(form)
        self.space = Banner("info", "")
        lay.addWidget(self.space)
        self.a.changed.connect(self._names)
        self.b.changed.connect(self._names)
        self._names()

    def _swap(self):
        a, b = self.a.path(), self.b.path()
        self.a.setPath(b)
        self.b.setPath(a)

    def _names(self, *_):
        a, b = self.a.path(), self.b.path()
        self.formula.setText(f"{io.stem(a) or 'A'}  -  {io.stem(b) or 'B'}")
        if a and b:
            self.out.setPath(difference_path(a))
        self.space.set("info", "")
        if os.path.isfile(a) and os.path.isfile(b):
            try:
                r = space.compare(io.load(a), io.load(b))
                if not r.same_grid:
                    self.space.set("warning", "A and B are not on the same voxel grid", [r.summary] + r.issues)
            except Exception:
                pass

    def validate(self):
        for p, n in ((self.a.path(), "A"), (self.b.path(), "B")):
            if not os.path.isfile(p):
                return f"Choose image {n}"
        if not self.out.path():
            return "Choose an output file"
        if os.path.exists(self.out.path()) and not ask("Overwrite?", f"{self.out.path()} exists. Overwrite?", self):
            return "Not overwritten"
        return None

    def task(self, report):
        report.busy("Subtracting")
        return [ops.subtract(self.a.path(), self.b.path(), self.out.path())]


# --------------------------------------------------------------------------- skull strip
class SkullstripDialog(BatchDialog):
    title = "Skull strip (SynthStrip)"
    subtitle = "Deep-learning brain extraction that works on any contrast and resolution (Hoopes et al., 2022)."
    show_log = True

    def build(self, lay):
        self.add_files(lay)
        pref = self.settings.get("synthstrip_backend", "auto")
        self.backend, self.exe = synthstrip.pick_backend(pref)
        if self.backend:
            what = {"python": "nipreps-synthstrip (Python + PyTorch, no container)",
                    "native": "mri_synthstrip (FreeSurfer)",
                    "docker": f"Docker image {self.settings.get('synthstrip_image')}",
                    "podman": f"Podman image {self.settings.get('synthstrip_image')}",
                    "apptainer": "Apptainer/Singularity"}[self.backend]
            status = QtWidgets.QLabel(f"Using {what}  ({self.exe})", objectName="ok")
        else:
            status = QtWidgets.QLabel(synthstrip.INSTALL_HELP, objectName="error")
        status.setWordWrap(True)
        lay.addWidget(status)
        form = QtWidgets.QFormLayout()
        self.mask = QtWidgets.QCheckBox("Also save the brain mask (_brainmask)")
        self.mask.setChecked(True)
        self.nocsf = QtWidgets.QCheckBox("Exclude CSF from the brain border (--no-csf)")
        python = self.backend == "python"
        # nipreps-synthstrip takes whole millimetres for the border
        self.border = dspin(1.0, -10, 10, 0 if python else 1, 1.0 if python else 0.5)
        form.addRow(self.mask)
        form.addRow(self.nocsf)
        form.addRow("Mask border (mm)", self.border)
        self.threads = QtWidgets.QSpinBox()
        self.threads.setRange(1, max(1, os.cpu_count() or 1))
        self.threads.setValue(min(int(self.settings.get("ants_threads", 3)), self.threads.maximum()))
        if python:
            form.addRow("CPU threads", self.threads)
            self.weights_hint = hint("")
            form.addRow("", self.weights_hint)
            self.nocsf.toggled.connect(self._weights_status)
            self._weights_status()
        self.add_suffix(form, "_brain")
        lay.addLayout(form)
        if self.backend in ("docker", "podman"):
            lay.addWidget(hint("The first run downloads the image (~1-2 GB) if it is not present yet."))
        self.pkexec = bool(self.settings.get("docker_pkexec"))

    def _weights_status(self, *_):
        name = synthstrip.WEIGHTS[self.nocsf.isChecked()][0]
        found = synthstrip.find_weights(self.nocsf.isChecked())
        self.weights_hint.setText(f"Model weights: {found}" if found else
                                  f"Model weights {name} (~{synthstrip.WEIGHTS_MB} MB) will be downloaded "
                                  "once from surfer.nmr.mgh.harvard.edu (MIT / CC BY 4.0 licence).")

    def validate(self):
        if not self.backend:
            return "SynthStrip not found (see message above)"
        self._download = False
        if self.backend == "python" and not synthstrip.find_weights(self.nocsf.isChecked()):
            name = synthstrip.WEIGHTS[self.nocsf.isChecked()][0]
            if not ask("Download model weights?",
                       f"SynthStrip needs its trained model ({name}, about {synthstrip.WEIGHTS_MB} MB).\n"
                       f"Download it now from the official SynthStrip website into\n{synthstrip.weights_dir()} ?",
                       self):
                return "The model weights are needed to run SynthStrip"
            self._download = True
        return super().validate()

    def task(self, report):
        if getattr(self, "_download", False):
            path = synthstrip.download_weights(self.nocsf.isChecked(), report.progress,
                                               lambda: report.cancelled)
            report.log(f"Downloaded and verified {path}")
        return super().task(report)

    def process(self, path, report):
        i = self.flist.paths().index(path) + 1 if path in self.flist.paths() else 1
        report.busy(f"Running SynthStrip on {os.path.basename(path)} ({i}/{self.flist.count()})...")
        out = self.out_name(path, self.suffix_edit.text())
        mask = io.out_path(path, "_brainmask", ext=self.settings.get("output_ext", "same")) \
            if self.mask.isChecked() else None
        outs, self.pkexec = strip_one(path, out, mask, self.backend, self.exe, self.settings, report,
                                      no_csf=self.nocsf.isChecked(), border=self.border.value(),
                                      threads=self.threads.value(), pkexec=self.pkexec)
        return outs


def strip_one(path, out, mask, backend, exe, s, report, no_csf=False, border=1.0, threads=None,
              pkexec=False):
    """Run SynthStrip on one image. Returns (written paths, whether pkexec ended up being used)."""
    for attempt in range(2):
        cmd = synthstrip.build_command(backend, exe, path, out, mask, no_csf=no_csf, border=border,
                                       image=s.get("synthstrip_image", synthstrip.DEFAULT_IMAGE),
                                       pkexec=pkexec, threads=threads)
        report.log("$ " + " ".join(cmd))
        lines = []

        def on_line(line):
            lines.append(line)
            report.log(line)

        code = run_streaming(cmd, on_line, report.cancel_event)
        if code == 0:
            return [p for p in (out, mask) if p], pkexec
        text = "\n".join(lines)
        if attempt == 0 and backend == "docker" and not pkexec \
                and synthstrip.is_permission_error(text) and shutil.which("pkexec"):
            report.log("Docker needs root: retrying with pkexec (you will be asked for your password).")
            pkexec = True
            continue
        last = next((l for l in reversed(lines) if l.strip()), "")
        raise RuntimeError(f"SynthStrip failed (exit code {code}): {last}")
    return [], pkexec


# --------------------------------------------------------------------------- quick runs
def difference_path(a: str) -> str:
    """difference.nii.gz next to A (never overwriting an existing file)."""
    ext = user_settings.get("output_ext")
    ext = ".nii.gz" if ext in (None, "same") else ext
    return io.unique_path(os.path.join(os.path.dirname(os.path.abspath(a)), "difference" + ext))


def _with_options(title, task, dialog_cls, files, running, extra=()):
    """Run at once with default settings; the pop-up's 'Options...' opens the full dialog."""
    wanted = {}

    def options(toast):
        wanted["dialog"] = True
        toast.close()

    run_quick(title, task, running, buttons=[("Options...", options), *extra])
    if wanted.get("dialog"):
        return run_dialog(dialog_cls, files)
    return 0


# --------------------------------------------------------------------------- entry points
def binarise(files):
    if len(files) != 1:
        return run_dialog(BinariseDialog, files)
    src = files[0]
    s = user_settings.load()

    def task(report):
        out = io.out_path(src, "_mask", ext=s.get("output_ext", "same"))
        return [ops.binarise(src, out, 0.0)]

    return _with_options("Binarise", task, BinariseDialog, files, f"Binarising {os.path.basename(src)} (> 0)...")


def threshold(files):
    return run_dialog(ThresholdDialog, files)


def extract_roi(files):
    return run_dialog(ExtractRoiDialog, files)


def extract_volume(files):
    return run_dialog(ExtractVolumeDialog, files)


def resample(files):
    return run_dialog(ResampleDialog, files)


def subtract(files):
    """Two images: compute first - second at once, in the order they were selected."""
    if len(files) != 2:
        return run_dialog(SubtractDialog, files)
    a, b = files
    out = difference_path(a)

    def task(report):
        ops.subtract(a, b, out)
        return f"Saved:\n{out}\n\ndifference = {os.path.basename(a)}  minus  {os.path.basename(b)}"

    run_quick("Subtract", task, f"{os.path.basename(a)} - {os.path.basename(b)}...")
    return 0


def skullstrip(files):
    """One image: skull strip at once with the default options (brain + brain mask)."""
    if len(files) != 1:
        return run_dialog(SkullstripDialog, files)
    app()
    src = files[0]
    s = user_settings.load()
    backend, exe = synthstrip.pick_backend(s.get("synthstrip_backend", "auto"))

    def task(report):
        if not backend:
            raise RuntimeError(synthstrip.INSTALL_HELP)
        if backend == "python" and not synthstrip.find_weights(False):
            path = synthstrip.download_weights(
                False, lambda f, m: report.progress(f, f"First use: {m}"), lambda: report.cancelled)
            report.log(f"Downloaded and verified {path}")
        report.busy(f"Running SynthStrip on {os.path.basename(src)} (about a minute on CPU)...")
        out = io.out_path(src, "_brain", ext=s.get("output_ext", "same"))
        mask = io.out_path(src, "_brainmask", ext=s.get("output_ext", "same"))
        outs, _ = strip_one(src, out, mask, backend, exe, s, report, threads=s.get("ants_threads", 3),
                            pkexec=bool(s.get("docker_pkexec")))
        return outs

    if backend == "python" and not synthstrip.find_weights(False):
        if not ask("Download model weights?",
                   f"SynthStrip needs its trained model ({synthstrip.WEIGHTS[False][0]}, about "
                   f"{synthstrip.WEIGHTS_MB} MB). Download it now from the official SynthStrip website?"):
            return 0
    return _with_options("Skull strip (SynthStrip)", task, SkullstripDialog, files,
                         f"Running SynthStrip on {os.path.basename(src)}...")

