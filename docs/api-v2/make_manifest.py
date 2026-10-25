import hashlib
import sys
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
MANIFEST = DIRECTORY / "MANIFEST.sha256"


def definition_files():
    return sorted(
        path
        for path in DIRECTORY.rglob("*")
        if path.is_file()
        and path != MANIFEST
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )


def manifest_text():
    lines = []
    for path in definition_files():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(DIRECTORY).as_posix()}")
    return "\n".join(lines) + "\n"


def main(argv):
    if "--check" in argv:
        if MANIFEST.read_text() != manifest_text():
            print("MANIFEST.sha256 is out of date; run make_manifest.py")
            return 1
        return 0
    MANIFEST.write_text(manifest_text())
    print(f"wrote {MANIFEST.name} ({len(definition_files())} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
