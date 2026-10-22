"""Tool registry and plugin API.

Every entry of the NiiCE context menu is a `Tool`. The built-in tools are registered in
`niice.builtin` with exactly the same API that plugins use.

Writing a plugin
----------------
Drop a .py file in the plugins folder (see `niice settings` -> Plugins, usually
~/.config/niice/plugins/) or ship a package exposing an entry point in the group
"niice.plugins". Two decorators are available:

1. `image_filter` - a function that maps one image to a new one. NiiCE builds the dialog
   (file list, parameter widgets, output suffix, progress bar) for you:

       from niice.plugins import image_filter, Float

       @image_filter(id="my.smooth", menu="Operations/Gaussian smooth", suffix="_smooth",
                     params={"sigma": Float(1.0, min=0, max=20, help="in voxels")})
       def smooth(data, img, sigma):
           from scipy.ndimage import gaussian_filter
           return gaussian_filter(data, sigma)

   `data` is the float32 voxel array, `img` the nibabel image. Return an array on the same grid
   (saved with the input header) or a nibabel image.

2. `tool` - full control: your function receives the list of selected file paths and can open
   any window it likes (Qt via PySide6, or the helpers in niice.gui.common).

       from niice.plugins import tool

       @tool(id="my.count", menu="Info/Count voxels", min_files=1)
       def count(files):
           from niice.gui.common import message
           message("Voxels", "\\n".join(f"{f}: ..." for f in files))
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Union

from . import settings


# --------------------------------------------------------------------------- parameter types
@dataclass
class Param:
    default: Any = None
    label: Optional[str] = None
    help: str = ""


@dataclass
class Float(Param):
    default: float = 0.0
    min: float = -1e9
    max: float = 1e9
    step: float = 0.1
    decimals: int = 3


@dataclass
class Int(Param):
    default: int = 0
    min: int = -10**9
    max: int = 10**9


@dataclass
class Bool(Param):
    default: bool = False


@dataclass
class Choice(Param):
    choices: Sequence[str] = ()

    def __post_init__(self):
        if self.default is None and self.choices:
            self.default = self.choices[0]


@dataclass
class Text(Param):
    default: str = ""


@dataclass
class File(Param):
    """A path chosen with a file dialog (e.g. a mask or reference image)."""
    default: str = ""
    filter: str = "NIfTI (*.nii *.nii.gz);;All files (*)"


# --------------------------------------------------------------------------- tools
@dataclass
class Tool:
    id: str                      # unique, e.g. "ops.binarise" or "mylab.smooth"
    menu: str                    # menu path, "Category/Label" (one or more levels)
    run: Union[Callable[[list], Any], str]  # callable(files) or "module:function"
    description: str = ""
    min_files: int = 1
    max_files: Optional[int] = None
    nifti_only: bool = True      # drop non-NIfTI files from the selection
    requires: Sequence[str] = () # executables that must be found (shown in Settings)
    source: str = "builtin"
    always_on: bool = False      # cannot be disabled (Settings itself)
    order: int = 100             # position inside its submenu

    @property
    def label(self) -> str:
        return self.menu.split("/")[-1]

    @property
    def category(self) -> str:
        return "/".join(self.menu.split("/")[:-1])

    def callable(self) -> Callable[[list], Any]:
        if isinstance(self.run, str):
            mod, _, fn = self.run.partition(":")
            return getattr(importlib.import_module(mod), fn)
        return self.run

    def missing_requirements(self) -> list[str]:
        from .core.ants import find
        return [r for r in self.requires if find(r) is None]


_REGISTRY: dict[str, Tool] = {}
LOAD_ERRORS: list[tuple[str, str]] = []  # (plugin source, traceback)
_current_source = "builtin"
_loaded = False


def register(t: Tool) -> Tool:
    if t.source == "builtin" and _current_source != "builtin":
        t.source = _current_source
    if t.id in _REGISTRY and _REGISTRY[t.id].source != t.source:
        LOAD_ERRORS.append((t.source, f"Tool id '{t.id}' already used by {_REGISTRY[t.id].source}; skipped"))
        return t
    _REGISTRY[t.id] = t
    return t


def tool(id: str, menu: str, **kwargs):
    """Decorator: register `fn(files)` as a context-menu tool."""
    def deco(fn):
        register(Tool(id=id, menu=menu, run=fn, description=kwargs.pop("description", fn.__doc__ or ""),
                      **kwargs))
        return fn
    return deco


def image_filter(id: str, menu: str, params: Optional[dict] = None, suffix: str = "_out",
                 description: str = "", **kwargs):
    """Decorator: register `fn(data, img, **params) -> array | image` with an automatic dialog."""
    def deco(fn):
        spec = dict(params or {})
        desc = description or (fn.__doc__ or "").strip()

        def run(files):
            from .gui.auto import run_image_filter
            return run_image_filter(files, fn, spec, suffix=suffix, title=menu.split("/")[-1],
                                    description=desc)

        register(Tool(id=id, menu=menu, run=run, description=desc, **kwargs))
        fn.niice_params = spec
        return fn
    return deco


# --------------------------------------------------------------------------- discovery
def load_all(force: bool = False) -> dict[str, Tool]:
    """Import built-ins, user plugin files and entry-point plugins (once)."""
    global _loaded, _current_source
    if _loaded and not force:
        return _REGISTRY
    if force:
        _REGISTRY.clear()
        LOAD_ERRORS.clear()
        for name in [m for m in sys.modules if m.startswith("niice_plugin_")]:
            del sys.modules[name]
    from . import builtin
    if force:
        importlib.reload(builtin)

    folder = settings.plugins_dir()
    if os.path.isdir(folder):
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(folder, fname)
            _current_source = f"plugin file {fname}"
            try:
                name = "niice_plugin_" + fname[:-3]
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
            except Exception:
                LOAD_ERRORS.append((fname, traceback.format_exc()))
            finally:
                _current_source = "builtin"

    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="niice.plugins") if hasattr(eps, "select") else eps.get("niice.plugins", [])
        for ep in group:
            _current_source = f"package {ep.value.split(':')[0]}"
            try:
                obj = ep.load()
                if callable(obj) and not isinstance(obj, type(sys)):
                    obj()  # entry point may be a register() function
            except Exception:
                LOAD_ERRORS.append((ep.name, traceback.format_exc()))
            finally:
                _current_source = "builtin"
    except Exception:
        pass
    _loaded = True
    return _REGISTRY


def all_tools() -> list[Tool]:
    return sorted(load_all().values(), key=lambda t: (t.category != "", t.category, t.order, t.label))


def get_tool(tool_id: str) -> Optional[Tool]:
    return load_all().get(tool_id)


def enabled_tools(s: Optional[dict] = None) -> list[Tool]:
    s = s or settings.load()
    disabled = set(s.get("disabled", []))
    return [t for t in all_tools() if t.always_on or t.id not in disabled]


EXAMPLE_PLUGIN = '''"""Example NiiCE plugin: Gaussian smoothing.

Files in this folder are loaded at start-up. Edit, rename or delete freely, then open
NiiCE Settings and press "Save & update menu" so the context menu picks up the change.
"""
from niice.plugins import image_filter, Float, Choice


@image_filter(
    id="example.smooth",
    menu="Operations/Gaussian smooth",
    suffix="_smooth",
    params={
        "fwhm_mm": Float(4.0, min=0, max=50, step=0.5, label="FWHM (mm)",
                         help="Full width at half maximum of the Gaussian kernel"),
        "mode": Choice(choices=["nearest", "constant", "reflect"], help="Border handling"),
    },
)
def smooth(data, img, fwhm_mm, mode):
    """Smooth each 3D volume with a Gaussian kernel of the given FWHM."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    # FWHM = 2*sqrt(2*ln 2) * sigma ~= 2.355 * sigma; sigma is converted from mm to voxels per axis
    zooms = np.array(img.header.get_zooms()[:3])
    sigma = list(fwhm_mm / 2.3548 / zooms) + [0] * (data.ndim - 3)
    return gaussian_filter(data, sigma=sigma, mode=mode)
'''
