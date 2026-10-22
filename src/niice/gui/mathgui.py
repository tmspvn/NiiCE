"""Math: Python/numpy code between images (one line or many)."""
from __future__ import annotations

import os
import re

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import io, mathexpr, space
from .. import settings
from . import theme
from .common import Banner, PathEdit, ToolDialog, ask, file_list_with_buttons, hint, run_dialog

Qt = QtCore.Qt

DTYPES = {"automatic": None, "float32": np.float32, "int16": np.int16, "int32": np.int32,
          "uint8 (masks)": np.uint8}


AVAILABLE_TEXT = """\
a, b, c ...      the selected images, as numpy arrays (float32)
np               numpy
nib              nibabel   (e.g. nib.load(path), nib.Nifti1Image(data, affine))
ndi              scipy.ndimage
ref_img          the reference image, as a nibabel image (ref_img.affine, ref_img.header)
result = ...     saved as 'Save as' (or just end with an expression)
save(arr, 'name')   writes an extra image on the reference grid
print(...)       shows in the log
import ...       works as usual (e.g. from scipy import stats)"""

# Code block colours: always a dark panel so it reads as code in light and dark desktop themes
CODE_BG, CODE_FG, GUTTER_BG, GUTTER_FG = "#16181f", "#e6e6e6", "#20232c", "#7f8596"


class CodeEdit(QtWidgets.QPlainTextEdit):
    """Plain-text code editor: line numbers, Tab = 4 spaces, auto-indent, Ctrl+Enter = run."""
    run_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setPointSize(font.pointSize() + 1)
        self.setFont(font)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.setStyleSheet(f"QPlainTextEdit {{ background: {CODE_BG}; color: {CODE_FG}; "
                           "selection-background-color: #3d5a80; selection-color: white; "
                           f"border: 1px solid {theme.accent()}; border-radius: 6px; }}")
        pal = self.palette()
        pal.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor(GUTTER_FG))
        self.setPalette(pal)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.gutter = QtWidgets.QWidget(self)
        self.gutter.paintEvent = self._paint_gutter
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._scroll_gutter)
        self._update_margin()

    def _gutter_width(self):
        return 10 + self.fontMetrics().horizontalAdvance("9") * max(2, len(str(self.blockCount())))

    def _update_margin(self, *_):
        self.setViewportMargins(self._gutter_width(), 0, 0, 0)

    def _scroll_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(cr.left(), cr.top(), self._gutter_width(), cr.height()))

    def _paint_gutter(self, ev):
        p = QtGui.QPainter(self.gutter)
        p.fillRect(ev.rect(), QtGui.QColor(GUTTER_BG))
        p.setPen(QtGui.QColor(GUTTER_FG))
        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while block.isValid() and top <= ev.rect().bottom():
            if block.isVisible():
                p.drawText(0, top, self.gutter.width() - 4, h, Qt.AlignRight, str(block.blockNumber() + 1))
            top += int(self.blockBoundingRect(block).height())
            block = block.next()
        p.end()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and ev.modifiers() & Qt.ControlModifier:
            self.run_requested.emit()
            return
        if ev.key() == Qt.Key_Tab and not ev.modifiers():
            self.insertPlainText("    ")
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            line = self.textCursor().block().text()
            indent = line[: len(line) - len(line.lstrip())]
            if line.rstrip().endswith(":"):
                indent += "    "
            super().keyPressEvent(ev)
            self.insertPlainText(indent)
            return
        super().keyPressEvent(ev)

    def highlight_line(self, n: int):
        """Select line n (1-based), e.g. where an error happened."""
        block = self.document().findBlockByNumber(n - 1)
        if block.isValid():
            cur = QtGui.QTextCursor(block)
            cur.select(QtGui.QTextCursor.LineUnderCursor)
            self.setTextCursor(cur)
            self.setFocus()


