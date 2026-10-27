"""Command line: `niice run <tool> files...`, `niice install`, `niice settings`, ..."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import traceback

from . import __version__, plugins, settings
from .core import io

USAGE = """niice <command> [options]

  niice run TOOL FILES...     open a tool (this is what the context menu calls)
  niice TOOL FILES...         same, shorter (e.g. niice info a.nii.gz)
  niice list                  list tools and whether they are enabled
  niice settings              open the settings window
  niice install [--targets nautilus nemo caja dolphin explorer]
  niice uninstall             remove the context-menu entries
  niice info --text FILES...  print header info in the terminal
  niice demo [FOLDER]         write small example images to try the tools on
  niice synthstrip-weights    download the SynthStrip model weights (python backend)
"""


def _notify_failure(title: str, text: str) -> None:
    """Show an error even when the GUI could not start (e.g. PySide6 missing)."""
    print(f"{title}: {text}", file=sys.stderr)
    try:
        from .gui.common import error
        error(title, text.splitlines()[0], text)
        return
    except Exception:
        pass
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", f"NiiCE: {title}", text[:300]], check=False)


def _hint(tb: str) -> str:
    """Plain-language explanation for errors caused by the installation, not by NiiCE."""
    if "GLIBCXX" in tb or "libstdc++" in tb:
        return ("Qt could not load: NiiCE's Python environment uses a C++ library (libstdc++) that is too "
                "old, which is common when it was built from Anaconda's Python. Reinstall with the system "
                "Python: ./install.sh --uninstall && ./install.sh --python /usr/bin/python3")
    if "No module named 'PySide6'" in tb:
        return "Qt for Python (PySide6) is not installed in NiiCE's environment: run ./install.sh again."
    if "xcb" in tb and "plugin" in tb:
        return ("Qt could not start its window system plugin. On Ubuntu/Debian: "
                "sudo apt install libxcb-cursor0")
    return ""


def run_tool(tool_id: str, files: list[str], collect: bool = False) -> int:
    if collect and files:
        from .integration.collect import collect as do_collect
        merged = do_collect(tool_id, files[0])
        if merged is None:
            return 0
        files = merged
    t = plugins.get_tool(tool_id)
    if t is None:
        _notify_failure("Unknown tool", f"No tool with id '{tool_id}'. Run 'niice list'.")
        return 2
    s = settings.load()
    if not t.always_on and t.id in s.get("disabled", []):
        _notify_failure("Tool disabled", f"'{t.menu}' is disabled in NiiCE settings.")
        return 2
    files = [os.path.abspath(f) for f in files]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        _notify_failure("File not found", "These files do not exist:\n" + "\n".join(missing)
                        + "\n\nTip: 'niice demo' writes example images you can try the tools on.")
        return 2
    if t.nifti_only:
        skipped = [f for f in files if not io.is_nifti(f)]
        files = [f for f in files if io.is_nifti(f)]
    else:
        skipped = []
    if len(files) < t.min_files:
        msg = f"'{t.label}' needs at least {t.min_files} NIfTI file(s)."
        if skipped:
            msg += "\nNot NIfTI: " + ", ".join(os.path.basename(f) for f in skipped)
        _notify_failure("Not enough files", msg)
        return 2
    if t.max_files is not None and len(files) > t.max_files:
        _notify_failure("Too many files", f"'{t.label}' takes at most {t.max_files} file(s); "
                                          f"{len(files)} selected.")
        return 2
    try:
        t.callable()(files)
    except Exception:
        tb = traceback.format_exc()
        hint = _hint(tb)
        _notify_failure(f"{t.label} failed", (hint + "\n\n" if hint else "") + tb)
        return 1
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print(__version__)
        return 0
    cmd = argv[0]
    if cmd == "run":
        p = argparse.ArgumentParser(prog="niice run")
        p.add_argument("--collect", action="store_true", help=argparse.SUPPRESS)
        p.add_argument("tool")
        p.add_argument("files", nargs="*")
        a = p.parse_args(argv[1:])
        return run_tool(a.tool, a.files, a.collect)
    if cmd == "list":
        enabled = {t.id for t in plugins.enabled_tools()}
        for t in plugins.all_tools():
            flag = "on " if t.id in enabled else "off"
            miss = t.missing_requirements()
            extra = f"  [needs {', '.join(miss)}]" if miss else ""
            print(f"  [{flag}] {t.id:<20} {t.menu:<40} {t.source}{extra}")
        for src, tb in plugins.LOAD_ERRORS:
            print(f"\nPlugin error in {src}:\n{tb}", file=sys.stderr)
        return 0
    if cmd == "settings":
        return run_tool("settings", [])
    if cmd in ("install", "uninstall"):
        from .integration import menus
        p = argparse.ArgumentParser(prog=f"niice {cmd}")
        p.add_argument("--targets", nargs="*", help="default: detected file managers")
        a = p.parse_args(argv[1:])
        avail = menus.targets()
        names = a.targets or ([n for n, (_, found) in avail.items() if found] if cmd == "install"
                              else list(avail))
        unknown = [n for n in names if n not in avail]
        if unknown:
            print(f"Unknown target(s) {unknown}; available: {list(avail)}", file=sys.stderr)
            return 2
        s = settings.load()
        if cmd == "install":
            if not names:
                print("No supported file manager found. Use --targets to force one, e.g. "
                      "--targets nautilus", file=sys.stderr)
                return 1
            done = menus.install(names, plugins.enabled_tools(s))
            s["integrations"] = sorted(set(s.get("integrations", [])) | set(done))
            print("Installed the NiiCE menu:")
            for n in done:
                print(f"  {n}: {menus.install_notes.get(n, 'installed')}")
            if "nautilus" in done:
                print("Restart Nautilus to see it:  nautilus -q")
            if "nemo" in done:
                print("Restart Nemo to see it:  nemo -q")
            if "thunar" in done:
                print("Restart Thunar to see it:  thunar -q")
        else:
            menus.uninstall(names)
            s["integrations"] = [n for n in s.get("integrations", []) if n not in names]
            print("Removed NiiCE menu from: " + ", ".join(names))
        settings.save(s)
        return 0
    if cmd == "synthstrip-weights":
        from .core import synthstrip
        for no_csf in (False, True):
            name = synthstrip.WEIGHTS[no_csf][0]
            have = synthstrip.find_weights(no_csf)
            if have:
                print(f"{name}: already present ({have})")
                continue
            print(f"{name}: downloading...", flush=True)
            path = synthstrip.download_weights(
                no_csf, lambda f, m: print(f"\r  {100 * f:5.1f}%", end="", flush=True))
            print(f"\n  saved and verified: {path}")
        if not synthstrip.python_backend_installed():
            print("\nNote: nipreps-synthstrip/torch are not installed in this Python yet:\n"
                  "  pip install torch --index-url https://download.pytorch.org/whl/cpu\n"
                  "  pip install nipreps-synthstrip")
        return 0
    if cmd == "demo":
        from .core.demo import write_demo
        folder = os.path.abspath(argv[1] if len(argv) > 1 else "niice_demo")
        for path, what in write_demo(folder):
            print(f"  {os.path.basename(path):<24} {what}")
        print(f"\nWritten to {folder}. Try for example:\n"
              f"  cd {folder}\n"
              "  niice info T1w.nii.gz T2w_shifted.nii.gz\n"
              "  niice alignment T1w.nii.gz T2w_shifted.nii.gz\n"
              "  niice math T1w.nii.gz labels.nii.gz\n"
              "  niice gif bold.nii.gz")
        return 0
    if cmd == "info" and "--text" in argv:
        from .core.info import text_report
        files = [f for f in argv[1:] if f != "--text"]
        if not files:
            print("niice info --text FILES...", file=sys.stderr)
            return 2
        print(text_report(files))
        return 0
    # Shortcut: niice <tool-id or last part of it> files...
    tools = plugins.load_all()
    match = cmd if cmd in tools else next((i for i in tools if i.split(".")[-1] == cmd), None)
    if match:
        return run_tool(match, argv[1:])
    print(f"Unknown command '{cmd}'.\n\n{USAGE}", file=sys.stderr)
    return 2
