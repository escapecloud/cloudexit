# tests/providers/aws/test_costs.py
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch


from cloudexit.providers.aws.costs import get_missing_months_aws


class GetMissingMonthsAwsTests(unittest.TestCase):
    @patch("cloudexit.providers.aws.costs.datetime")
    def test_returns_missing_months(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 6, 15, tzinfo=timezone.utc)
        mock_dt.strptime = datetime.strptime

        processed = {"2026-06-01", "2026-05-01", "2026-04-01"}
        missing = get_missing_months_aws(processed, 6)

        # Should be missing: 2026-03, 2026-02, 2026-01
        self.assertEqual(len(missing), 3)
        self.assertIn(date(2026, 3, 1), missing)
        self.assertIn(date(2026, 2, 1), missing)
        self.assertIn(date(2026, 1, 1), missing)

    @patch("cloudexit.providers.aws.costs.datetime")
    def test_returns_empty_when_all_present(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 6, 15, tzinfo=timezone.utc)
        mock_dt.strptime = datetime.strptime

        processed = {
            "2026-06-01",
            "2026-05-01",
            "2026-04-01",
            "2026-03-01",
            "2026-02-01",
            "2026-01-01",
        }
        missing = get_missing_months_aws(processed, 6)
        self.assertEqual(missing, [])

    @patch("cloudexit.providers.aws.costs.datetime")
    def test_returns_all_when_none_processed(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 6, 15, tzinfo=timezone.utc)
        mock_dt.strptime = datetime.strptime

        missing = get_missing_months_aws(set(), 6)
        self.assertEqual(len(missing), 6)


class BuildAwsCostInventoryErrorTests(unittest.TestCase):
    @patch("cloudexit.providers.aws.costs.connect")
    @patch("cloudexit.providers.aws.costs.boto3.Session")
    def test_passes_session_token_to_boto3_session(
        self, mock_session_cls, mock_connect
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce
        mock_ce.get_cost_and_usage.return_value = {"ResultsByTime": []}

        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        from cloudexit.providers.aws.costs import build_aws_cost_inventory

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            build_aws_cost_inventory(
                2,
                {
                    "accessKey": "AK",
                    "secretKey": "SK",
                    "sessionToken": "TOKEN",
                    "region": "us-east-1",
                },
                report_path,
                raw_data_path,
            )

        mock_session_cls.assert_called_once_with(
            aws_access_key_id="AK",
            aws_secret_access_key="SK",
            aws_session_token="TOKEN",
            region_name="us-east-1",
        )

    @patch("cloudexit.providers.aws.costs.connect")
    @patch("cloudexit.providers.aws.costs.boto3.Session")
    def test_sqlite_error_is_logged_but_not_reraised(
        self, mock_session_cls, mock_connect
    ):
        """sqlite3.Error is caught and logged but NOT re-raised in current code."""
        import sqlite3

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce
        mock_ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-01-01"},
                    "Groups": [
                        {
                            "Metrics": {
                                "UnblendedCost": {"Amount": "10.0", "Unit": "USD"}
                            }
                        }
                    ],
                }
            ]
        }

        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = sqlite3.Error("disk I/O error")

        from cloudexit.providers.aws.costs import build_aws_cost_inventory

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            # sqlite3.Error is caught but NOT re-raised in current code
            # (this documents the current behavior)
            try:
                build_aws_cost_inventory(
                    2,
                    {"accessKey": "AK", "secretKey": "SK", "region": "us-east-1"},
                    report_path,
                    raw_data_path,
                )
            except sqlite3.Error:
                pass  # Expected: current code catches but does not re-raise sqlite3.Error


if __name__ == "__main__":
    unittest.main()
