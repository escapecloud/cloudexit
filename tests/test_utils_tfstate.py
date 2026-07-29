import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.utils_tfstate import (
    build_tfstate_resource_inventory,
    extract_managed_resources,
    extract_state_scope,
    file_sha256,
    parse_tfstate,
)

# Schema subset the tfstate builder touches, mirroring datasets/data.db.
SCHEMA = """
CREATE TABLE resourcetype (
    id INTEGER PRIMARY KEY,
    csp INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    icon TEXT NOT NULL,
    status TEXT CHECK(status IN ('t','f')) NOT NULL,
    tf_code TEXT
);
CREATE TABLE resource_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type INTEGER NOT NULL,
    location TEXT NOT NULL,
    count INTEGER NOT NULL,
    UNIQUE(resource_type, location)
);
"""


def build_state(resources, **overrides):
    state = {
        "version": 4,
        "terraform_version": "1.14.6",
        "serial": 7,
        "lineage": "e1c2f0c0-0000-0000-0000-000000000000",
        "resources": resources,
    }
    state.update(overrides)
    return state


def managed(resource_type, name, instances, module=None):
    entry = {
        "mode": "managed",
        "type": resource_type,
        "name": name,
        "provider": f'provider["registry.terraform.io/hashicorp/{resource_type.split("_")[0]}"]',
        "instances": instances,
    }
    if module:
        entry["module"] = module
    return entry


def instance(attributes=None, index_key=None):
    entry = {"schema_version": 0, "attributes": attributes or {}}
    if index_key is not None:
        entry["index_key"] = index_key
    return entry


def write_state(directory, state, filename="infra.tfstate"):
    path = Path(directory) / filename
    path.write_text(json.dumps(state), encoding="utf-8")
    return str(path)


