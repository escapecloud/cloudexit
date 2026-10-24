import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.utils import load_config


class LoadConfigTests(unittest.TestCase):
    def test_load_config_returns_parsed_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            expected = {
                "cloudServiceProvider": 2,
                "assessmentType": 1,
                "providerDetails": {"region": "eu-central-1"},
            }
            config_path.write_text(json.dumps(expected), encoding="utf-8")

            self.assertEqual(load_config(str(config_path)), expected)

    def test_load_config_returns_none_for_missing_file(self):
        with patch("utils.utils.console.print") as mock_print:
            result = load_config("/tmp/does-not-exist-config.json")

        self.assertIsNone(result)
        mock_print.assert_called_once()

    def test_load_config_returns_none_for_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text("{invalid json", encoding="utf-8")

            with patch("utils.utils.console.print") as mock_print:
                result = load_config(str(config_path))

        self.assertIsNone(result)
        mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
