import copy
import hashlib
import hmac
import json
import os
import stat
import tempfile
import unittest
from argparse import Namespace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from cloudexit.cli import app
from cloudexit.cli.parser import parse_arguments
from cloudexit.platform import document as platform_document
from cloudexit.platform.document import (
    SCHEMA_VERSION,
    RunContext,
    ScopeError,
    SyncDocumentError,
    build_sync_document,
    decimal_amount,
    new_uuid7,
    prepare_sync_document,
    salt_path,
    scope_key,
    validate_document,
)
from tests.support.isolation import isolated_environ
from tests.support.sync import (
    ACCESS_KEY,
    CLIENT_SECRET,
    COMPLETED_AT,
    DEFINITION,
    SECRET_KEY,
    SECRETS,
    SESSION_TOKEN,
    STARTED_AT,
    SYNC_ID,
    build_run,
    egress_row,
    schema_errors,
)
from cloudexit import codes
from cloudexit.version import __version__

LOGGER = "cloudexit.platform.document"
AWS_DETAILS = {
    "accessKey": ACCESS_KEY,
    "secretKey": SECRET_KEY,
    "sessionToken": SESSION_TOKEN,
    "region": "eu-central-1",
    "accountId": "123456789012",
}
AZURE_DETAILS = {
    "tenantId": "11111111-1111-4111-8111-111111111111",
    "subscriptionId": "22222222-2222-4222-8222-222222222222",
    "resourceGroupName": "Prod-RG",
    "clientId": "33333333-3333-4333-8333-333333333333",
    "clientSecret": CLIENT_SECRET,
    "credential": object(),
}
CLAIM = {
    "version": "2026-11-02",
    "sequence": 41,
    "sha256": "c" * 64,
}


class SyncDocumentTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = patch.dict(os.environ, isolated_environ(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)

    def report(self, name=None, **kwargs):
        self.reports = getattr(self, "reports", 0) + 1
        return build_run(self.tmp / (name or f"report-{self.reports}"), **kwargs)

    def context(self, provider=2, details=None, **overrides):
        values = dict(
            sync_id=SYNC_ID,
            started_at=STARTED_AT,
            cloud_service_provider=provider,
            assessment_type=2,
            provider_details=details
            or (AWS_DETAILS if provider == 2 else AZURE_DETAILS),
            completed_at=COMPLETED_AT,
        )
        values.update(overrides)
        return RunContext(**values)

    def assert_valid(self, document):
        self.assertEqual(schema_errors(document), [])
        validate_document(document)


AWS_RESOURCES = [
    (1, "eu-central-1", 8),
    (4, "eu-central-1", 2),
    (2, "eu-central-1", 36),
]
AWS_COSTS = [("2026-09-01", 1234.5600000000002, "USD"), ("2026-10-01", 15.5, "USD")]


def resource(type_id, code, location, count):
    return {"type_id": type_id, "type_code": code, "location": location, "count": count}


class GoldenDocumentTests(SyncDocumentTestCase):
    def test_aws_api_run(self):
        report = self.report(resources=AWS_RESOURCES, costs=AWS_COSTS)

        document = build_sync_document(report, self.context(2))

        self.assertEqual(
            document,
            {
                "schema_version": "2.0",
                "sync": {
                    "id": SYNC_ID,
                    "started_at": STARTED_AT,
                    "completed_at": COMPLETED_AT,
                    "collector": {
                        "name": "cloudexit",
                        "version": __version__,
                        "mode": "api",
                        "catalogue": {**CLAIM, "resource_type_ids": [1, 4]},
                    },
                },
                "assessment_type": 2,
                "scope": {
                    "provider": "aws",
                    "aws": {"account_id": "123456789012", "regions": ["eu-central-1"]},
                },
                "inventory": {
                    "resources": [
                        resource(1, "AWS.EC2.DescribeInstances", "eu-central-1", 8),
                        resource(2, "AWS.S3.ListBuckets", "eu-central-1", 36),
                        resource(4, "AWS.RDS.DescribeDBInstances", "eu-central-1", 2),
                    ],
                    "costs": [
                        {
                            "month": "2026-09",
                            "amount": "1234.56",
                            "currency": "USD",
                            "final": True,
                        },
                        {
                            "month": "2026-10",
                            "amount": "15.5",
                            "currency": "USD",
                            "final": False,
                        },
                    ],
                    "egress": {
                        "collected": False,
                        "truncated": False,
                        "names": "plain",
                        "items": [],
                    },
                },
            },
        )
        self.assert_valid(document)

    def test_azure_api_run(self):
        report = self.report(
            resources=[(3, "westeurope", 5)], costs=[("2026-10-01", 7, "EUR")]
        )

        document = build_sync_document(report, self.context(1, assessment_type=1))

        self.assertEqual(
            document["scope"],
            {
                "provider": "azure",
                "azure": {
                    "tenant_id": AZURE_DETAILS["tenantId"],
                    "subscription_id": AZURE_DETAILS["subscriptionId"],
                    "resource_group": "Prod-RG",
                },
            },
        )
        self.assertEqual(document["assessment_type"], 1)
        self.assertEqual(
            document["sync"]["collector"]["catalogue"]["resource_type_ids"], [3]
        )
        self.assertEqual(
            document["inventory"]["resources"],
            [resource(3, "Azure.Compute.VirtualMachines", "westeurope", 5)],
        )
        self.assertEqual(
            document["inventory"]["costs"],
            [{"month": "2026-10", "amount": "7", "currency": "EUR", "final": False}],
        )
        self.assert_valid(document)

    def test_azure_subscription_wide_scope_has_no_resource_group(self):
        details = {**AZURE_DETAILS, "resourceGroupName": ""}
        document = build_sync_document(
            self.report(resources=[(3, "westeurope", 1)]), self.context(1, details)
        )
        self.assertIsNone(document["scope"]["azure"]["resource_group"])
        self.assert_valid(document)

    def test_tfstate_run(self):
        report = self.report(
            resources=[(1, "eu-west-1", 3)],
            tfstate_scope={
                "file": "infra.tfstate",
                "lineage": "e1c2f0c0-0000-4000-8000-000000000000",
                "serial": 7,
            },
        )
        details = {"tfstatePath": "infra.tfstate"}

        document = build_sync_document(
            report, self.context(2, details, assessment_type=1)
        )

        self.assertEqual(document["sync"]["collector"]["mode"], "tfstate")
        self.assertEqual(
            document["scope"],
            {
                "provider": "aws",
                "tfstate": {
                    "lineage": "e1c2f0c0-0000-4000-8000-000000000000",
                    "serial": 7,
                },
            },
        )
        self.assertEqual(document["inventory"]["costs"], [])
        self.assert_valid(document)

    def test_a_tfstate_without_a_lineage_cannot_be_described(self):
        report = self.report(tfstate_scope={"lineage": None, "serial": 1})
        with self.assertRaises(SyncDocumentError):
            build_sync_document(
                report, self.context(2, {"tfstatePath": "infra.tfstate"})
            )

    def test_a_dataset_without_catalogue_meta_leaves_the_catalogue_out(self):
        report = self.report(resources=AWS_RESOURCES, meta=False)

        document = build_sync_document(report, self.context(2))

        self.assertNotIn("catalogue", document["sync"]["collector"])
        self.assert_valid(document)

    def test_a_missing_aws_account_id_is_an_error(self):
        details = {k: v for k, v in AWS_DETAILS.items() if k != "accountId"}
        with self.assertRaises(SyncDocumentError) as ctx:
            build_sync_document(self.report(), self.context(2, details))
        self.assertIn("/aws/account_id", str(ctx.exception))

    def test_assessment_type_follows_the_run_depth(self):
        report = self.report(resources=AWS_RESOURCES)
        for assessment_type, collected, expected in (
            (1, False, 1),
            (2, False, 2),
            (1, True, 3),
            (2, True, 3),
        ):
            with self.subTest(assessment_type=assessment_type, collected=collected):
                document = build_sync_document(
                    report,
                    self.context(
                        2,
                        assessment_type=assessment_type,
                        egress_collected=collected,
                    ),
                )
                self.assertEqual(document["assessment_type"], expected)

    def test_the_target_and_ci_are_left_out_unless_given(self):
        document = build_sync_document(self.report(), self.context(2))
        self.assertNotIn("target", document)
        self.assertNotIn("ci", document["sync"])

    def test_the_collector_version_and_schema_version(self):
        document = build_sync_document(self.report(), self.context(2))
        version = (DEFINITION / "VERSION").read_text(encoding="utf-8").strip()
        major, minor, _ = version.split(".")

        self.assertEqual(SCHEMA_VERSION, f"{major}.{minor}")
        self.assertEqual(document["schema_version"], SCHEMA_VERSION)
        self.assertEqual(document["sync"]["collector"]["version"], __version__)


class EgressTests(SyncDocumentTestCase):
    ROWS = [
        egress_row(2, "bucket-b", None, notes=["size unknown"]),
        egress_row(
            1,
            "web-1",
            5_000_000,
            flags=["encrypted"],
            notes=["n1"],
            tiers=[("GLACIER", 2_000_000, 1), ("STANDARD", 3_000_000, 0)],
        ),
    ]

    def egress(self, rows=None, **overrides):
        report = self.report(
            resources=AWS_RESOURCES, egress=self.ROWS if rows is None else rows
        )
        context = self.context(2, egress_collected=True, **overrides)
        return build_sync_document(report, context)

    def test_the_egress_block_from_the_run_tables(self):
        document = self.egress()

        self.assertEqual(document["assessment_type"], 3)
        self.assertEqual(
            document["inventory"]["egress"],
            {
                "collected": True,
                "truncated": False,
                "names": "plain",
                "items": [
                    {
                        "type_id": 1,
                        "name": "web-1",
                        "size_bytes": 5_000_000,
                        "flags": ["encrypted"],
                        "notes": ["n1"],
                        "tiers": [
                            {
                                "tier": "STANDARD",
                                "size_bytes": 3_000_000,
                                "is_archive": False,
                            },
                            {
                                "tier": "GLACIER",
                                "size_bytes": 2_000_000,
                                "is_archive": True,
                            },
                        ],
                    },
                    {
                        "type_id": 2,
                        "name": "bucket-b",
                        "size_bytes": None,
                        "flags": [],
                        "notes": ["size unknown"],
                        "tiers": [],
                    },
                ],
            },
        )
        self.assert_valid(document)

    def test_egress_that_was_not_collected_is_not_sent(self):
        report = self.report(resources=AWS_RESOURCES, egress=self.ROWS)
        document = build_sync_document(report, self.context(2, egress_collected=False))

        self.assertEqual(
            document["inventory"]["egress"],
            {"collected": False, "truncated": False, "names": "plain", "items": []},
        )
        self.assertNotIn("web-1", json.dumps(document))
        self.assert_valid(document)

    def test_collected_with_no_rows_is_still_collected(self):
        document = self.egress(rows=[])
        egress = document["inventory"]["egress"]
        self.assertTrue(egress["collected"])
        self.assertFalse(egress["truncated"])
        self.assertEqual(egress["items"], [])

    def test_too_many_items_keep_the_largest_and_set_truncated(self):
        rows = [egress_row(1, f"secret-name-{n}", n + 1) for n in range(1005)]
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            document = self.egress(rows=rows)

        egress = document["inventory"]["egress"]
        self.assertTrue(egress["truncated"])
        self.assertEqual(len(egress["items"]), 1000)
        self.assertEqual(egress["items"][0]["size_bytes"], 1005)
        self.assertEqual(min(item["size_bytes"] for item in egress["items"]), 6)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("5 items dropped", logs.output[0])
        self.assertNotIn("secret-name", logs.output[0])
        self.assert_valid(document)

    def test_long_strings_and_lists_are_trimmed_without_logging_names(self):
        row = egress_row(
            1,
            "n" * 300,
            2**60,
            flags=[f"flag-{n}" + "f" * 80 for n in range(12)],
            notes=[f"note-{n}" for n in range(11)],
            tiers=[(f"tier-{n}" + "t" * 60, n, 0) for n in range(12)],
        )
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            document = self.egress(rows=[row])

        item = document["inventory"]["egress"]["items"][0]
        self.assertTrue(document["inventory"]["egress"]["truncated"])
        self.assertEqual(len(item["name"]), 255)
        self.assertEqual(item["size_bytes"], 2**53 - 1)
        self.assertEqual(len(item["flags"]), 10)
        self.assertTrue(all(len(flag) <= 64 for flag in item["flags"]))
        self.assertEqual(len(item["notes"]), 10)
        self.assertEqual(len(item["tiers"]), 10)
        self.assertTrue(all(len(tier["tier"]) <= 50 for tier in item["tiers"]))
        text = logs.output[0]
        for label in (
            "names shortened",
            "sizes capped",
            "flags dropped",
            "tiers dropped",
        ):
            self.assertIn(label, text)
        self.assertNotIn("nnnn", text)
        self.assert_valid(document)

    def test_untrimmed_egress_does_not_log_a_warning(self):
        with self.assertNoLogs(LOGGER, level="WARNING"):
            self.egress()


class HashedNamesTests(SyncDocumentTestCase):
    ROWS = [egress_row(1, "web-1", 10), egress_row(2, "bucket-b", 5)]

    def document(self, details=None, **overrides):
        report = self.report(
            name=f"report-{len(list(self.tmp.iterdir()))}",
            resources=AWS_RESOURCES,
            egress=self.ROWS,
        )
        context = self.context(
            2, details, egress_collected=True, egress_names="hashed", **overrides
        )
        return build_sync_document(report, context)

    def names(self, document):
        return {
            item["type_id"]: item["name"]
            for item in document["inventory"]["egress"]["items"]
        }

    def test_names_are_salted_hashes_and_marked_hashed(self):
        document = self.document()

        egress = document["inventory"]["egress"]
        self.assertEqual(egress["names"], "hashed")
        for name in self.names(document).values():
            self.assertRegex(name, r"^[0-9a-f]{64}$")
        self.assertNotIn("web-1", json.dumps(document))
        self.assert_valid(document)

    def test_the_hash_is_an_hmac_with_the_stored_scope_salt(self):
        document = self.document()

        salt_file = salt_path("aws:123456789012:eu-central-1")
        salt = bytes.fromhex(salt_file.read_text().strip())
        expected = hmac.new(salt, b"web-1", hashlib.sha256).hexdigest()
        self.assertEqual(self.names(document)[1], expected)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_the_salt_file_is_private(self):
        self.document()
        salt_file = salt_path("aws:123456789012:eu-central-1")
        self.assertEqual(stat.S_IMODE(salt_file.stat().st_mode), 0o600)

    def test_hashes_are_stable_within_a_scope(self):
        first = self.names(self.document())
        second = self.names(self.document())
        self.assertEqual(first, second)

    def test_hashes_differ_across_scopes(self):
        first = self.names(self.document())
        other = {**AWS_DETAILS, "accountId": "210987654321"}
        second = self.names(self.document(other))
        self.assertNotEqual(first[1], second[1])

    def test_plain_names_are_unchanged_and_need_no_salt(self):
        report = self.report(resources=AWS_RESOURCES, egress=self.ROWS)
        document = build_sync_document(report, self.context(2, egress_collected=True))
        self.assertEqual(
            {item["name"] for item in document["inventory"]["egress"]["items"]},
            {"web-1", "bucket-b"},
        )
        self.assertFalse((self.tmp / "data").exists())


class ScopeKeyVectorTests(unittest.TestCase):
    def test_the_published_vectors(self):
        vectors = json.loads(
            (DEFINITION / "vectors" / "scope_key.json").read_text(encoding="utf-8")
        )
        self.assertGreater(len(vectors["cases"]), 20)

        for case in vectors["cases"]:
            with self.subTest(case["name"]):
                expect = case["expect"]
                if "scope_key" in expect:
                    self.assertEqual(scope_key(case["scope"]), expect["scope_key"])
                else:
                    with self.assertRaises(ScopeError) as ctx:
                        scope_key(case["scope"])
                    if "pointer" in expect["error"]:
                        self.assertEqual(
                            ctx.exception.pointer, expect["error"]["pointer"]
                        )


class Uuid7Tests(unittest.TestCase):
    def test_layout(self):
        value = new_uuid7(now_ms=0x0193_9B8E_7C2A)

        self.assertRegex(
            value,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )
        self.assertTrue(value.startswith("01939b8e-7c2a-7"))

    def test_ids_sort_by_time(self):
        ids = [new_uuid7(now_ms=1_700_000_000_000 + step * 1000) for step in range(50)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 50)

    def test_the_default_id_is_a_version_7_uuid(self):
        self.assertRegex(new_uuid7(), platform_document.UUID7_RE.pattern)

    def test_the_fallback_is_used_without_a_native_implementation(self):
        with patch.object(platform_document.uuid, "uuid7", None, create=True):
            self.assertRegex(new_uuid7(), platform_document.UUID7_RE.pattern)


class DecimalAmountTests(unittest.TestCase):
    def test_amounts_are_exact_decimal_strings(self):
        cases = {
            0.1 + 0.2: "0.3",
            1234.5600000000002: "1234.56",
            5: "5",
            0.0: "0",
            -0.0000001: "0",
            1e-06: "0.000001",
            12.50: "12.5",
            "12.50": "12.5",
            Decimal("1E+3"): "1000",
            -3.75: "-3.75",
            123456789.12: "123456789.12",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(decimal_amount(value), expected)

    def test_no_amount_looks_like_a_float(self):
        for value in (1e-5, 1e15, 1 / 3, 99999999.99):
            self.assertRegex(decimal_amount(value), r"^-?[0-9]+(\.[0-9]+)?$")

    def test_a_bad_amount_is_an_error(self):
        for value in ("abc", float("nan"), float("inf"), 1e30):
            with self.subTest(value=value), self.assertRaises(SyncDocumentError):
                decimal_amount(value)


class InventoryTests(SyncDocumentTestCase):
    def test_counts_above_the_item_limit_are_split_and_sum_up(self):
        report = self.report(resources=[(1, "eu-central-1", 25000)])
        items = build_sync_document(report, self.context(2))["inventory"]["resources"]

        self.assertEqual([item["count"] for item in items], [10000, 10000, 5000])
        self.assertEqual({item["location"] for item in items}, {"eu-central-1"})

    def test_a_zero_count_is_kept(self):
        report = self.report(resources=[(1, "eu-central-1", 0)])
        items = build_sync_document(report, self.context(2))["inventory"]["resources"]
        self.assertEqual([item["count"] for item in items], [0])

    def test_more_than_twelve_months_keep_the_latest(self):
        costs = [
            (f"2025-{month:02d}-01", float(month), "USD") for month in range(1, 13)
        ]
        costs += [("2026-01-01", 13.0, "USD"), ("2026-02-01", 14.0, "USD")]
        report = self.report(costs=costs)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            items = build_sync_document(report, self.context(2))["inventory"]["costs"]

        self.assertEqual(len(items), 12)
        self.assertEqual(items[0]["month"], "2025-03")
        self.assertEqual(items[-1]["month"], "2026-02")
        self.assertIn("2 month(s) dropped", logs.output[0])

    def test_months_before_the_current_one_are_final(self):
        costs = [("2026-08-01", 1.0, "USD"), ("2026-10-01", 2.0, "USD")]
        items = build_sync_document(self.report(costs=costs), self.context(2))[
            "inventory"
        ]["costs"]
        self.assertEqual([item["final"] for item in items], [True, False])

    def test_an_unreadable_run_database_is_a_sync_document_error(self):
        with self.assertRaises(SyncDocumentError):
            build_sync_document(str(self.tmp / "missing"), self.context(2))


class NoCredentialsTests(SyncDocumentTestCase):
    def test_no_secret_value_or_credential_key_is_in_any_document(self):
        rows = [egress_row(1, "web-1", 10, notes=["ok"])]
        for provider in (1, 2):
            for collected in (False, True):
                with self.subTest(provider=provider, collected=collected):
                    report = self.report(
                        name=f"r-{provider}-{collected}",
                        resources=[(3 if provider == 1 else 1, "x", 1)],
                        egress=rows,
                    )
                    document = build_sync_document(
                        report,
                        self.context(
                            provider, egress_collected=collected, egress_names="hashed"
                        ),
                    )
                    text = json.dumps(document)

                    for secret in SECRETS:
                        self.assertNotIn(secret, text)
                    for key in (
                        "accessKey",
                        "secretKey",
                        "sessionToken",
                        "clientSecret",
                    ):
                        self.assertNotIn(key, text)
                    self.assertNotIn("credential", text.lower())

    def test_the_written_file_has_no_secrets_either(self):
        report = self.report(resources=AWS_RESOURCES)
        output = prepare_sync_document(report, self.context(2))
        text = output.path.read_text(encoding="utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, text)


class ValidationTests(SyncDocumentTestCase):
    def valid(self):
        report = self.report(
            resources=AWS_RESOURCES, costs=AWS_COSTS, egress=EgressTests.ROWS
        )
        return build_sync_document(report, self.context(2, egress_collected=True))

    def mutations(self):
        def set_path(path, value):
            def apply(doc):
                target = doc
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value

            return apply

        def remove(path):
            def apply(doc):
                target = doc
                for part in path[:-1]:
                    target = target[part]
                del target[path[-1]]

            return apply

        return {
            "count over the limit": set_path(
                ["inventory", "resources", 0, "count"], 10001
            ),
            "type id zero": set_path(["inventory", "resources", 0, "type_id"], 0),
            "float amount": set_path(["inventory", "costs", 0, "amount"], 1.5),
            "scientific amount": set_path(["inventory", "costs", 0, "amount"], "1e3"),
            "bad month": set_path(["inventory", "costs", 0, "month"], "2026-13"),
            "bad currency": set_path(["inventory", "costs", 0, "currency"], "US"),
            "missing sync id": remove(["sync", "id"]),
            "sync id not v7": set_path(
                ["sync", "id"], "01939b8e-7c2a-4d41-9a51-3c8f0a6e21d4"
            ),
            "empty regions": set_path(["scope", "aws", "regions"], []),
            "bad account id": set_path(["scope", "aws", "account_id"], "123"),
            "assessment type": set_path(["assessment_type"], 4),
            "bad egress names": set_path(["inventory", "egress", "names"], "other"),
            "egress flag too long": set_path(
                ["inventory", "egress", "items", 0, "flags"], ["f" * 65]
            ),
            "egress size too large": set_path(
                ["inventory", "egress", "items", 0, "size_bytes"], 2**53
            ),
            "missing inventory": remove(["inventory"]),
            "catalogue sha256": set_path(
                ["sync", "collector", "catalogue", "sha256"], "ABC"
            ),
        }

    def test_the_valid_document_passes_both_checks(self):
        self.assert_valid(self.valid())

    def test_the_runtime_check_and_the_schema_reject_the_same_mistakes(self):
        for name, mutate in self.mutations().items():
            with self.subTest(name):
                document = copy.deepcopy(self.valid())
                mutate(document)

                self.assertNotEqual(schema_errors(document), [], "schema accepts it")
                with self.assertRaises(SyncDocumentError):
                    validate_document(document)

    def test_rules_that_only_the_runtime_check_enforces(self):
        # The host applies these; the JSON schema cannot express them.
        for name, mutate in {
            "completed before started": lambda doc: doc["sync"].update(completed_at=1),
            "tfstate mode with an api scope": lambda doc: doc["sync"][
                "collector"
            ].update(mode="tfstate"),
        }.items():
            with self.subTest(name):
                document = copy.deepcopy(self.valid())
                mutate(document)
                with self.assertRaises(SyncDocumentError):
                    validate_document(document)

    def test_too_many_resource_items_are_rejected(self):
        document = self.valid()
        document["inventory"]["resources"] = [
            {"type_id": 1, "location": f"loc-{n}", "count": 1} for n in range(1001)
        ]
        self.assertNotEqual(schema_errors(document), [])
        with self.assertRaises(SyncDocumentError):
            validate_document(document)

    def test_a_document_over_the_body_limit_is_rejected(self):
        document = self.valid()
        document["inventory"]["egress"]["items"][0]["notes"] = ["x" * 255] * 10
        document["inventory"]["egress"]["items"] = [
            copy.deepcopy(document["inventory"]["egress"]["items"][0])
            for _ in range(500)
        ]
        with self.assertRaises(SyncDocumentError) as ctx:
            validate_document(document)
        self.assertIn("larger than", " ".join(ctx.exception.problems))

    def test_problems_name_the_pointer(self):
        document = self.valid()
        document["inventory"]["resources"][0]["count"] = -1
        with self.assertRaises(SyncDocumentError) as ctx:
            validate_document(document)
        self.assertIn("/inventory/resources/0/count", ctx.exception.problems[0])


class OutputTests(SyncDocumentTestCase):
    def prepare(self, **kwargs):
        report = self.report(resources=AWS_RESOURCES, costs=AWS_COSTS)
        return report, prepare_sync_document(report, self.context(2), **kwargs)

    def test_the_document_is_written_next_to_the_report(self):
        report, output = self.prepare()

        self.assertEqual(output.path, Path(report) / "raw_data" / "sync.json")
        document = json.loads(output.path.read_text(encoding="utf-8"))
        self.assertEqual(document["sync"]["id"], SYNC_ID)
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(output.size, output.path.stat().st_size)
        self.assertEqual(
            output.sha256, hashlib.sha256(output.path.read_bytes()).hexdigest()
        )
        self.assertIsNone(output.emitted)

    def test_emit_payload_writes_a_copy_named_after_the_sync_id(self):
        out_dir = self.tmp / "out" / "nested"
        report, output = self.prepare(emit_dir=str(out_dir))

        self.assertEqual(output.emitted, out_dir / f"{SYNC_ID}.json")
        self.assertEqual(output.emitted.read_bytes(), output.path.read_bytes())

    def test_the_summary_line_has_counts_and_a_hash_but_no_names(self):
        report = self.report(
            resources=AWS_RESOURCES,
            costs=AWS_COSTS,
            egress=[egress_row(1, "very-secret-name", 10, notes=["secret note"])],
        )
        with self.assertLogs(LOGGER, level="INFO") as logs:
            output = prepare_sync_document(
                report, self.context(2, egress_collected=True)
            )

        (line,) = logs.output
        self.assertIn(SYNC_ID, line)
        self.assertIn("3 resource item(s), 2 cost month(s)", line)
        self.assertIn("egress collected=true truncated=false (1 item(s))", line)
        self.assertIn(f"{output.size} bytes", line)
        self.assertIn(output.sha256, line)
        self.assertNotIn("very-secret-name", line)
        self.assertNotIn("secret note", line)
        self.assertEqual(output.summary, line.split(":", 2)[2])

    def test_the_document_is_not_dumped_at_debug_level(self):
        report = self.report(
            resources=AWS_RESOURCES,
            egress=[egress_row(1, "very-secret-name", 10)],
        )
        with self.assertLogs(level="DEBUG") as logs:
            prepare_sync_document(report, self.context(2, egress_collected=True))
        self.assertNotIn("very-secret-name", "\n".join(logs.output))

    def test_nothing_is_written_when_the_document_is_invalid(self):
        report = self.report(resources=AWS_RESOURCES)
        details = {**AWS_DETAILS, "region": "not a region"}
        with self.assertRaises(SyncDocumentError):
            prepare_sync_document(report, self.context(2, details))
        self.assertFalse((Path(report) / "raw_data" / "sync.json").exists())


class RunAssessmentTests(SyncDocumentTestCase):
    CONFIG = {
        "name": "Test Assessment",
        "cloudServiceProvider": 2,
        "exitStrategy": 1,
        "assessmentType": 1,
        "providerDetails": dict(AWS_DETAILS),
    }

    def run_assessment(self, report, *, egress_ok=None, **kwargs):
        raw_data = os.path.join(report, "raw_data")
        patches = [
            patch("cloudexit.cli.app.validate_config"),
            patch(
                "cloudexit.cli.app.create_directory", return_value=(report, raw_data)
            ),
            patch("cloudexit.cli.app.verify_credentials", return_value=(True, "ok")),
            patch(
                "cloudexit.cli.app.test_permissions",
                return_value=(True, True, True, "ok"),
            ),
            patch(
                "cloudexit.cli.app.create_resource_inventory",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "cloudexit.cli.app.create_cost_inventory",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "cloudexit.cli.app.perform_risk_assessment",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "cloudexit.cli.app.generate_report",
                return_value={"success": True, "reports": {}},
            ),
            patch("cloudexit.cli.app.add_run_log_handler"),
            patch("cloudexit.cli.app.print_step"),
            patch("cloudexit.cli.app.console.print"),
        ]
        if egress_ok is not None:
            patches += [
                patch(
                    "cloudexit.cli.app.estimate_egress",
                    return_value={"success": egress_ok, "logs": "x", "json_path": "p"},
                ),
                patch(
                    "cloudexit.cli.app.generate_egress_html_report",
                    return_value={"success": True, "html_path": "h"},
                ),
                patch(
                    "cloudexit.cli.app.generate_egress_pdf_report",
                    return_value={"success": True, "pdf_path": "p"},
                ),
            ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return app.run_assessment(copy.deepcopy(self.CONFIG), "aws", **kwargs)

    def test_every_run_writes_a_sync_document(self):
        report = self.report(resources=AWS_RESOURCES, costs=AWS_COSTS)

        self.run_assessment(report)

        document = json.loads(
            (Path(report) / "raw_data" / "sync.json").read_text(encoding="utf-8")
        )
        self.assertRegex(document["sync"]["id"], platform_document.UUID7_RE.pattern)
        self.assertEqual(document["assessment_type"], 1)
        self.assertFalse(document["inventory"]["egress"]["collected"])
        self.assertEqual(schema_errors(document), [])

    def test_the_sync_id_is_created_at_collection_start(self):
        report = self.report(resources=AWS_RESOURCES)
        with patch("cloudexit.cli.app.new_uuid7", return_value=SYNC_ID) as mock_id:
            self.run_assessment(report)

        mock_id.assert_called_once_with()
        document = json.loads(
            (Path(report) / "raw_data" / "sync.json").read_text(encoding="utf-8")
        )
        self.assertEqual(document["sync"]["id"], SYNC_ID)

    def test_emit_payload_and_hashed_names_reach_the_document(self):
        report = self.report(resources=AWS_RESOURCES, egress=EgressTests.ROWS)
        out_dir = self.tmp / "emitted"

        self.run_assessment(
            report,
            egress=True,
            egress_ok=True,
            emit_payload=str(out_dir),
            egress_names="hashed",
        )

        (emitted,) = out_dir.glob("*.json")
        document = json.loads(emitted.read_text(encoding="utf-8"))
        self.assertTrue(document["inventory"]["egress"]["collected"])
        self.assertEqual(document["inventory"]["egress"]["names"], "hashed")
        self.assertEqual(document["assessment_type"], 3)
        self.assertEqual(
            emitted.read_bytes(), (Path(report) / "raw_data" / "sync.json").read_bytes()
        )

    def test_a_failed_egress_stage_is_not_collected(self):
        report = self.report(resources=AWS_RESOURCES, egress=EgressTests.ROWS)

        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(report, egress=True, egress_ok=False)

        self.assertEqual(ctx.exception.code, codes.EGRESS)
        document = json.loads(
            (Path(report) / "raw_data" / "sync.json").read_text(encoding="utf-8")
        )
        self.assertFalse(document["inventory"]["egress"]["collected"])
        self.assertEqual(document["inventory"]["egress"]["items"], [])

    def test_a_problem_does_not_fail_a_plain_run(self):
        report = str(self.tmp / "no-database")
        os.makedirs(os.path.join(report, "raw_data"))

        self.run_assessment(report)

        self.assertFalse((Path(report) / "raw_data" / "sync.json").exists())
        statuses = [call.kwargs.get("status") for call in app.print_step.mock_calls]
        self.assertIn("warning", statuses)

    def test_a_problem_fails_a_dry_run_and_an_emit_request(self):
        for kwargs in ({"dry_run": True}, {"emit_payload": str(self.tmp / "out")}):
            with self.subTest(kwargs=kwargs):
                report = str(self.tmp / f"no-database-{len(kwargs)}{list(kwargs)[0]}")
                os.makedirs(os.path.join(report, "raw_data"))
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(report, **kwargs)
                self.assertEqual(ctx.exception.code, codes.REPORT)


class ArgumentTests(unittest.TestCase):
    def parse(self, *argv):
        with patch("sys.argv", ["cloudexit", *argv]):
            return parse_arguments()

    def test_defaults(self):
        for provider in ("aws", "azure"):
            args = self.parse(provider)
            self.assertIsNone(args.emit_payload)
            self.assertEqual(args.egress_names, "plain")

    def test_flags_on_aws_and_azure(self):
        for provider in ("aws", "azure"):
            args = self.parse(
                provider, "--emit-payload", "out", "--egress-names", "hashed"
            )
            self.assertEqual(args.emit_payload, "out")
            self.assertEqual(args.egress_names, "hashed")

    def test_other_values_for_the_names_option_are_refused(self):
        with (
            patch("sys.stderr"),
            self.assertRaises(SystemExit),
        ):
            self.parse("aws", "--egress-names", "obfuscated")

    def test_handlers_pass_the_options_on(self):
        env = {
            "ESC_EXIT_STRATEGY": "1",
            "ESC_ASSESSMENT_TYPE": "1",
            "AWS_ACCESS_KEY_ID": "x",
        }
        args = Namespace(
            config=None,
            profile=None,
            tfstate="infra.tfstate",
            name=None,
            non_interactive=True,
            dry_run=False,
            egress=False,
            emit_payload="out",
            egress_names="hashed",
        )
        with (
            patch.dict(os.environ, env),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(args)

        self.assertEqual(mock_run.call_args.kwargs["emit_payload"], "out")
        self.assertEqual(mock_run.call_args.kwargs["egress_names"], "hashed")


if __name__ == "__main__":
    unittest.main()