def seed_db(db_path, rows):
    """rows: (id, csp, code, name, status, tf_code)"""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO resourcetype (id, csp, code, name, icon, status, tf_code) "
        "VALUES (?, ?, ?, ?, '/icons/misc/no_image.png', ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class ParseTfstateTests(unittest.TestCase):
    def test_rejects_missing_file(self):
        with self.assertRaisesRegex(ValueError, "Could not read Terraform state file"):
            parse_tfstate("/tmp/does-not-exist.tfstate")

    def test_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "broken.tfstate"
            path.write_text("{not json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                parse_tfstate(str(path))

    def test_rejects_legacy_state_version(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = write_state(tmp_dir, build_state([], version=3))

            with self.assertRaisesRegex(ValueError, "version 4"):
                parse_tfstate(path)

    def test_rejects_non_object_document(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "list.tfstate"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "not a state document"):
                parse_tfstate(str(path))

    def test_accepts_minimal_valid_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = write_state(
                tmp_dir,
                build_state(
                    [
                        managed(
                            "aws_s3_bucket", "this", [instance({"region": "eu-west-1"})]
                        )
                    ]
                ),
            )

            state = parse_tfstate(path)

        self.assertEqual(state["version"], 4)
        self.assertEqual(state["terraform_version"], "1.14.6")
        self.assertEqual(len(state["resources"]), 1)


class ExtractManagedResourcesTests(unittest.TestCase):
    def test_excludes_data_sources(self):
        state = build_state(
            [
                {
                    "mode": "data",
                    "type": "aws_caller_identity",
                    "name": "current",
                    "instances": [instance({"region": "eu-west-1"})],
                },
                managed("aws_s3_bucket", "this", [instance({"region": "eu-west-1"})]),
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual([r["type"] for r in records], ["aws_s3_bucket"])

    def test_one_record_per_instance(self):
        state = build_state(
            [
                managed(
                    "aws_dynamodb_table",
                    "this",
                    [
                        instance({"region": "eu-central-1"}, index_key="alpha"),
                        instance({"region": "eu-central-1"}, index_key="beta"),
                        instance({"region": "eu-central-1"}, index_key="gamma"),
                    ],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(len(records), 3)
        self.assertEqual({r["location"] for r in records}, {"eu-central-1"})

    def test_extracts_and_lowercases_aws_region(self):
        state = build_state(
            [managed("aws_s3_bucket", "this", [instance({"region": " EU-West-1 "})])]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["location"], "eu-west-1")

    def test_extracts_and_lowercases_azure_location(self):
        state = build_state(
            [
                managed(
                    "azurerm_managed_disk",
                    "this",
                    [instance({"location": "NorthEurope"})],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["location"], "northeurope")

    def test_falls_back_to_unknown_location(self):
        state = build_state(
            [
                managed("aws_iam_role", "this", [instance({"name": "role"})]),
                managed("aws_iam_policy", "this", [instance({"region": "   "})]),
                managed("aws_iam_user", "this", [instance()]),
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual([r["location"] for r in records], ["unknown"] * 3)

    def test_falls_back_to_region_from_own_arn(self):
        state = build_state(
            [
                managed(
                    "aws_dynamodb_table",
                    "this",
                    [
                        instance(
                            {
                                "arn": "arn:aws:dynamodb:EU-Central-1:266579820564:table/orders"
                            }
                        )
                    ],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["location"], "eu-central-1")

    def test_region_attribute_wins_over_arn(self):
        state = build_state(
            [
                managed(
                    "aws_efs_file_system",
                    "this",
                    [
                        instance(
                            {
                                "region": "eu-west-1",
                                "arn": "arn:aws:elasticfilesystem:us-east-1:266579820564:file-system/fs-1",
                            }
                        )
                    ],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["location"], "eu-west-1")

    def test_ignores_arns_of_referenced_resources(self):
        # stream_arn / kms_key_arn point at other resources, which may live in a
        # different region or account; only the resource's own arn may be read.
        state = build_state(
            [
                managed(
                    "aws_dynamodb_table",
                    "this",
                    [
                        instance(
                            {
                                "stream_arn": "arn:aws:dynamodb:us-east-1:999999999999:table/x/stream/2024",
                                "kms_key_arn": "arn:aws:kms:ap-south-1:999999999999:key/abc",
                                "restore_source_table_arn": "arn:aws:dynamodb:sa-east-1:999999999999:table/y",
                            }
                        )
                    ],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["location"], "unknown")

    def test_global_and_malformed_arns_fall_back_to_unknown(self):
        state = build_state(
            [
                # Global services write an empty region field by design.
                managed(
                    "aws_s3_bucket",
                    "this",
                    [instance({"arn": "arn:aws:s3:::my-bucket"})],
                ),
                managed(
                    "aws_iam_role",
                    "this",
                    [instance({"arn": "arn:aws:iam::266579820564:role/admin"})],
                ),
                managed(
                    "aws_vpc", "trunc", [instance({"arn": "arn:aws:ec2:eu-west-1"})]
                ),
                managed("aws_vpc", "nonsense", [instance({"arn": "not-an-arn"})]),
                managed("aws_vpc", "wrongtype", [instance({"arn": 42})]),
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual([r["location"] for r in records], ["unknown"] * 5)

    def test_arn_account_id_is_never_extracted(self):
        state = build_state(
            [
                managed(
                    "aws_dynamodb_table",
                    "this",
                    [
                        instance(
                            {
                                "arn": "arn:aws:dynamodb:eu-central-1:266579820564:table/t"
                            }
                        )
                    ],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["location"], "eu-central-1")
        self.assertNotIn("266579820564", json.dumps(records))

    def test_address_includes_module_and_index_key(self):
        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [instance({"region": "eu-west-1"}, index_key=3)],
                    module="module.storage",
                ),
                managed("aws_vpc", "main", [instance({"region": "eu-west-1"})]),
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(records[0]["address"], "module.storage.aws_s3_bucket.this[3]")
        self.assertEqual(records[1]["address"], "aws_vpc.main")

    def test_never_copies_attributes(self):
        state = build_state(
            [
                managed(
                    "azurerm_storage_account",
                    "this",
                    [
                        instance(
                            {
                                "location": "westeurope",
                                "primary_connection_string": "SUPER_SECRET_VALUE",
                            }
                        )
                    ],
                )
            ]
        )

        records = extract_managed_resources(state)

        self.assertEqual(set(records[0].keys()), {"address", "type", "location"})
        self.assertNotIn("SUPER_SECRET_VALUE", json.dumps(records))


class StateScopeTests(unittest.TestCase):
    def test_sha256_matches_the_file_contents(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = write_state(tmp_dir, build_state([]))
            expected = hashlib.sha256(Path(path).read_bytes()).hexdigest()

            self.assertEqual(file_sha256(path), expected)

    def test_sha256_returns_none_for_unreadable_file(self):
        self.assertIsNone(file_sha256("/tmp/does-not-exist.tfstate"))

    def test_extracts_azure_subscription_and_resource_groups(self):
        state = build_state(
            [
                managed(
                    "azurerm_storage_account",
                    "this",
                    [
                        instance(
                            {
                                "id": "/subscriptions/sub-a/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/x"
                            }
                        ),
                        instance(
                            {
                                "id": "/subscriptions/sub-a/resourceGroups/rg-2/providers/Microsoft.Storage/storageAccounts/y"
                            }
                        ),
                    ],
                )
            ]
        )

        scope = extract_state_scope(state, 1)

        self.assertEqual(scope["subscriptions"], ["sub-a"])
        self.assertEqual(scope["resource_groups"], ["rg-1", "rg-2"])

    def test_handles_multiple_subscriptions(self):
        state = build_state(
            [
                managed(
                    "azurerm_managed_disk",
                    "this",
                    [
                        instance(
                            {
                                "id": "/subscriptions/sub-b/resourceGroups/rg-1/providers/x"
                            }
                        ),
                        instance(
                            {
                                "id": "/subscriptions/sub-a/resourceGroups/rg-1/providers/x"
                            }
                        ),
                    ],
                )
            ]
        )

        scope = extract_state_scope(state, 1)

        self.assertEqual(scope["subscriptions"], ["sub-a", "sub-b"])

    def test_ignores_data_sources_and_unparsable_ids(self):
        state = build_state(
            [
                {
                    "mode": "data",
                    "type": "azurerm_resource_group",
                    "name": "this",
                    "instances": [
                        instance(
                            {
                                "id": "/subscriptions/data-sub/resourceGroups/rg-x/providers/y"
                            }
                        )
                    ],
                },
                managed("azurerm_managed_disk", "a", [instance({"id": "not-an-id"})]),
                managed("azurerm_managed_disk", "b", [instance({"id": 42})]),
                managed("azurerm_managed_disk", "c", [instance()]),
            ]
        )

        scope = extract_state_scope(state, 1)

        self.assertEqual(scope["subscriptions"], [])
        self.assertEqual(scope["resource_groups"], [])

    def test_aws_state_yields_no_subscription_or_resource_group(self):
        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [instance({"region": "eu-west-1", "id": "my-bucket"})],
                )
            ]
        )

        scope = extract_state_scope(state, 2)

        self.assertEqual(scope, {"subscriptions": [], "resource_groups": []})


class BuildTfstateResourceInventoryTests(unittest.TestCase):
    AWS_ROWS = [
        (444, 2, "AWS.s3.list_buckets.Buckets", "S3 Bucket", "t", "aws_s3_bucket"),
        (
            300,
            2,
            "AWS.dynamodb.list_tables.TableNames",
            "DynamoDB Table",
            "t",
            "aws_dynamodb_table",
        ),
        (
            500,
            2,
            "AWS.ec2.describe_instances.Reservations",
            "EC2 Instance",
            "f",
            "aws_instance",
        ),
        (
            600,
            1,
            "Microsoft.Compute/virtualMachines",
            "Virtual Machine",
            "t",
            "azurerm_linux_virtual_machine",
        ),
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.report_path = self._tmp.name
        self.raw_data_path = os.path.join(self.report_path, "raw_data")
        os.makedirs(os.path.join(self.report_path, "data"))
        os.makedirs(self.raw_data_path)
        self.db_path = os.path.join(self.report_path, "data", "assessment.db")

    def _inventory(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT resource_type, location, count FROM resource_inventory "
            "ORDER BY resource_type, location"
        ).fetchall()
        conn.close()
        return rows

    def _manifest(self):
        path = Path(self.raw_data_path) / "tfstate_manifest.json"
        return json.loads(path.read_text(encoding="utf-8")), path.read_text(
            encoding="utf-8"
        )

    def _build(self, state, csp=2, rows=None, filename="infra.tfstate"):
        seed_db(self.db_path, self.AWS_ROWS if rows is None else rows)
        state_path = write_state(self._tmp.name, state, filename=filename)
        build_tfstate_resource_inventory(
            csp,
            {"tfstatePath": state_path},
            self.report_path,
            self.raw_data_path,
        )
        return state_path

    def _build_returning(self, state, csp=2, rows=None):
        seed_db(self.db_path, self.AWS_ROWS if rows is None else rows)
        state_path = write_state(self._tmp.name, state)
        return build_tfstate_resource_inventory(
            csp,
            {"tfstatePath": state_path},
            self.report_path,
            self.raw_data_path,
        )

    def test_arn_derived_regions_aggregate_and_stay_out_of_the_manifest(self):
        state = build_state(
            [
                managed(
                    "aws_dynamodb_table",
                    "tables",
                    [
                        instance(
                            {
                                "arn": "arn:aws:dynamodb:eu-central-1:266579820564:table/a"
                            },
                            index_key="a",
                        ),
                        instance(
                            {
                                "arn": "arn:aws:dynamodb:eu-central-1:266579820564:table/b"
                            },
                            index_key="b",
                        ),
                        instance(
                            {"arn": "arn:aws:dynamodb:us-east-1:266579820564:table/c"},
                            index_key="c",
                        ),
                    ],
                )
            ]
        )

        self._build(state)

        self.assertEqual(
            self._inventory(),
            [(300, "eu-central-1", 2), (300, "us-east-1", 1)],
        )

        _, serialized = self._manifest()
        self.assertNotIn("266579820564", serialized)
        self.assertNotIn("arn:aws", serialized)

    def test_aggregates_matched_types_per_location(self):
        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [
                        instance({"region": "eu-west-1"}, index_key=0),
                        instance({"region": "eu-west-1"}, index_key=1),
                        instance({"region": "us-east-1"}, index_key=2),
                    ],
                ),
                managed(
                    "aws_dynamodb_table",
                    "tables",
                    [instance({"region": "eu-west-1"})],
                ),
            ]
        )

        self._build(state)

        self.assertEqual(
            self._inventory(),
            [
                (300, "eu-west-1", 1),
                (444, "eu-west-1", 2),
                (444, "us-east-1", 1),
            ],
        )

    def test_unmapped_types_are_skipped_and_recorded_in_manifest(self):
        state = build_state(
            [
                managed("aws_s3_bucket", "this", [instance({"region": "eu-west-1"})]),
                managed(
                    "aws_s3_bucket_versioning",
                    "this",
                    [
                        instance({"region": "eu-west-1"}, index_key=0),
                        instance({"region": "eu-west-1"}, index_key=1),
                    ],
                ),
            ]
        )

        self._build(state)

        self.assertEqual(self._inventory(), [(444, "eu-west-1", 1)])

        manifest, _ = self._manifest()
        self.assertEqual(manifest["unmapped_types"], {"aws_s3_bucket_versioning": 2})
        self.assertEqual(
            [entry["type"] for entry in manifest["counted"]], ["aws_s3_bucket"]
        )

    def test_duplicate_tf_code_resolves_to_lowest_id(self):
        rows = [
            (444, 2, "AWS.s3.list_buckets.Buckets", "S3 Bucket", "t", "aws_s3_bucket"),
            (
                293,
                2,
                "AWS.glacier.list_vaults.VaultList",
                "Glacier Vault",
                "t",
                "aws_s3_bucket",
            ),
        ]
        state = build_state(
            [managed("aws_s3_bucket", "this", [instance({"region": "eu-west-1"})])]
        )

        self._build(state, rows=rows)

        self.assertEqual(self._inventory(), [(293, "eu-west-1", 1)])

    def test_ignores_disabled_rows_and_other_csp_rows(self):
        state = build_state(
            [
                managed("aws_instance", "this", [instance({"region": "eu-west-1"})]),
                managed(
                    "azurerm_linux_virtual_machine",
                    "this",
                    [instance({"location": "westeurope"})],
                ),
            ]
        )

        self._build(state, csp=2)

        self.assertEqual(self._inventory(), [])
        manifest, _ = self._manifest()
        # Same-provider glue and foreign resources are reported separately.
        self.assertEqual(manifest["unmapped_types"], {"aws_instance": 1})
        self.assertEqual(
            manifest["other_provider_types"], {"azurerm_linux_virtual_machine": 1}
        )

    def test_rejects_state_with_no_resources_for_the_selected_provider(self):
        state = build_state(
            [
                managed(
                    "azurerm_storage_account",
                    "this",
                    [
                        instance({"location": "westeurope"}, index_key=0),
                        instance({"location": "westeurope"}, index_key=1),
                    ],
                )
            ]
        )
        seed_db(self.db_path, self.AWS_ROWS)
        state_path = write_state(self._tmp.name, state)

        with self.assertRaises(ValueError) as ctx:
            build_tfstate_resource_inventory(
                2, {"tfstatePath": state_path}, self.report_path, self.raw_data_path
            )

        message = str(ctx.exception)
        self.assertIn("No AWS resources found", message)
        self.assertIn("2 Azure resources", message)
        self.assertIn("main.py azure --tfstate", message)

    def test_rejects_state_holding_only_unsupported_providers(self):
        state = build_state(
            [
                managed("google_storage_bucket", "this", [instance({})]),
                managed("cloudflare_record", "this", [instance({})]),
            ]
        )
        seed_db(self.db_path, self.AWS_ROWS)
        state_path = write_state(self._tmp.name, state)

        with self.assertRaises(ValueError) as ctx:
            build_tfstate_resource_inventory(
                2, {"tfstatePath": state_path}, self.report_path, self.raw_data_path
            )

        message = str(ctx.exception)
        self.assertIn("No AWS resources found", message)
        self.assertIn("other providers", message)
        # Nothing to redirect to — cloudexit cannot assess these.
        self.assertNotIn("Did you mean", message)

    def test_empty_state_does_not_raise(self):
        coverage = self._build_returning(build_state([]))

        self.assertEqual(coverage["instances_total"], 0)
        self.assertEqual(self._inventory(), [])

    def test_mixed_state_counts_own_and_reports_excluded(self):
        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [
                        instance({"region": "eu-west-1"}, index_key=0),
                        instance({"region": "eu-west-1"}, index_key=1),
                        instance({"region": "eu-west-1"}, index_key=2),
                    ],
                ),
                managed(
                    "azurerm_storage_account",
                    "this",
                    [
                        instance({"location": "westeurope"}, index_key=i)
                        for i in range(27)
                    ],
                ),
                managed("aws_s3_bucket_versioning", "this", [instance({})]),
            ]
        )

        coverage = self._build_returning(state)

        self.assertEqual(self._inventory(), [(444, "eu-west-1", 3)])
        self.assertEqual(
            coverage,
            {
                "instances_total": 31,
                "instances_counted": 3,
                "instances_excluded_other_provider": 27,
                "instances_excluded_unmapped": 1,
            },
        )

        manifest, _ = self._manifest()
        self.assertEqual(manifest["coverage"], coverage)
        self.assertEqual(
            manifest["other_provider_types"], {"azurerm_storage_account": 27}
        )
        self.assertEqual(manifest["unmapped_types"], {"aws_s3_bucket_versioning": 1})

    def test_manifest_carries_no_attributes(self):
        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [
                        instance(
                            {
                                "region": "eu-west-1",
                                "bucket": "my-bucket",
                                "generated_password": "SUPER_SECRET_VALUE",
                            }
                        )
                    ],
                )
            ]
        )

        state_path = self._build(state)

        manifest, serialized = self._manifest()
        self.assertNotIn("attributes", serialized)
        self.assertNotIn("SUPER_SECRET_VALUE", serialized)
        self.assertNotIn("my-bucket", serialized)
        self.assertEqual(manifest["source_file"], os.path.basename(state_path))
        self.assertNotIn(os.path.dirname(state_path), serialized)
        self.assertEqual(manifest["terraform_version"], "1.14.6")
        self.assertEqual(manifest["state_serial"], 7)
        self.assertEqual(
            set(manifest["counted"][0].keys()),
            {"address", "type", "resource_type_id", "location"},
        )

    def test_manifest_scope_block_records_file_state_and_locations(self):
        import hashlib

        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [
                        instance({"region": "eu-west-1"}, index_key=0),
                        instance({"region": "us-east-1"}, index_key=1),
                    ],
                )
            ]
        )

        state_path = self._build(state)

        manifest, _ = self._manifest()
        scope = manifest["scope"]
        self.assertEqual(scope["file"], "infra.tfstate")
        self.assertEqual(
            scope["sha256"], hashlib.sha256(Path(state_path).read_bytes()).hexdigest()
        )
        self.assertEqual(scope["lineage"], "e1c2f0c0-0000-0000-0000-000000000000")
        self.assertEqual(scope["serial"], 7)
        self.assertEqual(scope["locations"], ["eu-west-1", "us-east-1"])
        self.assertEqual(scope["subscriptions"], [])
        self.assertEqual(scope["resource_groups"], [])

    def test_rerun_is_idempotent(self):
        state = build_state(
            [
                managed(
                    "aws_s3_bucket",
                    "this",
                    [
                        instance({"region": "eu-west-1"}, index_key=0),
                        instance({"region": "eu-west-1"}, index_key=1),
                    ],
                )
            ]
        )

        state_path = self._build(state)
        build_tfstate_resource_inventory(
            2,
            {"tfstatePath": state_path},
            self.report_path,
            self.raw_data_path,
        )

        self.assertEqual(self._inventory(), [(444, "eu-west-1", 2)])

    def test_invalid_state_raises_value_error(self):
        seed_db(self.db_path, self.AWS_ROWS)

        with self.assertRaisesRegex(ValueError, "version 4"):
            build_tfstate_resource_inventory(
                2,
                {
                    "tfstatePath": write_state(
                        self._tmp.name, build_state([], version=3)
                    )
                },
                self.report_path,
                self.raw_data_path,
            )


if __name__ == "__main__":
    unittest.main()
