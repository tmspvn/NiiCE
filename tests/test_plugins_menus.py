import os
import threading

import pytest

from niice import cli, plugins, settings
from niice.integration import collect, menus


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NIICE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plugins.load_all(force=True)
    yield
    plugins.load_all(force=True)


def test_builtin_tools_registered():
    ids = {t.id for t in plugins.all_tools()}
    assert {"info", "math", "ops.binarise", "ops.skullstrip", "plot.gif", "reg.register", "settings"} <= ids
    for t in plugins.all_tools():
        assert callable(t.callable())  # every "module:function" resolves


def test_disable_tool_hides_it():
    s = settings.load()
    s["disabled"] = ["plot.gif", "settings"]
    settings.save(s)
    ids = {t.id for t in plugins.enabled_tools()}
    assert "plot.gif" not in ids
    assert "settings" in ids  # always_on


def test_user_plugin_file_is_loaded(tmp_path):
    os.makedirs(settings.plugins_dir())
    with open(os.path.join(settings.plugins_dir(), "smooth.py"), "w") as f:
        f.write(plugins.EXAMPLE_PLUGIN)
    with open(os.path.join(settings.plugins_dir(), "broken.py"), "w") as f:
        f.write("raise RuntimeError('boom')\n")
    plugins.load_all(force=True)
    t = plugins.get_tool("example.smooth")
    assert t is not None and t.source == "plugin file smooth.py"
    assert any(src == "broken.py" and "boom" in tb for src, tb in plugins.LOAD_ERRORS)
    # the decorated function still works as a plain function
    import numpy as np
    import nibabel as nib
    mod = __import__("niice_plugin_smooth")
    img = nib.Nifti1Image(np.zeros((11, 11, 11), np.float32), np.eye(4))
    data = np.zeros((11, 11, 11), np.float32)
    data[5, 5, 5] = 1
    out = mod.smooth(data, img, fwhm_mm=2.0, mode="nearest")
    assert out.sum() == pytest.approx(1.0, rel=1e-3) and out[5, 5, 5] < 1


def test_nautilus_install_and_uninstall(tmp_path):
    tools = plugins.enabled_tools()
    menus.install(["nautilus"], tools, use_extension=False)  # Scripts > NiiCE variant
    root = os.path.expanduser("~/.local/share/nautilus/scripts/NiiCE")
    script = os.path.join(root, "Operations", "Binarise")
    assert os.access(script, os.X_OK)
    assert "-m niice run ops.binarise" in open(script).read()
    assert os.path.exists(os.path.join(root, "Settings..."))
    menus.uninstall(["nautilus"])
    assert not os.path.exists(root)


def test_refuses_foreign_folder(tmp_path):
    root = os.path.expanduser("~/.local/share/nautilus/scripts/NiiCE")
    os.makedirs(root)
    with pytest.raises(RuntimeError):
        menus.install(["nautilus"], plugins.enabled_tools(), use_extension=False)


@pytest.mark.parametrize("plugin_present", [True, False])
def test_install_picks_top_level_menu_when_plugin_present(tmp_path, monkeypatch, plugin_present):
    monkeypatch.setattr(menus, "python_extension_available", lambda name: plugin_present)
    menus.install(["nautilus"], plugins.enabled_tools())
    ext = os.path.expanduser("~/.local/share/nautilus-python/extensions/niice_menu.py")
    scripts = os.path.expanduser("~/.local/share/nautilus/scripts/NiiCE")
    assert os.path.exists(ext) == plugin_present
    assert os.path.isdir(scripts) == (not plugin_present)
    assert menus.install_notes["nautilus"].startswith("Scripts") == (not plugin_present)


def test_dolphin_install(tmp_path):
    menus.install(["dolphin"], plugins.enabled_tools())
    text = open(os.path.expanduser("~/.local/share/kio/servicemenus/niice.desktop")).read()
    assert "X-KDE-Submenu=NiiCE" in text and "run ops.resample %F" in text


