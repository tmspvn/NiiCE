"""User settings, stored as JSON in the per-user config folder."""
from __future__ import annotations

import json
import os
import sys

DEFAULTS = {
    "disabled": [],               # tool ids hidden from the context menu
    "output_ext": "same",         # 'same' | '.nii.gz' | '.nii'
    "open_folder_when_done": False,
    "synthstrip_backend": "auto",  # 'auto' | 'native' | 'docker' | 'podman' | 'apptainer'
    "synthstrip_image": "freesurfer/synthstrip",
    "docker_pkexec": False,       # prefix docker with pkexec (graphical sudo)
    "ants_threads": 3,
    "preview_max_mvox": 64,
    "theme": "system",            # 'system' | 'light' | 'dark'
    "accent": "#1565c0",          # accent colour of buttons, selections, progress bars       # skip previews of images with more voxels (millions)
    "integrations": [],           # file managers the menu was installed into
}


def config_dir() -> str:
    override = os.environ.get("NIICE_CONFIG_DIR")
    if override:
        return override
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "niice")


def plugins_dir() -> str:
    return os.path.join(config_dir(), "plugins")


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def load() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(settings_path()) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save(s: dict) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    tmp = settings_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, settings_path())


def get(key: str):
    return load().get(key, DEFAULTS.get(key))
