"""Run external programs while streaming their output line by line, with cancellation."""
from __future__ import annotations

import os
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


def open_in_file_manager(path: str) -> None:
    """Open a folder (or the folder containing a file) in the system file manager."""
    folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    if sys.platform.startswith("win"):
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


def open_file(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
