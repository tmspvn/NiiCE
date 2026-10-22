"""Info: image properties and header viewer; header comparison for several images."""
from __future__ import annotations

import csv
import os

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import info as core
from ..core.io import split_ext
from .common import Report, Thumbs, Worker, app, hint, show_modal

Qt = QtCore.Qt
DIFF_COLOR = QtGui.QColor(255, 193, 7, 90)


def _table(headers, rows, stretch_last=True) -> QtWidgets.QTableWidget:
    t = QtWidgets.QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    t.setWordWrap(True)
    mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            it = QtWidgets.QTableWidgetItem(str(val))
            if c > 0:
                it.setFont(mono)
            t.setItem(r, c, it)
    t.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
    if stretch_last:
        t.horizontalHeader().setStretchLastSection(True)
    t.resizeRowsToContents()
    return t


def _copy_table(t: QtWidgets.QTableWidget):
    lines = ["\t".join(t.horizontalHeaderItem(c).text() for c in range(t.columnCount()))]
    for r in range(t.rowCount()):
        if t.isRowHidden(r):
            continue
        lines.append("\t".join((t.item(r, c).text() if t.item(r, c) else "").replace("\n", " ")
                               for c in range(t.columnCount())))
    QtWidgets.QApplication.clipboard().setText("\n".join(lines))


def _export_table(parent, t: QtWidgets.QTableWidget, default: str):
    path, _ = QtWidgets.QFileDialog.getSaveFileName(parent, "Export table", default, "CSV (*.csv)")
    if not path:
        return
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([t.horizontalHeaderItem(c).text() for c in range(t.columnCount())])
        for r in range(t.rowCount()):
            if not t.isRowHidden(r):
                w.writerow([t.item(r, c).text() if t.item(r, c) else "" for c in range(t.columnCount())])


class InfoWindow(QtWidgets.QDialog):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"NiiCE - Info - {os.path.basename(path)}")
        self.resize(820, 680)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(os.path.basename(path), objectName="title")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(title, 1)
        top.addWidget(Thumbs(path, 96))
        lay.addLayout(top)

        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs, 1)
        summ = core.summary(path)
        self.summary_rows = list(summ.items())
        self.summary = _table(["Property", "Value"], self.summary_rows)
        tabs.addTab(self.summary, "Summary")

        hdr = core.header_fields(core.load(path))
        rows = [(k, core.fmt(v), core.FIELD_HELP.get(k, "")) for k, v in hdr.items()]
        self.header = _table(["Field", "Value", "Meaning"], rows)
        tabs.addTab(self.header, "Full header")
        self.tabs = tabs

        row = QtWidgets.QHBoxLayout()
        self.stats_label = hint("Computing intensity statistics...")
        row.addWidget(self.stats_label, 1)
        copy = QtWidgets.QPushButton("Copy")
        copy.clicked.connect(lambda: _copy_table(self.tabs.currentWidget()))
        exp = QtWidgets.QPushButton("Export CSV...")
        exp.clicked.connect(lambda: _export_table(self, self.tabs.currentWidget(),
                                                  split_ext(path)[0] + "_info.csv"))
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(copy)
        row.addWidget(exp)
        row.addWidget(close)
        lay.addLayout(row)

        # Statistics need to read all voxels: do it in the background
        self._report = Report()
        self._worker = Worker(lambda r: core.intensity_stats(path), self._report)
        self._worker.succeeded.connect(self._add_stats)
        self._worker.failed.connect(lambda m, tb: self.stats_label.setText(f"Statistics failed: {m}"))
        self._worker.start()

    def _add_stats(self, stats):
        t = self.summary
        for k, v in stats.items():
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QtWidgets.QTableWidgetItem(k))
            it = QtWidgets.QTableWidgetItem(v)
            it.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            t.setItem(r, 1, it)
        t.resizeRowsToContents()
        self.stats_label.setText("")


class CompareWindow(QtWidgets.QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.setWindowTitle(f"NiiCE - Compare {len(paths)} headers")
        self.resize(min(1400, 380 + 260 * len(paths)), 720)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(f"Comparing {len(paths)} images", objectName="title"))

        thumbs = QtWidgets.QHBoxLayout()
        for p in paths[:6]:
            box = QtWidgets.QVBoxLayout()
            box.addWidget(Thumbs(p, 64))
            lbl = QtWidgets.QLabel(os.path.basename(p))
            lbl.setObjectName("hint")
            box.addWidget(lbl)
            thumbs.addLayout(box)
        thumbs.addStretch(1)
        lay.addLayout(thumbs)

        rows, differs = core.compare(paths)
        self.differs = differs
        names = [os.path.basename(p) for p in paths]
        self.table = _table(["Field"] + names, [(f, *vals) for f, vals in rows], stretch_last=False)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.table.setColumnWidth(0, 170)
        for c in range(1, len(names) + 1):
            self.table.setColumnWidth(c, 240)
        for r, d in enumerate(differs):
            if d:
                for c in range(self.table.columnCount()):
                    self.table.item(r, c).setBackground(DIFF_COLOR)
        lay.addWidget(self.table, 1)

        n_diff = sum(differs)
        row = QtWidgets.QHBoxLayout()
        self.only = QtWidgets.QCheckBox(f"Show only the {n_diff} differing fields")
        self.only.setChecked(n_diff > 0)
        self.only.toggled.connect(self._filter)
        row.addWidget(self.only)
        row.addWidget(hint("Highlighted = differs (numbers compared with tolerance 1e-5)."), 1)
        copy = QtWidgets.QPushButton("Copy")
        copy.clicked.connect(lambda: _copy_table(self.table))
        exp = QtWidgets.QPushButton("Export CSV...")
        exp.clicked.connect(lambda: _export_table(self, self.table, os.path.join(
            os.path.dirname(paths[0]), "header_comparison.csv")))
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        for b in (copy, exp, close):
            row.addWidget(b)
        lay.addLayout(row)
        self._filter(self.only.isChecked())

    def _filter(self, only):
        for r, d in enumerate(self.differs):
            self.table.setRowHidden(r, bool(only and not d))
        self.table.resizeRowsToContents()


def run(files):
    app()
    return show_modal(InfoWindow(files[0]) if len(files) == 1 else CompareWindow(files))
