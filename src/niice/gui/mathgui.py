"""Math: numpy expressions between images."""
from __future__ import annotations

import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import io, mathexpr
from .common import PathEdit, ToolDialog, ask, file_list_with_buttons, hint, run_dialog

Qt = QtCore.Qt

DTYPES = {"automatic": None, "float32": np.float32, "int16": np.int16, "int32": np.int32,
          "uint8 (masks)": np.uint8}


class MathDialog(ToolDialog):
    title = "Math"
    subtitle = ("Write a numpy expression. Each image is a letter: a, b, c ... "
                "The result is saved on the voxel grid of the reference image.")
    run_label = "Compute"

    def build(self, lay):
        box, self.flist = file_list_with_buttons(self.files, letters=True)
        lay.addWidget(box)
        self.flist.changed.connect(self._check)

        form = QtWidgets.QFormLayout()
        self.expr = QtWidgets.QLineEdit()
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        mono.setPointSize(mono.pointSize() + 2)
        self.expr.setFont(mono)
        self.expr.setPlaceholderText("e.g.  (a - b) / b * 100")
        self.expr.textChanged.connect(self._check)
        ex = QtWidgets.QToolButton(text="Examples")
        ex.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(ex)
        for e, desc in mathexpr.EXAMPLES:
            act = menu.addAction(f"{e}    -  {desc}")
            act.triggered.connect(lambda _=False, e=e: self.expr.setText(e))
        ex.setMenu(menu)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.expr, 1)
        row.addWidget(ex)
        form.addRow("Expression", row)
        self.check_label = QtWidgets.QLabel(" ")
        form.addRow("", self.check_label)

        first = self.files[0] if self.files else os.path.expanduser("~/x.nii.gz")
        self.out = PathEdit(os.path.join(os.path.dirname(first), "result.nii.gz"), save=True)
        form.addRow("Save as", self.out)
        self.ref = QtWidgets.QComboBox()
        form.addRow("Reference grid", self.ref)
        self.dtype = QtWidgets.QComboBox()
        self.dtype.addItems(DTYPES)
        form.addRow("Output type", self.dtype)
        lay.addLayout(form)

        helpers = ", ".join(sorted(mathexpr.HELPERS))
        lay.addWidget(hint(
            "Operators: + - * / ** % and comparisons > < >= <= == !=. Combine masks with & | ~ "
            "(not 'and'/'or'). Any np.<function> is allowed; these also work without 'np.': "
            f"{helpers}. Constants: pi, e, nan, inf. A result that is a single number "
            "(e.g. np.mean(a)) is shown instead of saved."))
        self._refill_ref()
        self._check()

    def _refill_ref(self):
        cur = self.ref.currentIndex()
        self.ref.clear()
        for i, p in enumerate(self.flist.paths()[:26]):
            self.ref.addItem(f"{chr(97 + i)}  ({os.path.basename(p)})")
        self.ref.setCurrentIndex(max(0, min(cur, self.ref.count() - 1)))

    def _check(self):
        if self.ref.count() != self.flist.count():
            self._refill_ref()
        try:
            used = mathexpr.validate(self.expr.text(), self.flist.count())
            self.check_label.setText("OK - uses " + ", ".join(used))
            self.check_label.setStyleSheet("color: #2e7d32;")
            return True
        except mathexpr.ExprError as e:
            self.check_label.setText(str(e) if self.expr.text().strip() else " ")
            self.check_label.setStyleSheet("color: #c62828;")
            return False

    def validate(self):
        if self.flist.count() == 0:
            return "Add at least one image"
        if self.flist.count() > 26:
            return "At most 26 images (a-z)"
        if not self._check():
            return self.check_label.text()
        out = self.out.path()
        if not out:
            return "Choose an output file"
        if not io.is_nifti(out):
            self.out.setPath(out + ".nii.gz")
        if os.path.exists(self.out.path()) and not ask("Overwrite?", f"{self.out.path()} exists. Overwrite?", self):
            return "Not overwritten - choose another name"
        return None

    def task(self, report):
        kind, value = mathexpr.run(self.expr.text(), self.flist.paths(), self.out.path(),
                                   dtype=DTYPES[self.dtype.currentText()],
                                   ref_index=self.ref.currentIndex(), progress=report.progress)
        if kind == "value":
            return f"Result: {value:.8g}   (a single number, nothing saved)"
        return [value]


def run(files):
    return run_dialog(MathDialog, files)
