import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
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


class RunAssessmentPreValidationTests(unittest.TestCase):
    def test_invalid_config_stops_before_pipeline_side_effects(self):
        config = {
            "assessmentType": 99,
            "cloudServiceProvider": 2,
            "providerDetails": {},
        }

        with (
            patch(
                "main.validate_config", side_effect=ValueError("bad config")
            ) as mock_validate,
            patch("main.resolve_mode") as mock_resolve_mode,
            patch("main.create_directory") as mock_create_directory,
            patch("main.verify_credentials") as mock_verify_credentials,
            patch("main.print_step") as mock_print_step,
            patch("main.console.print"),
        ):
            result = main.run_assessment(config, "aws")

        self.assertIsNone(result)
        mock_validate.assert_called_once_with(config)
        mock_print_step.assert_called_once_with(
            "Configuration validation failed.", status="error", logs="bad config"
        )
        mock_resolve_mode.assert_not_called()
        mock_create_directory.assert_not_called()
        mock_verify_credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
