import os
from pathlib import Path


def isolated_environ(root):
    # Where a run keeps its files, for Linux and macOS (XDG) and for Windows. Data
    # and config line up on both, so a path built from `root` is the same everywhere.
    root = Path(root)
    environ = {
        "XDG_DATA_HOME": root / "data",
        "XDG_CONFIG_HOME": root / "config",
        "XDG_CACHE_HOME": root / "cache",
        "XDG_STATE_HOME": root / "state",
        "LOCALAPPDATA": root / "data",
        "APPDATA": root / "config",
        "HOME": root / "home",
        "USERPROFILE": root / "home",
    }
    environ = {name: str(path) for name, path in environ.items()}
    # A cleared environment still needs this on Windows
    if "SYSTEMROOT" in os.environ:
        environ["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return environ


def forget_cache(root):
    # The platform answers cached by an earlier call of the same test
    import shutil

    for path in (Path(root) / "cache", Path(root) / "data" / "cloudexit" / "cache"):
        shutil.rmtree(path, ignore_errors=True)
