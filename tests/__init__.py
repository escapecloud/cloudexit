import atexit
import os
import shutil
import tempfile

# No test may read or write the real home, config, data or cache directories
_home = tempfile.mkdtemp(prefix="cloudexit-tests-")
atexit.register(shutil.rmtree, _home, ignore_errors=True)
os.environ.update(
    HOME=_home,
    USERPROFILE=_home,
    APPDATA=os.path.join(_home, "AppData", "Roaming"),
    LOCALAPPDATA=os.path.join(_home, "AppData", "Local"),
)
