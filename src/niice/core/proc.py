"""Run external programs while streaming their output line by line, with cancellation."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from typing import Callable, Optional, Sequence


class Cancelled(Exception):
    pass


def run_streaming(cmd: Sequence[str], on_line: Callable[[str], None],
                  cancel: Optional[threading.Event] = None, cwd: Optional[str] = None,
                  env: Optional[dict] = None) -> int:
    """Run `cmd`, call on_line(text) for each output line (stdout+stderr), return the exit code.

    If `cancel` gets set, the whole process group is terminated and Cancelled is raised.
    """
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True  # own process group, so we can kill children too
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, bufsize=1, cwd=cwd,
                            env=env, errors="replace", **kwargs)
    watcher = None
    if cancel is not None:
        def watch():
            while proc.poll() is None:
                if cancel.wait(0.2):
                    _terminate(proc)
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\n"))
    code = proc.wait()
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return code


def _terminate(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            proc.terminate()
    except (ProcessLookupError, OSError):
        pass


def _clean_env() -> dict:
    """Environment for launching desktop programs: without the Qt/library paths NiiCE's own Qt
    (PySide6) may have set, which can confuse other applications."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("QT_", "QML", "PYSIDE")) or key in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH"):
            env.pop(key)
    return env


def open_path(path: str) -> bool:
    """Open a file or folder with the desktop's default application. Returns False if it failed."""
    path = os.path.abspath(path)
    try:  # Qt knows the desktop's preferred way (portals on Flatpak/Snap/Wayland)
        from PySide6 import QtCore, QtGui, QtWidgets
        if QtWidgets.QApplication.instance() is not None:
            if QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path)):
                return True
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    commands = [["open", path]] if sys.platform == "darwin" else [["xdg-open", path], ["gio", "open", path]]
    for cmd in commands:
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, env=_clean_env(), start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
    return False


def open_in_file_manager(path: str) -> bool:
    """Open a folder (or the folder containing a file) in the system file manager."""
    folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    return open_path(folder)


def open_file(path: str) -> bool:
    return open_path(path)