class MathDialog(ToolDialog):
    title = "Math"
    subtitle = ("Each image is a letter: a, b, c ... Write Python below (one line or many); the "
                "result is saved on the voxel grid of the reference image.")
    run_label = "Compute"

    def build(self, lay):
        box, self.flist = file_list_with_buttons(self.files, letters=True)
        lay.addWidget(box)
        self.flist.changed.connect(self._check)

        self.code = CodeEdit()
        self.code.setPlaceholderText("# e.g.\n(a - b) / b * 100\n\n# or several lines:\n"
                                     "mask = b > 0\nresult = a * mask")
        self.code.setPlainText(self.settings.get("math_code", ""))
        self.code.setMinimumHeight(220)
        self.code.textChanged.connect(self._check)
        self.code.run_requested.connect(self.start)
        lay.addWidget(self.code, 1)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(hint("Last line or  result = ...  is saved  |  save(array, 'name') writes more "
                           "files  |  print() goes to the log  |  Ctrl+Enter runs"), 1)
        more = QtWidgets.QToolButton(text="?")
        more.setToolTip(AVAILABLE_TEXT)
        more.clicked.connect(lambda: QtWidgets.QMessageBox.information(self, "Available in your code",
                                                                       AVAILABLE_TEXT))
        row.addWidget(more)
        ex = QtWidgets.QToolButton(text="Examples")
        ex.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(ex)
        for e, desc in mathexpr.EXAMPLES:  # one-liners
            act = menu.addAction(f"{e}    -  {desc}")
            act.triggered.connect(lambda _=False, e=e, d=desc: self.code.setPlainText(f"# {d}\n{e}\n"))
        menu.addSeparator()
        for title, snippet in mathexpr.CODE_EXAMPLES:  # several lines
            act = menu.addAction(title)
            act.triggered.connect(lambda _=False, c=snippet: self.code.setPlainText(c))
        ex.setMenu(menu)
        row.addWidget(ex)
        lay.addLayout(row)
        lay.addWidget(hint("The code runs as normal Python on your computer: only run code you trust."))

        form = QtWidgets.QFormLayout()
        self.check_label = QtWidgets.QLabel(" ")
        self.check_label.setWordWrap(True)
        form.addRow("", self.check_label)
        self.space = Banner("info", "")

        first = self.files[0] if self.files else os.path.expanduser("~/x.nii.gz")
        self.out = PathEdit(os.path.join(os.path.dirname(first), "result.nii.gz"), save=True)
        form.addRow("Save as", self.out)
        self.ref = QtWidgets.QComboBox()
        form.addRow("Reference grid", self.ref)
        self.ref.currentIndexChanged.connect(lambda *_: self._check())
        self.dtype = QtWidgets.QComboBox()
        self.dtype.addItems(DTYPES)
        form.addRow("Output type", self.dtype)
        lay.addLayout(form)
        lay.addWidget(self.space)
        self._refill_ref()
        self._check()
        self.resize(680, 820)

    def _refill_ref(self):
        cur = self.ref.currentIndex()
        self.ref.blockSignals(True)  # refilling must not re-trigger _check
        self.ref.clear()
        for i, p in enumerate(self.flist.paths()[:26]):
            self.ref.addItem(f"{chr(97 + i)}  ({os.path.basename(p)})")
        self.ref.setCurrentIndex(max(0, min(cur, self.ref.count() - 1)))
        self.ref.blockSignals(False)

    def _check(self):
        if self.ref.count() != self.flist.count():
            self._refill_ref()
        text = self.code.toPlainText()
        try:
            _, used, sets_result = mathexpr.prepare_code(text, self.flist.count())
            msg = "Syntax OK" + (f" - uses {', '.join(used)}" if used else "")
            if not sets_result:
                msg += " - no result: only save(...) calls will write files"
            self._check_grids(used)
            self.check_label.setText(msg)
            self.check_label.setStyleSheet(f"color: {theme.ok_color()};")
            return True
        except mathexpr.ExprError as e:
            self.check_label.setText(str(e) if text.strip() else " ")
            self.check_label.setStyleSheet(f"color: {theme.error_color()};")
            return False

    def _check_grids(self, used):
        """Warn when the images in the expression are not on the reference image's voxel grid."""
        paths = self.flist.paths()
        ref_i = max(self.ref.currentIndex(), 0)
        key = (tuple(paths), tuple(used), ref_i)
        if key == getattr(self, "_grid_key", None):
            return
        self._grid_key = key
        ref = io.load(paths[ref_i])
        bad = []
        for letter in used:
            i = ord(letter) - 97
            if i != ref_i:
                r = space.compare(ref, io.load(paths[i]))
                if not r.same_grid:
                    bad.append(f"{letter} ({os.path.basename(paths[i])}): " + "; ".join(r.issues[:3]))
        if bad:
            self.space.set("warning", f"Not on the voxel grid of the reference ({chr(97 + ref_i)}): "
                                      "voxels would be combined by index, not by location. Resample first.",
                           bad)
        else:
            self.space.set("info", "")

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

    def start(self):
        if self.panel.running:
            return
        s = settings.load()  # remember the code for next time
        s["math_code"] = self.settings["math_code"] = self.code.toPlainText()
        settings.save(s)
        super().start()

    def task(self, report):
        res = mathexpr.run_code(self.code.toPlainText(), self.flist.paths(), self.out.path(),
                                dtype=DTYPES[self.dtype.currentText()], ref_index=self.ref.currentIndex(),
                                progress=report.progress, log=report.log)
        if res["value"] is not None:
            extra = f"; {len(res['files'])} file(s) saved" if res["files"] else "   (a single number, nothing saved)"
            return _value_text(res["value"]) + extra
        return res["files"]

    def _ok(self, result):
        super()._ok(result)
        if self.panel.log.toPlainText().strip() and not self.panel.log.isVisible():
            self.panel.toggle_log()  # show what the code printed

    def _failed(self, msg, tb):
        super()._failed(msg, tb)
        m = re.match(r"line (\d+):", msg)
        if m:
            self.code.highlight_line(int(m.group(1)))


def _value_text(value) -> str:
    try:
        return f"Result: {value:.8g}"
    except (TypeError, ValueError):
        return f"Result: {value}"


def run(files):
    return run_dialog(MathDialog, files)
