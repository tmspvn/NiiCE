"""Install the NiiCE right-click menu into file managers.

Linux:   Nautilus (GNOME Files) and Nemo (Cinnamon): a top-level "NiiCE" menu through a small
         nautilus-python / nemo-python extension when that plugin is installed (package
         python3-nautilus / python-nemo), otherwise Scripts > NiiCE. Caja (MATE): Scripts > NiiCE.
         Dolphin (KDE): service menu.
Windows: Explorer cascading menu for .nii and .gz files (current user, no admin rights).
macOS:   not automated; see README (Automator Quick Action calling `niice run <tool> "$@"`).

Every entry runs `<python> -m niice run <tool-id> <selected files>`, with the Python that ran
the installer, so virtual environments and conda keep working.
"""
from __future__ import annotations

import glob
import os
import shutil
import stat
import subprocess
import sys
from typing import Sequence

MARKER = ".niice-generated"
MENU_NAME = "NiiCE"

_LINUX_SCRIPT_DIRS = {
    "nautilus": ("Nautilus (GNOME Files)", "~/.local/share/nautilus/scripts", "nautilus"),
    "nemo": ("Nemo (Cinnamon)", "~/.local/share/nemo/scripts", "nemo"),
    "caja": ("Caja (MATE)", "~/.config/caja/scripts", "caja"),
}
_DOLPHIN_DIRS = ("~/.local/share/kio/servicemenus", "~/.local/share/kservices5/ServiceMenus")
# File managers with a Python extension API: name -> (gi module, extension folder, plugin library)
_PY_EXTENSIONS = {
    "nautilus": ("Nautilus", "~/.local/share/nautilus-python/extensions", "libnautilus-python.so"),
    "nemo": ("Nemo", "~/.local/share/nemo-python/extensions", "libnemo-python.so"),
}
EXTENSION_FILE = "niice_menu.py"
install_notes: dict[str, str] = {}  # name -> how the menu was installed (filled by install())


def python_exe(gui: bool = False) -> str:
    exe = sys.executable
    if gui and sys.platform.startswith("win"):
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe


def targets() -> dict[str, tuple[str, bool]]:
    """Available integrations: name -> (label, detected on this system)."""
    if sys.platform.startswith("win"):
        return {"explorer": ("Windows Explorer", True)}
    if sys.platform == "darwin":
        return {}
    out = {}
    for name, (label, _, exe) in _LINUX_SCRIPT_DIRS.items():
        out[name] = (label, shutil.which(exe) is not None)
    out["dolphin"] = ("Dolphin (KDE)", shutil.which("dolphin") is not None)
    return out


def python_extension_available(name: str) -> bool:
    """Is the nautilus-python / nemo-python plugin installed (so a top-level menu is possible)?"""
    if name not in _PY_EXTENSIONS:
        return False
    lib = _PY_EXTENSIONS[name][2]
    patterns = [f"/usr/lib*/{name}/extensions-*/{lib}", f"/usr/lib/*/{name}/extensions-*/{lib}",
                f"/usr/local/lib*/{name}/extensions-*/{lib}", f"/usr/lib*/*/{name}/extensions-*/{lib}"]
    return any(glob.glob(p) for p in patterns)


def install(names: Sequence[str], tools, use_extension=None) -> list[str]:
    """(Re)write menu entries for `tools` into each integration in `names`.

    use_extension: None = top-level menu via nautilus-/nemo-python when available, True/False to force.
    """
    done = []
    install_notes.clear()
    for name in names:
        ext = python_extension_available(name) if use_extension is None else bool(use_extension)
        if name in _PY_EXTENSIONS and ext:
            _remove_scripts(os.path.expanduser(_LINUX_SCRIPT_DIRS[name][1]))  # no duplicate menu
            _install_extension(name, tools)
            install_notes[name] = "top-level NiiCE menu"
        elif name in _LINUX_SCRIPT_DIRS:
            _remove_extension(name)
            _install_scripts(os.path.expanduser(_LINUX_SCRIPT_DIRS[name][1]), tools)
            install_notes[name] = "Scripts > NiiCE"
            if name in _PY_EXTENSIONS:
                pkg = "python3-nautilus" if name == "nautilus" else "python-nemo"
                install_notes[name] += (f" (for a top-level menu install {pkg}, e.g. "
                                        f"'sudo apt install {pkg}', then run 'niice install' again)")
        elif name == "dolphin":
            _install_dolphin(tools)
        elif name == "explorer":
            from . import windows
            windows.install(tools, python_exe(gui=True))
        else:
            continue
        done.append(name)
    return done


def uninstall(names: Sequence[str]) -> None:
    for name in names:
        if name in _LINUX_SCRIPT_DIRS:
            _remove_scripts(os.path.expanduser(_LINUX_SCRIPT_DIRS[name][1]))
            _remove_extension(name)
        elif name == "dolphin":
            for d in _DOLPHIN_DIRS:
                p = os.path.join(os.path.expanduser(d), "niice.desktop")
                if os.path.exists(p):
                    os.remove(p)
        elif name == "explorer":
            from . import windows
            windows.uninstall()


# --------------------------------------------------------------------------- Nautilus & co
SCRIPT = """#!/bin/sh
# Generated by NiiCE ({tool_id}). Regenerate with: niice install
exec "{python}" -m niice run {tool_id} "$@"
"""


def _safe(name: str) -> str:
    return name.replace(os.sep, "-").strip() or "_"


