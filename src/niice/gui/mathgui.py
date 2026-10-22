"""Math: numpy expressions or multi-line Python code between images."""
from __future__ import annotations

import os
import re

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import io, mathexpr, space
from .. import settings
from .common import Banner, PathEdit, ToolDialog, ask, file_list_with_buttons, hint, run_dialog

Qt = QtCore.Qt

DTYPES = {"automatic": None, "float32": np.float32, "int16": np.int16, "int32": np.int32,
          "uint8 (masks)": np.uint8}


class CodeEdit(QtWidgets.QPlainTextEdit):
    """Plain-text code editor: line numbers, Tab = 4 spaces, auto-indent, Ctrl+Enter = run."""
    run_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setPointSize(font.pointSize() + 1)
        self.setFont(font)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
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
        p.fillRect(ev.rect(), self.palette().alternateBase())
        p.setPen(self.palette().color(QtGui.QPalette.PlaceholderText))
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
    subtitle = ("Each image is a letter: a, b, c ... Write a one-line numpy expression, or switch to "
                "Code for several lines of Python. The result is saved on the voxel grid of the "
                "reference image.")
    run_label = "Compute"

    def build(self, lay):
        box, self.flist = file_list_with_buttons(self.files, letters=True)
        lay.addWidget(box)
        self.flist.changed.connect(self._check)

        self.tabs = QtWidgets.QTabWidget()
        # --- one-line expression
        etab = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(etab)
        self.expr = QtWidgets.QLineEdit()
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        mono.setPointSize(mono.pointSize() + 2)
        self.expr.setFont(mono)
        self.expr.setPlaceholderText("e.g.  (a - b) / b * 100")
        self.expr.textChanged.connect(self._check)
        self.expr.returnPressed.connect(self.start)
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
        el.addLayout(row)
        helpers = ", ".join(sorted(mathexpr.HELPERS))
        el.addWidget(hint(
            "Operators: + - * / ** % and comparisons > < >= <= == !=. Combine masks with & | ~ "
            "(not 'and'/'or'). Any np.<function> is allowed; these also work without 'np.': "
            f"{helpers}. Constants: pi, e, nan, inf. A result that is a single number "
            "(e.g. np.mean(a)) is shown instead of saved."))
        el.addStretch(1)
        self.tabs.addTab(etab, "Expression")
        # --- multi-line code
        ctab = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(ctab)
        self.code = CodeEdit()
        self.code.setPlaceholderText("# paste or write Python here, e.g.\nmask = b > 0\nresult = a * mask")
        self.code.setPlainText(self.settings.get("math_code", ""))
        self.code.setMinimumHeight(200)
        self.code.textChanged.connect(self._check)
        self.code.run_requested.connect(self.start)
        cl.addWidget(self.code, 1)
        crow = QtWidgets.QHBoxLayout()
        cex = QtWidgets.QToolButton(text="Examples")
        cex.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        cmenu = QtWidgets.QMenu(cex)
        for title, snippet in mathexpr.CODE_EXAMPLES:
            act = cmenu.addAction(title)
            act.triggered.connect(lambda _=False, c=snippet: self.code.setPlainText(c))
        cex.setMenu(cmenu)
        crow.addWidget(hint("result = ... is saved  |  save(array, 'name') writes more files  |  "
                            "print() goes to the log  |  Ctrl+Enter runs"), 1)
        more = QtWidgets.QToolButton(text="?")
        more.setToolTip(mathexpr.CODE_HELP)
        more.clicked.connect(lambda: QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), mathexpr.CODE_HELP, more))
        crow.addWidget(more)
        crow.addWidget(cex)
        cl.addLayout(crow)
        self.tabs.addTab(ctab, "Code (multi-line)")
        self.tabs.setCurrentIndex(1 if self.settings.get("math_mode") == "code" else 0)
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(hint("Code runs as normal Python on your computer (the one-line Expression is "
                           "restricted to array maths): only run code you trust."))

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

    def code_mode(self) -> bool:
        return self.tabs.currentIndex() == 1

    def _tab_changed(self, i):
        if i == 1 and not self.code.toPlainText().strip() and self.expr.text().strip():
            self.code.setPlainText(f"result = {self.expr.text().strip()}\n")
        self._check()

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
        text = self.code.toPlainText() if self.code_mode() else self.expr.text()
        try:
            if self.code_mode():
                _, used, sets_result = mathexpr.prepare_code(text, self.flist.count())
                msg = "Syntax OK" + (f" - uses {', '.join(used)}" if used else "")
                if not sets_result:
                    msg += " - no result set: only save(...) calls will write files"
            else:
                used = mathexpr.validate(text, self.flist.count())
                msg = "OK - uses " + ", ".join(used)
            self._check_grids(used)
            self.check_label.setText(msg)
            self.check_label.setStyleSheet("color: #2e7d32;")
            return True
        except mathexpr.ExprError as e:
            self.check_label.setText(str(e) if text.strip() else " ")
            self.check_label.setStyleSheet("color: #c62828;")
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
        if self.code_mode():  # remember the code for next time
            self.settings["math_code"] = self.code.toPlainText()
        self.settings["math_mode"] = "code" if self.code_mode() else "expression"
        s = settings.load()
        s.update({k: self.settings[k] for k in ("math_code", "math_mode") if k in self.settings})
        settings.save(s)
        super().start()

    def task(self, report):
        kw = dict(dtype=DTYPES[self.dtype.currentText()], ref_index=self.ref.currentIndex(),
                  progress=report.progress)
        if self.code_mode():
            res = mathexpr.run_code(self.code.toPlainText(), self.flist.paths(), self.out.path(),
                                    log=report.log, **kw)
            if res["value"] is not None:
                extra = f"; {len(res['files'])} file(s) saved" if res["files"] else ", nothing saved"
                return _value_text(res["value"]) + extra
            return res["files"]
        kind, value = mathexpr.run(self.expr.text(), self.flist.paths(), self.out.path(), **kw)
        if kind == "value":
            return _value_text(value) + "   (a single number, nothing saved)"
        return [value]

    def _ok(self, result):
        super()._ok(result)
        if self.code_mode() and self.panel.log.toPlainText().strip() and not self.panel.log.isVisible():
            self.panel.toggle_log()  # show what the code printed

    def _failed(self, msg, tb):
        super()._failed(msg, tb)
        m = re.match(r"line (\d+):", msg)
        if self.code_mode() and m:
            self.code.highlight_line(int(m.group(1)))


def _value_text(value) -> str:
    try:
        return f"Result: {value:.8g}"
    except (TypeError, ValueError):
        return f"Result: {value}"


def run(files):
    return run_dialog(MathDialog, files)
