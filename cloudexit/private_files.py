import os
import stat
import sys
from pathlib import Path


def write_private(path: str | Path, data: bytes) -> None:
    # Readable by the owner only; a stricter umask is never loosened
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def restrict_tree(root: str | Path) -> None:
    # Remove the group and other permissions of a folder and everything in it;
    # whatever the owner has stays as it is
    if sys.platform == "win32":
        return
    root = Path(root)
    if not root.is_dir():
        return
    for directory, folders, files in os.walk(root):
        for name in [*folders, *files]:
            _restrict(Path(directory) / name)
    _restrict(root)


def _restrict(path: Path) -> None:
    try:
        if path.is_symlink():
            return
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode & 0o700)
    except OSError:
        pass
