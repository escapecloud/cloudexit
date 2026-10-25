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


def safe_relative(relative: object) -> PurePosixPath | None:
    # A path a catalogue names for an asset, as a relative path inside the
    # assets folder, or None. One leading slash is how the catalogue writes its
    # icons ("/icons/aws/..."): it means the assets folder, not the file system.
    text = str(relative or "")
    if text.startswith("/"):
        text = text[1:]
    if not text or text.startswith(("/", "\\")):
        return None
    # No backslash (UNC and Windows separators), no colon (drive letters and
    # stream names) and no NUL; nothing that could leave the folder
    if any(char in text for char in ("\\", ":", "\x00")):
        return None
    parts = PurePosixPath(text).parts
    if not parts or ".." in parts:
        return None
    return PurePosixPath(*parts)


def resolve_inside(base: str | Path, relative: object) -> Path | None:
    # base/relative when it stays inside base, whatever links lie on the way;
    # nothing outside base is looked at
    safe = safe_relative(relative)
    if safe is None:
        return None
    root = Path(base)
    candidate = root.joinpath(*safe.parts)
    try:
        inside = candidate.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate if inside else None


def find_asset(report_path: str | Path, relative: str) -> Path | None:
    # The report folder carries its own copy of the assets; the installed
    # package is the fallback.
    for base in (Path(report_path) / "assets", asset_path()):
        candidate = resolve_inside(base, relative)
        if candidate is not None and candidate.is_file():
            return candidate
    return None