def _remove_scripts(base: str) -> None:
    root = os.path.join(base, MENU_NAME)
    if os.path.isdir(root):
        if not os.path.exists(os.path.join(root, MARKER)):
            raise RuntimeError(f"{root} exists but was not created by NiiCE; not touching it")
        shutil.rmtree(root)


def _install_scripts(base: str, tools) -> None:
    _remove_scripts(base)
    root = os.path.join(base, MENU_NAME)
    os.makedirs(root, exist_ok=True)
    open(os.path.join(root, MARKER), "w").close()
    for t in tools:
        parts = [_safe(p) for p in t.menu.split("/")]
        folder = os.path.join(root, *parts[:-1])
        os.makedirs(folder, exist_ok=True)
        label = parts[-1] + ("..." if t.id == "settings" else "")
        path = os.path.join(folder, label)
        with open(path, "w") as f:
            f.write(SCRIPT.format(python=python_exe(), tool_id=t.id))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --------------------------------------------------------------------------- nautilus-/nemo-python
EXTENSION = '''# Generated by NiiCE: top-level "NiiCE" right-click menu. Regenerate with: niice install
# Runs inside the file manager (system Python), so it only launches NiiCE's own Python.
import subprocess

import gi

for _version in ("4.0", "3.0"):
    try:
        gi.require_version("{module}", _version)
        break
    except ValueError:
        pass
from gi.repository import GObject, {module}  # noqa: E402

PYTHON = {python!r}
TOOLS = {tools!r}  # (tool id, menu path)
NIFTI = (".nii", ".nii.gz")


def _paths(files):
    out = []
    for f in files:
        loc = f.get_location()
        path = loc.get_path() if loc is not None else None
        if path:
            out.append(path)
    return out


class NiiCEMenu(GObject.GObject, {module}.MenuProvider):
    def get_file_items(self, *args):  # (files) in Nautilus 4, (window, files) before
        paths = _paths(args[-1])
        if not any(p.lower().endswith(NIFTI) for p in paths):
            return []
        top = {module}.MenuItem(name="NiiCE::menu", label="NiiCE", tip="NIfTI tools")
        root = {module}.Menu()
        top.set_submenu(root)
        submenus = {{}}
        for tool_id, menu in TOOLS:
            parts = menu.split("/")
            parent, key = root, ""
            for cat in parts[:-1]:
                key += "/" + cat
                if key not in submenus:
                    item = {module}.MenuItem(name="NiiCE::" + key, label=cat, tip="")
                    sub = {module}.Menu()
                    item.set_submenu(sub)
                    parent.append_item(item)
                    submenus[key] = sub
                parent = submenus[key]
            label = parts[-1] + ("..." if tool_id == "settings" else "")
            item = {module}.MenuItem(name="NiiCE::" + tool_id, label=label, tip="")
            item.connect("activate", self._run, tool_id, paths)
            parent.append_item(item)
        return [top]

    def get_background_items(self, *args):
        return []

    def _run(self, _item, tool_id, paths):
        subprocess.Popen([PYTHON, "-m", "niice", "run", tool_id] + paths, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''


def _extension_path(name: str) -> str:
    return os.path.join(os.path.expanduser(_PY_EXTENSIONS[name][1]), EXTENSION_FILE)


def _install_extension(name: str, tools) -> str:
    # Settings goes last in the menu; categories keep their order
    ordered = [t for t in tools if t.id != "settings"] + [t for t in tools if t.id == "settings"]
    text = EXTENSION.format(module=_PY_EXTENSIONS[name][0], python=python_exe(),
                            tools=[(t.id, t.menu) for t in ordered])
    path = _extension_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def _remove_extension(name: str) -> None:
    if name in _PY_EXTENSIONS and os.path.exists(_extension_path(name)):
        os.remove(_extension_path(name))


# --------------------------------------------------------------------------- Dolphin
def _install_dolphin(tools) -> None:
    folder = os.path.expanduser(_DOLPHIN_DIRS[0])
    os.makedirs(folder, exist_ok=True)
    _install_mime()
    actions = []
    body = []
    for i, t in enumerate(tools):
        key = f"niice{i}"
        actions.append(key)
        name = t.menu.replace("/", ": ")
        body.append(f"[Desktop Action {key}]\nName={name}\nIcon=applications-science\n"
                    f'Exec="{python_exe()}" -m niice run {t.id} %F\n')
    text = ("[Desktop Entry]\nType=Service\nX-KDE-ServiceTypes=KonqPopupMenu/Plugin\n"
            "MimeType=application/x-nifti;application/x-nifti-gz;\n"
            f"Actions={';'.join(actions)}\nX-KDE-Submenu={MENU_NAME}\n\n" + "\n".join(body))
    path = os.path.join(folder, "niice.desktop")
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


MIME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-nifti">
    <comment>NIfTI image</comment>
    <glob pattern="*.nii"/>
  </mime-type>
  <mime-type type="application/x-nifti-gz">
    <comment>Compressed NIfTI image</comment>
    <sub-class-of type="application/gzip"/>
    <glob pattern="*.nii.gz" weight="60"/>
  </mime-type>
</mime-info>
"""


def _install_mime() -> None:
    base = os.path.expanduser("~/.local/share/mime")
    os.makedirs(os.path.join(base, "packages"), exist_ok=True)
    with open(os.path.join(base, "packages", "niice.xml"), "w") as f:
        f.write(MIME_XML)
    if shutil.which("update-mime-database"):
        subprocess.run(["update-mime-database", base], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
