"""Colour themes: System / Light / Dark plus an accent colour, chosen in Settings > General."""
from __future__ import annotations

from PySide6 import QtGui, QtWidgets

DEFAULT_ACCENT = "#1565c0"
THEMES = ("system", "light", "dark")
# name -> colour, shown as swatches in Settings
ACCENTS = {
    "Blue": "#1565c0", "Teal": "#00897b", "Green": "#2e7d32", "Purple": "#6a1b9a",
    "Pink": "#c2185b", "Orange": "#ef6c00", "Red": "#c62828", "Grey": "#546e7a",
}

_DARK = {  # palette role -> colour
    "Window": "#2b2d31", "WindowText": "#e6e6e6", "Base": "#1f2125", "AlternateBase": "#2b2d31",
    "Text": "#e6e6e6", "Button": "#363940", "ButtonText": "#e6e6e6", "ToolTipBase": "#3a3d44",
    "ToolTipText": "#e6e6e6", "PlaceholderText": "#8a8f98", "Light": "#4a4d55", "Midlight": "#3d4047",
    "Mid": "#8a8f98", "Dark": "#1a1b1e", "Shadow": "#101113", "BrightText": "#ff6e6e",
}
_DARK_DISABLED = {"WindowText": "#6f737b", "Text": "#6f737b", "ButtonText": "#6f737b"}

_current = {"theme": "system", "accent": DEFAULT_ACCENT}
_system = {}  # the platform's own style and palette, to return to 'system'


def is_dark() -> bool:
    """True when the window background is dark (Dark theme, or a dark system theme)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return _current["theme"] == "dark"
    return app.palette().color(QtGui.QPalette.Window).lightness() < 128


def accent() -> str:
    return _current["accent"]


def ok_color() -> str:
    return "#66bb6a" if is_dark() else "#2e7d32"


def error_color() -> str:
    return "#ef5350" if is_dark() else "#c62828"


def _valid(colour: str) -> str:
    return colour if QtGui.QColor(colour).isValid() else DEFAULT_ACCENT


def apply(app: QtWidgets.QApplication, theme: str = "system", accent_colour: str = DEFAULT_ACCENT,
          stylesheet: str = "") -> None:
    """Apply a theme and accent colour to the whole application (open windows update at once)."""
    theme = theme if theme in THEMES else "system"
    acc = QtGui.QColor(_valid(accent_colour))
    _current.update(theme=theme, accent=acc.name())
    if not _system:
        _system["style"] = app.style().name()
        _system["palette"] = QtGui.QPalette(app.palette())

    if theme == "system":
        app.setStyle(_system["style"])
        pal = QtGui.QPalette(_system["palette"])
    else:
        app.setStyle("Fusion")  # draws every widget from the palette, so colours apply everywhere
        pal = app.style().standardPalette()
        if theme == "dark":
            for role, colour in _DARK.items():
                pal.setColor(getattr(QtGui.QPalette, role), QtGui.QColor(colour))
            for role, colour in _DARK_DISABLED.items():
                pal.setColor(QtGui.QPalette.Disabled, getattr(QtGui.QPalette, role), QtGui.QColor(colour))
    pal.setColor(QtGui.QPalette.Highlight, acc)
    pal.setColor(QtGui.QPalette.HighlightedText,
                 QtGui.QColor("white" if acc.lightness() < 170 else "black"))
    pal.setColor(QtGui.QPalette.Link, acc.lighter(140) if theme == "dark" else acc)
    app.setPalette(pal)
    app.setStyleSheet(stylesheet.replace("@accent", acc.name()))


def matplotlib_style() -> dict:
    """rcParams so embedded plots match the window colours."""
    if not is_dark():
        return {}
    fg, bg, base = _DARK["WindowText"], _DARK["Window"], _DARK["Base"]
    return {"figure.facecolor": bg, "axes.facecolor": base, "axes.edgecolor": _DARK["Mid"],
            "axes.labelcolor": fg, "text.color": fg, "xtick.color": fg, "ytick.color": fg,
            "grid.color": "#555a63", "legend.facecolor": bg, "legend.edgecolor": _DARK["Mid"],
            "savefig.facecolor": bg}
