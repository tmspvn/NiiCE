"""Dialog built automatically from an `image_filter` plugin's parameter spec."""
from __future__ import annotations

import os

import nibabel as nib
import numpy as np
from PySide6 import QtWidgets

from .. import plugins as P
from ..core import io
from .common import PathEdit, ToolDialog, file_list_with_buttons, hint, run_dialog


def make_widget(param: P.Param):
    if isinstance(param, P.Bool):
        w = QtWidgets.QCheckBox()
        w.setChecked(bool(param.default))
        return w, w.isChecked
    if isinstance(param, P.Int):
        w = QtWidgets.QSpinBox()
        w.setRange(int(max(param.min, -2**31 + 1)), int(min(param.max, 2**31 - 1)))
        w.setValue(int(param.default))
        return w, w.value
    if isinstance(param, P.Float):
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(param.min, param.max)
        w.setDecimals(param.decimals)
        w.setSingleStep(param.step)
        w.setValue(float(param.default))
        return w, w.value
    if isinstance(param, P.Choice):
        w = QtWidgets.QComboBox()
        w.addItems([str(c) for c in param.choices])
        if param.default is not None:
            w.setCurrentText(str(param.default))
        return w, w.currentText
    if isinstance(param, P.File):
        w = PathEdit(param.default or "", filter=param.filter)
        return w, w.path
    w = QtWidgets.QLineEdit(str(param.default or ""))
    return w, w.text


class FilterDialog(ToolDialog):
    def __init__(self, files, fn, spec, suffix, title, description, parent=None):
        self.fn, self.spec, self.suffix_default = fn, spec, suffix
        self.title, self.subtitle = title, description
        super().__init__(files, parent)

    def build(self, lay):
        box, self.flist = file_list_with_buttons(self.files)
        lay.addWidget(box)
        form = QtWidgets.QFormLayout()
        self.getters = {}
        for name, param in self.spec.items():
            w, get = make_widget(param)
            if param.help:
                w.setToolTip(param.help)
            form.addRow(param.label or name.replace("_", " "), w)
            if param.help:
                form.addRow("", hint(param.help))
            self.getters[name] = get
        self.suffix_edit = QtWidgets.QLineEdit(self.suffix_default)
        form.addRow("Output suffix", self.suffix_edit)
        lay.addLayout(form)

    def validate(self):
        return None if self.flist.count() else "Add at least one image"

    def task(self, report):
        params = {k: g() for k, g in self.getters.items()}
        paths, outs = self.flist.paths(), []
        for i, p in enumerate(paths):
            report.check()
            report.progress(i / len(paths), f"{os.path.basename(p)} ({i + 1}/{len(paths)})")
            img = io.load(p)
            result = self.fn(io.load_data(img), img, **params)
            out = self.out_name(p, self.suffix_edit.text())
            if isinstance(result, nib.spatialimages.SpatialImage):
                nib.save(result, out)
            elif result is not None:
                io.save_like(np.asarray(result), img, out)
            else:
                continue
            outs.append(out)
        return outs


def run_image_filter(files, fn, spec, suffix, title, description):
    return run_dialog(FilterDialog, files, fn, spec, suffix, title, description)
