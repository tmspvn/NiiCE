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
    menus.install(["nautilus"], tools)
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
        menus.install(["nautilus"], plugins.enabled_tools())


def test_dolphin_install(tmp_path):
    menus.install(["dolphin"], plugins.enabled_tools())
    text = open(os.path.expanduser("~/.local/share/kio/servicemenus/niice.desktop")).read()
    assert "X-KDE-Submenu=NiiCE" in text and "run ops.resample %F" in text


def test_cli_rejects_wrong_selection(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_notify_failure", lambda t, m: print("FAIL", t, m))
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
