from importlib.resources import files
from pathlib import Path, PurePosixPath


def asset_path(*parts: str) -> Path:
    root = files("cloudexit")
    if not isinstance(root, Path):
        raise RuntimeError(
            "the cloudexit assets are not on the file system "
            f"({type(root).__name__}); install the package into a directory"
        )
    return root.joinpath("assets", *parts)


def find_asset(report_path: str | Path, relative: str) -> Path | None:
    # The report folder carries its own copy of the assets; the installed
    # package is the fallback.
    parts = PurePosixPath(str(relative).lstrip("/")).parts
    if not parts or ".." in parts:
        return None
    for base in (Path(report_path) / "assets", asset_path()):
        candidate = base.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None
