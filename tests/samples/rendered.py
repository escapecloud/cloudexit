import atexit
import tempfile
from pathlib import Path

from tests.samples.catalogue import build_sample_catalogue
from tests.samples.render import render_cases
from tests.samples.scenarios import load_scenarios

_STATE = {}


def rendered():
    # Every sample case rendered once per test run, for all the tests that read
    # a finished report folder; rendering is the slow part
    if not _STATE:
        tmp = tempfile.TemporaryDirectory()
        atexit.register(tmp.cleanup)
        root = Path(tmp.name)
        scenarios = load_scenarios()
        catalogue = root / "catalogue.db"
        build_sample_catalogue(catalogue, scenarios.values())
        results, failures = render_cases(
            scenarios.values(), root / "out", catalogue_path=catalogue
        )
        _STATE.update(
            scenarios=scenarios,
            catalogue=catalogue,
            results={result.case: result for result in results},
            failures=failures,
        )
    return _STATE