def test_cli_rejects_wrong_selection(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_notify_failure", lambda t, m: print("FAIL", t, m))
    for name in ("a.nii.gz", "notes.txt"):
        (tmp_path / name).write_bytes(b"")  # only existence is checked before these errors
    assert cli.run_tool("ops.subtract", [str(tmp_path / "a.nii.gz")] * 3) == 2
    assert "Too many" in capsys.readouterr().out
    assert cli.run_tool("info", [str(tmp_path / "notes.txt")]) == 2
    assert "Not NIfTI" in capsys.readouterr().out


def test_collect_merges_concurrent_launches(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "QUIET", 0.3)
    monkeypatch.setattr(collect.tempfile, "gettempdir", lambda: str(tmp_path))
    results = []

    def launch(i):
        results.append(collect.collect("info", f"/data/f{i}.nii"))

    threads = [threading.Thread(target=launch, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    leaders = [r for r in results if r]
    assert len(leaders) == 1 and sorted(leaders[0]) == sorted(f"/data/f{i}.nii" for i in range(5))


def test_cli_missing_file_and_demo(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_notify_failure", lambda t, m: print("FAIL", t, m))
    assert cli.run_tool("info", [str(tmp_path / "a.nii.gz")]) == 2
    assert "File not found" in capsys.readouterr().out
    assert cli.main(["demo", str(tmp_path / "demo")]) == 0
    names = sorted(os.listdir(tmp_path / "demo"))
    assert names == ["T1w.nii.gz", "T2w_shifted.nii.gz", "bold.nii.gz", "labels.nii.gz"]
    from niice.core import io, space
    d = tmp_path / "demo"
    assert space.compare(io.load(str(d / "T1w.nii.gz")), io.load(str(d / "T2w_shifted.nii.gz"))).level == "warning"
    assert space.compare(io.load(str(d / "T1w.nii.gz")), io.load(str(d / "labels.nii.gz"))).level == "same"


def _fake_gi(module_name):
    """Minimal stand-in for gi.repository.<Nautilus|Nemo> to exercise the generated extension."""
    import types

    class Menu:
        def __init__(self):
            self.items = []

        def append_item(self, item):
            self.items.append(item)

    class MenuItem:
        def __init__(self, name, label, tip=""):
            self.name, self.label, self.submenu, self.handlers = name, label, None, []

        def set_submenu(self, m):
            self.submenu = m

        def connect(self, signal, fn, *args):
            self.handlers.append((fn, args))

    fm = types.SimpleNamespace(Menu=Menu, MenuItem=MenuItem, MenuProvider=type("MenuProvider", (), {}))
    gobject = types.SimpleNamespace(GObject=type("GObject", (), {}))
    repo = types.ModuleType("gi.repository")
    setattr(repo, module_name, fm)
    repo.GObject = gobject
    gi = types.ModuleType("gi")
    gi.require_version = lambda name, version: None
    gi.repository = repo
    return gi, repo


class _File:
    def __init__(self, path):
        self.path = path

    def get_location(self):
        import types
        return types.SimpleNamespace(get_path=lambda: self.path)


@pytest.mark.parametrize("fm,module", [("nautilus", "Nautilus"), ("nemo", "Nemo")])
def test_top_level_extension_menu(tmp_path, monkeypatch, fm, module):
    import importlib.util
    import sys
    tools = plugins.enabled_tools()
    menus.install([fm], tools, use_extension=True)
    path = os.path.expanduser(f"~/.local/share/{fm}-python/extensions/niice_menu.py")
    assert os.path.exists(path)
    assert not os.path.exists(os.path.expanduser(menus._LINUX_SCRIPT_DIRS[fm][1] + "/NiiCE"))
    gi, repo = _fake_gi(module)
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    spec = importlib.util.spec_from_file_location("niice_menu_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    provider = mod.NiiCEMenu()
    assert provider.get_file_items([_File("/d/notes.txt")]) == []          # not NIfTI: no menu
    items = provider.get_file_items(None, [_File("/d/t1.nii.gz"), _File("/d/t2.nii")])  # 3.x signature
    top = items[0]
    assert top.label == "NiiCE"
    labels = [i.label for i in top.submenu.items]
    assert labels[:2] == ["Info", "Math"] and labels[-1] == "Settings..."
    ops = next(i for i in top.submenu.items if i.label == "Operations")
    binarise = next(i for i in ops.submenu.items if i.label == "Binarise")
    launched = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    fn, args = binarise.handlers[0]
    fn(binarise, *args)
    assert launched[0][1:] == ["-m", "niice", "run", "ops.binarise", "/d/t1.nii.gz", "/d/t2.nii"]
    # falling back to scripts removes the extension again
    menus.install([fm], tools, use_extension=False)
    assert not os.path.exists(path) and "Scripts" in menus.install_notes[fm]


def test_empty_xdg_config_home_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("NIICE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert settings.config_dir() == os.path.join(os.path.expanduser("~/.config"), "niice")


def test_hint_for_old_libstdcxx():
    tb = ("ImportError: /home/u/anaconda3/bin/../lib/libstdc++.so.6: version `GLIBCXX_3.4.29' not found "
          "(required by .../shiboken6/libshiboken6.abi3.so.6.10)")
    assert "--python /usr/bin/python3" in cli._hint(tb)
    assert cli._hint("ZeroDivisionError") == ""


USER_UCA = """<?xml version="1.0" encoding="UTF-8"?>
<actions>
<action>
\t<icon>utilities-terminal</icon>
\t<name>Open Terminal Here</name>
\t<submenu></submenu>
\t<unique-id>1600000000000000-1</unique-id>
\t<command>exo-open --working-directory %f --launch TerminalEmulator</command>
\t<description>Example for a custom action</description>
\t<range></range>
\t<patterns>*</patterns>
\t<startup-notify/>
\t<directories/>
</action>
</actions>
"""


@pytest.mark.parametrize("submenus", [True, False])
def test_thunar_custom_actions(tmp_path, monkeypatch, submenus):
    import xml.etree.ElementTree as ET
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    uca = os.path.expanduser("~/.config/Thunar/uca.xml")
    os.makedirs(os.path.dirname(uca))
    with open(uca, "w") as fh:
        fh.write(USER_UCA)
    monkeypatch.setattr(menus, "thunar_version", lambda: (4, 18) if submenus else (1, 8))
    tools = plugins.enabled_tools()
    for _ in range(2):  # installing twice must not duplicate entries
        menus.install(["thunar"], tools)
    root = ET.parse(uca).getroot()
    actions = root.findall("action")
    ours = [a for a in actions if a.findtext("unique-id").startswith("niice-")]
    assert len(ours) == len(tools)
    assert actions[0].findtext("name") == "Open Terminal Here"      # user's action kept, first
    assert os.path.exists(uca + ".niice-backup")
    binarise = next(a for a in ours if a.findtext("unique-id") == "niice-ops.binarise")
    assert binarise.findtext("command").endswith("-m niice run ops.binarise %F")
    assert "*.nii.gz" in binarise.findtext("patterns") and binarise.find("other-files") is not None
    if submenus:
        assert (binarise.findtext("name"), binarise.findtext("submenu")) == ("Binarise", "NiiCE/Operations")
        assert next(a for a in ours if a.findtext("unique-id") == "niice-info").findtext("submenu") == "NiiCE"
        assert "submenu" in menus.install_notes["thunar"]
    else:
        assert binarise.findtext("name") == "NiiCE: Operations: Binarise"
    assert ours[-1].findtext("name").endswith("Settings...")
    menus.uninstall(["thunar"])
    left = ET.parse(uca).getroot().findall("action")
    assert [a.findtext("name") for a in left] == ["Open Terminal Here"]


def test_thunar_without_existing_file(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(menus, "thunar_version", lambda: None)
    menus.install(["thunar"], plugins.enabled_tools())
    uca = os.path.expanduser("~/.config/Thunar/uca.xml")
    assert ET.parse(uca).getroot().tag == "actions" and not os.path.exists(uca + ".niice-backup")
