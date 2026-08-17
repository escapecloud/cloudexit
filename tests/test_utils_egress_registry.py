# tests/test_utils_egress_registry.py
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.utils_db import load_data
from core.utils_egress import estimate_egress, load_egress_registry
from core.utils_egress_azure import filter_data_bearing_resources

# (id, csp, code, name, icon, status)
RESOURCE_TYPES = [
    (1, 1, "Microsoft.Storage/storageAccounts", "Storage Account", "sa.png", "t"),
    # Parent status 'f': stays out of the inventory catalogue but still holds
    # data that has to be egressed.
    (2, 1, "Microsoft.Compute/disks", "Managed Disks", "disk.png", "f"),
    (3, 1, "Microsoft.Network/virtualNetworks", "Virtual Network", "vnet.png", "t"),
    (4, 2, "AWS.s3.list_buckets.Buckets", "S3 (Simple Storage Service)", "s3.png", "t"),
    (5, 2, "AWS.backup.list_backup_plans.BackupPlansList", "Backup", "bk.png", "t"),
]

# (id, resource_type, data_category, strategy, params, status)
RESOURCE_TYPE_DATA = [
    (1, 1, "object", "storage_account_metrics", "{}", "t"),
    (
        2,
        2,
        "block",
        "allocated_size_property",
        '{"api_version": "2024-03-02", "size_property": "diskSizeGB"}',
        "t",
    ),
    # Own status 'f': switched off, must never reach the registry.
    (3, 3, "network", "should_never_load", "{}", "f"),
    (4, 4, "object", "s3_bucket_metrics", "{}", "t"),
    (
        5,
        5,
        "backup",
        "backup_vaults",
        '{"enumeration": {"service": "backup", '
        '"operation": "list_backup_vaults", "result_path": ["BackupVaultList"]}}',
        "t",
    ),
]

_SCHEMA = """
CREATE TABLE resourcetype (
    id INTEGER PRIMARY KEY,
    csp INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    icon TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE resourcetype_data (
    id INTEGER PRIMARY KEY,
    resource_type INTEGER NOT NULL,
    data_category TEXT NOT NULL,
    strategy TEXT NOT NULL,
    params TEXT,
    status TEXT NOT NULL
);
"""


def _make_db(path, *, with_data_table=True, data_rows=RESOURCE_TYPE_DATA):
    conn = sqlite3.connect(path)
    schema = _SCHEMA
    if not with_data_table:
        schema = _SCHEMA[: _SCHEMA.index("CREATE TABLE resourcetype_data")]
    conn.executescript(schema)
    conn.executemany("INSERT INTO resourcetype VALUES (?,?,?,?,?,?)", RESOURCE_TYPES)
    if with_data_table:
        conn.executemany(
            "INSERT INTO resourcetype_data VALUES (?,?,?,?,?,?)", data_rows
        )
    conn.commit()
    conn.close()


class _FixtureDbTests(unittest.TestCase):
    WITH_DATA_TABLE = True
    DATA_ROWS = RESOURCE_TYPE_DATA

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(os.unlink, self.db_path)
        _make_db(
            self.db_path,
            with_data_table=self.WITH_DATA_TABLE,
            data_rows=self.DATA_ROWS,
        )
        patcher = patch(
            "core.utils_egress.load_data",
            side_effect=lambda table: load_data(table, db_path=self.db_path),
        )
        self.addCleanup(patcher.stop)
        patcher.start()


class RegistryConstructionTests(_FixtureDbTests):
    def test_azure_registry_joins_and_keys_by_lowercased_code(self):
        registry = load_egress_registry(1)

        self.assertEqual(
            sorted(registry),
            ["microsoft.compute/disks", "microsoft.storage/storageaccounts"],
        )

    def test_aws_registry_keeps_code_verbatim(self):
        registry = load_egress_registry(2)

        self.assertEqual(
            sorted(registry),
            [
                "AWS.backup.list_backup_plans.BackupPlansList",
                "AWS.s3.list_buckets.Buckets",
            ],
        )

    def test_params_are_merged_into_the_entry(self):
        entry = load_egress_registry(1)["microsoft.compute/disks"]

        self.assertEqual(entry["api_version"], "2024-03-02")
        self.assertEqual(entry["size_property"], "diskSizeGB")

    def test_label_comes_from_resourcetype_name(self):
        registry = load_egress_registry(2)

        self.assertEqual(
            registry["AWS.s3.list_buckets.Buckets"]["label"],
            "S3 (Simple Storage Service)",
        )

    def test_category_and_strategy_come_from_resourcetype_data(self):
        entry = load_egress_registry(1)["microsoft.storage/storageaccounts"]

        self.assertEqual(entry["category"], "object")
        self.assertEqual(entry["strategy"], "storage_account_metrics")

    def test_enumeration_params_survive_the_merge(self):
        registry = load_egress_registry(2)
        entry = registry["AWS.backup.list_backup_plans.BackupPlansList"]

        self.assertEqual(
            entry["enumeration"],
            {
                "service": "backup",
                "operation": "list_backup_vaults",
                "result_path": ["BackupVaultList"],
            },
        )

    def test_other_csp_rows_are_not_included(self):
        self.assertNotIn("AWS.s3.list_buckets.Buckets", load_egress_registry(1))
        self.assertNotIn("microsoft.compute/disks", load_egress_registry(2))


