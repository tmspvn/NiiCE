"""Settings window: enable/disable tools, plugins, general options, context-menu install."""
from __future__ import annotations

import os

from PySide6 import QtCore, QtWidgets

from .. import plugins, settings
from ..core import synthstrip
from ..core.proc import open_in_file_manager
from ..integration import menus
from .common import app, error, hint, message, show_modal

Qt = QtCore.Qt


class SettingsWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NiiCE - Settings")
        self.resize(780, 620)
        self.s = settings.load()
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("NiiCE settings", objectName="title"))
        self.tabs = QtWidgets.QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tools_tab(), "Tools")
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._plugins_tab(), "Plugins")
        self.tabs.addTab(self._menu_tab(), "Context menu")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(hint(f"Stored in {settings.settings_path()}"), 1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QtWidgets.QPushButton("Save && update menu")
        save.setDefault(True)
        save.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)

    # ------------------------------------------------------------------ tabs
    def _tools_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(hint("Untick the tools you do not want in the right-click menu."))
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Tool", "Source", "Status"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 160)
        self._fill_tree()
        lay.addWidget(self.tree, 1)
        return w

    def _fill_tree(self):
        self.tree.clear()
        disabled = set(self.s.get("disabled", []))
        groups = {}
        for t in plugins.all_tools():
            cat = t.category or "(top level)"
            if cat not in groups:
                g = QtWidgets.QTreeWidgetItem([cat])
                g.setFlags(g.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                self.tree.addTopLevelItem(g)
                groups[cat] = g
            missing = t.missing_requirements()
            status = ("needs " + ", ".join(missing)) if missing else "ok"
            it = QtWidgets.QTreeWidgetItem([t.label, t.source, status])
            it.setToolTip(0, t.description)
            it.setData(0, Qt.UserRole, t.id)
            if t.always_on:
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                it.setText(2, "always shown")
            else:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked if t.id in disabled else Qt.Checked)
            groups[cat].addChild(it)
        self.tree.expandAll()

    def _general_tab(self):
        w = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(w)
        self.ext = QtWidgets.QComboBox()
        self.ext.addItems(["same", ".nii.gz", ".nii"])
        self.ext.setCurrentText(self.s.get("output_ext", "same"))
        form.addRow("Output format", self.ext)
        form.addRow("", hint("'same' keeps the extension of the input file."))
        self.open_folder = QtWidgets.QCheckBox("Open the output folder when a tool finishes")
        self.open_folder.setChecked(bool(self.s.get("open_folder_when_done")))
        form.addRow(self.open_folder)
        self.threads = QtWidgets.QSpinBox()
        self.threads.setRange(1, os.cpu_count() or 1)
        self.threads.setValue(min(int(self.s.get("ants_threads", 3)), self.threads.maximum()))
        form.addRow("ANTs threads (default)", self.threads)
        self.preview = QtWidgets.QSpinBox()
        self.preview.setRange(0, 100000)
        self.preview.setSuffix(" M voxels")
        self.preview.setSpecialValueText("no limit")
        self.preview.setValue(int(self.s.get("preview_max_mvox", 64)))
        form.addRow("Skip previews above", self.preview)
        form.addRow("", hint("Thumbnails, GIF and overlap previews are not drawn for images with more "
                             "voxels than this (the tools still work). Intensity statistics in Info "
                             "wait for a click above 4x this size."))
        self.backend = QtWidgets.QComboBox()
        found = synthstrip.available_backends()
        self.backend.addItem("auto", "auto")
        for b in synthstrip.BACKENDS:
            self.backend.addItem(f"{b}  ({'found' if found[b] else 'not found'})", b)
        idx = self.backend.findData(self.s.get("synthstrip_backend", "auto"))
        self.backend.setCurrentIndex(max(idx, 0))
        form.addRow("SynthStrip backend", self.backend)
        self.image = QtWidgets.QLineEdit(self.s.get("synthstrip_image", synthstrip.DEFAULT_IMAGE))
        form.addRow("SynthStrip image", self.image)
        form.addRow("", hint("Docker/Podman image name, or a .sif file for Apptainer."))
        self.pkexec = QtWidgets.QCheckBox("Run Docker through pkexec (asks for the password graphically)")
        self.pkexec.setChecked(bool(self.s.get("docker_pkexec")))
        form.addRow(self.pkexec)
        return w

    def _plugins_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(QtWidgets.QLabel(f"Plugin folder: <code>{settings.plugins_dir()}</code>"))
        lay.addWidget(hint(
            "Every .py file in this folder is loaded. A plugin is a decorated Python function; "
            "NiiCE builds the dialog for simple image filters. Use 'Create example' to start, "
            "edit the file, then 'Reload'. Installed packages can also add tools through the "
            "'niice.plugins' entry point."))
        row = QtWidgets.QHBoxLayout()
        for name, fn in (("Open folder", self._open_plugins), ("Create example", self._example),
                         ("Reload", self._reload)):
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QtWidgets.QLabel("<b>Load errors</b>"))
        self.errors = QtWidgets.QPlainTextEdit(objectName="log")
        self.errors.setReadOnly(True)
        lay.addWidget(self.errors, 1)
        self._show_errors()
        return w

    def _menu_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(hint("Where to install the NiiCE right-click menu. 'Save & update menu' rewrites "
                           "the entries so they match the enabled tools."))
        self.targets = {}
        installed = set(self.s.get("integrations", []))
        for name, (label, detected) in menus.targets().items():
            c = QtWidgets.QCheckBox(f"{label}" + ("" if detected else "   (not detected)"))
            c.setChecked(name in installed or (not installed and detected))
            lay.addWidget(c)
            self.targets[name] = c
        lay.addStretch(1)
        uninstall = QtWidgets.QPushButton("Remove NiiCE from all menus")
        uninstall.clicked.connect(self._uninstall)
        lay.addWidget(uninstall, 0, Qt.AlignLeft)
        return w

    # ------------------------------------------------------------------ actions
    def _open_plugins(self):
        os.makedirs(settings.plugins_dir(), exist_ok=True)
        open_in_file_manager(settings.plugins_dir())

    def _example(self):
        os.makedirs(settings.plugins_dir(), exist_ok=True)
        path = os.path.join(settings.plugins_dir(), "example_smooth.py")
        if os.path.exists(path):
            message("Example plugin", f"{path} already exists.", self)
        else:
            with open(path, "w") as f:
                f.write(plugins.EXAMPLE_PLUGIN)
            message("Example plugin", f"Created {path}.\nIt adds 'Operations/Gaussian smooth'.", self)
        self._reload()

    def _reload(self):
        self._sync_disabled()
        plugins.load_all(force=True)
        self._fill_tree()
        self._show_errors()

    def _show_errors(self):
        self.errors.setPlainText("\n\n".join(f"[{src}]\n{tb}" for src, tb in plugins.LOAD_ERRORS) or "none")

    def _sync_disabled(self):
        disabled = set(self.s.get("disabled", []))
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            g = root.child(i)
            for j in range(g.childCount()):
                it = g.child(j)
                tid = it.data(0, Qt.UserRole)
                if not it.flags() & Qt.ItemIsUserCheckable:
                    continue
                if it.checkState(0) == Qt.Checked:
                    disabled.discard(tid)
                else:
                    disabled.add(tid)
        self.s["disabled"] = sorted(disabled)

    def _uninstall(self):
        menus.uninstall(list(menus.targets()))
        self.s["integrations"] = []
        settings.save(self.s)
        for c in self.targets.values():
            c.setChecked(False)
        message("Removed", "NiiCE entries were removed from all context menus.", self)

    def save(self):
        self._sync_disabled()
        self.s["output_ext"] = self.ext.currentText()
        self.s["open_folder_when_done"] = self.open_folder.isChecked()
        self.s["ants_threads"] = self.threads.value()
        self.s["preview_max_mvox"] = self.preview.value()
        self.s["synthstrip_backend"] = self.backend.currentData()
        self.s["synthstrip_image"] = self.image.text().strip() or synthstrip.DEFAULT_IMAGE
        self.s["docker_pkexec"] = self.pkexec.isChecked()
        chosen = [n for n, c in self.targets.items() if c.isChecked()]
        removed = [n for n in self.s.get("integrations", []) if n not in chosen]
        self.s["integrations"] = chosen
        settings.save(self.s)
        try:
            if removed:
                menus.uninstall(removed)
            written = menus.install(chosen, plugins.enabled_tools(self.s))
        except Exception as e:
            error("Menu update failed", str(e), parent=self)
            return
        n = len(plugins.enabled_tools(self.s))
        message("Saved", f"Settings saved. {n} tools in the menu"
                + (f" ({', '.join(written)})." if written else ".")
                + "\nNautilus may need a restart (nautilus -q) to show changes.", self)
        self.accept()


def run(files=None):
    app()
    return show_modal(SettingsWindow())
