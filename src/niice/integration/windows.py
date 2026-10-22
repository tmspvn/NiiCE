"""Windows Explorer cascading context menu (HKEY_CURRENT_USER, no admin rights needed).

Layout in the registry, for each extension (.nii and .gz, since Windows sees only the last
extension of .nii.gz):

  HKCU\\Software\\Classes\\SystemFileAssociations\\<ext>\\shell\\NiiCE
      MUIVerb = "NiiCE", SubCommands = ""
      shell\\<NN Category>             MUIVerb, SubCommands = ""
          shell\\<NN Tool>\\command     "<pythonw>" -m niice run --collect <id> "%1"

Explorer starts one process per selected file; `--collect` merges them (see collect.py).
"""
from __future__ import annotations

BASE = r"Software\Classes\SystemFileAssociations\{ext}\shell\NiiCE"
EXTS = (".nii", ".gz")


def _delete_tree(winreg, root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(winreg, root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def uninstall():
    import winreg
    for ext in EXTS:
        _delete_tree(winreg, winreg.HKEY_CURRENT_USER, BASE.format(ext=ext))


def install(tools, pythonw: str):
    import winreg
    uninstall()
    hk = winreg.HKEY_CURRENT_USER
    for ext in EXTS:
        base = BASE.format(ext=ext)
        with winreg.CreateKey(hk, base) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, "NiiCE")
            winreg.SetValueEx(k, "SubCommands", 0, winreg.REG_SZ, "")
        cats = {}
        for i, t in enumerate(tools):
            parts = t.menu.split("/")
            parent = base
            for depth, cat in enumerate(parts[:-1]):
                key = "/".join(parts[: depth + 1])
                if key not in cats:
                    cats[key] = f"{parent}\\shell\\{len(cats):02d}"
                    with winreg.CreateKey(hk, cats[key]) as k:
                        winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, cat)
                        winreg.SetValueEx(k, "SubCommands", 0, winreg.REG_SZ, "")
                parent = cats[key]
            item = f"{parent}\\shell\\{i:02d}"
            with winreg.CreateKey(hk, item) as k:
                winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, parts[-1])
                winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
            with winreg.CreateKey(hk, item + "\\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ,
                                  f'"{pythonw}" -m niice run --collect {t.id} "%1"')