class ParamsCannotShadowCanonicalKeysTests(_FixtureDbTests):
    DATA_ROWS = [
        (
            1,
            1,
            "object",
            "storage_account_metrics",
            '{"label": "hijacked", "category": "hijacked", "strategy": "hijacked"}',
            "t",
        )
    ]

    def test_canonical_keys_win_over_params(self):
        entry = load_egress_registry(1)["microsoft.storage/storageaccounts"]

        self.assertEqual(entry["label"], "Storage Account")
        self.assertEqual(entry["category"], "object")
        self.assertEqual(entry["strategy"], "storage_account_metrics")


class StatusFilteringTests(_FixtureDbTests):
    def test_row_with_disabled_parent_resourcetype_is_included(self):
        # 513/514/515 ship with resourcetype.status = 'f' on purpose; filtering
        # on it would drop the whole block category from the estimate.
        registry = load_egress_registry(1)

        self.assertIn("microsoft.compute/disks", registry)
        self.assertEqual(registry["microsoft.compute/disks"]["label"], "Managed Disks")

    def test_row_with_disabled_resourcetype_data_is_excluded(self):
        registry = load_egress_registry(1)

        self.assertNotIn("microsoft.network/virtualnetworks", registry)
        strategies = [entry["strategy"] for entry in registry.values()]
        self.assertNotIn("should_never_load", strategies)


class AzureKeyMatchingTests(_FixtureDbTests):
    def test_lowercased_key_matches_an_arm_resource_type(self):
        registry = load_egress_registry(1)
        resource = MagicMock()
        # ARM returns the provider namespace in its own casing.
        resource.type = "Microsoft.Storage/storageAccounts"
        resource.name = "sa1"

        matched = filter_data_bearing_resources([resource], registry)

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1]["strategy"], "storage_account_metrics")
        self.assertEqual(matched[0][1]["label"], "Storage Account")


class MissingTableTests(_FixtureDbTests):
    WITH_DATA_TABLE = False

    def test_missing_table_fails_the_stage(self):
        # There is no built-in fallback: without master data the estimate would
        # be silently wrong, so the egress stage has to fail instead.
        with self.assertRaises(sqlite3.Error):
            load_egress_registry(1)

    def test_estimate_egress_reports_the_failure_instead_of_raising(self):
        with patch(
            "core.utils_egress_azure.collect_azure_egress",
            side_effect=sqlite3.OperationalError("no such table: resourcetype_data"),
        ):
            result = estimate_egress(
                1,
                {},
                tempfile.gettempdir(),
                report_path=tempfile.gettempdir(),
                name="acme",
                exit_strategy=1,
                assessment_type=1,
            )

        self.assertFalse(result["success"])
        self.assertIn("resourcetype_data", result["logs"])


class EmptyRegistryTests(_FixtureDbTests):
    DATA_ROWS = []

    def test_no_matching_rows_raises(self):
        with self.assertRaises(ValueError) as ctx:
            load_egress_registry(2)

        self.assertIn("CSP 2", str(ctx.exception))


class OtherCspOnlyTests(_FixtureDbTests):
    DATA_ROWS = [(1, 1, "object", "storage_account_metrics", "{}", "t")]

    def test_csp_without_enabled_rows_raises_even_when_another_csp_has_them(self):
        self.assertIn("microsoft.storage/storageaccounts", load_egress_registry(1))
        with self.assertRaises(ValueError):
            load_egress_registry(2)


class MalformedParamsTests(_FixtureDbTests):
    DATA_ROWS = [
        (1, 1, "object", "storage_account_metrics", "{not json", "t"),
        (2, 2, "block", "allocated_size_property", None, "t"),
    ]

    def test_bad_params_degrade_to_empty_without_dropping_the_row(self):
        registry = load_egress_registry(1)

        self.assertEqual(
            sorted(registry),
            ["microsoft.compute/disks", "microsoft.storage/storageaccounts"],
        )
        self.assertEqual(
            registry["microsoft.storage/storageaccounts"]["strategy"],
            "storage_account_metrics",
        )


if __name__ == "__main__":
    unittest.main()
