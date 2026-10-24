import json
import sqlite3
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from azure.core.credentials import AccessToken
from dateutil.relativedelta import relativedelta

from core import utils_azure
from core.utils_azure import (
    build_azure_cost_inventory,
    build_azure_resource_inventory,
    cost_result_dict,
    create_cost_client,
    create_resource_client,
    create_subscription_client,
    is_resource_inventory_empty,
    plain_json,
)
from core.utils_egress import GIB
from core.utils_egress_azure import build_egress_inventory
from tests.http_fixtures import fake_http, make_response
from utils.azure import select_resource_group, select_subscription

SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
GROUP = "rg-demo"
GROUP_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
STORAGE_ID = f"{GROUP_ID}/providers/Microsoft.Storage/storageAccounts/stdemo"
NETWORK_ID = f"{GROUP_ID}/providers/Microsoft.Network/virtualNetworks/vnet"
DISK_ID = f"{GROUP_ID}/providers/Microsoft.Compute/disks/disk1"

STORAGE_ACCOUNT = {
    "id": STORAGE_ID,
    "name": "stdemo",
    "type": "Microsoft.Storage/storageAccounts",
    "location": "westeurope",
    "tags": {"env": "demo"},
    "sku": {"name": "Standard_GRS", "tier": "Standard"},
    "kind": "StorageV2",
    "identity": {
        "type": "SystemAssigned",
        "principalId": "11111111-1111-1111-1111-111111111111",
        "tenantId": "22222222-2222-2222-2222-222222222222",
    },
    "managedBy": f"{GROUP_ID}/providers/Microsoft.Web/sites/app",
    "createdTime": "2026-01-02T03:04:05.0000000Z",
    "changedTime": "2026-02-03T04:05:06.0000000Z",
    "provisioningState": "Succeeded",
}
VIRTUAL_NETWORK = {
    "id": NETWORK_ID,
    "name": "vnet",
    "type": "Microsoft.Network/virtualNetworks",
    "location": " WestEurope ",
}
VIRTUAL_NETWORK_TWO = dict(VIRTUAL_NETWORK, id=NETWORK_ID + "-2", name="vnet-2")
DISK = {
    "id": DISK_ID,
    "name": "disk1",
    "type": "Microsoft.Compute/disks",
    "location": "westeurope",
}


class FakeCredential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("not-a-token", int(time.time()) + 3600)


def json_reply(body, status=200):
    response = make_response(status, json_body=body)
    # The SDK's transport sets a flag on, and closes, the urllib3 response
    response.raw = mock.MagicMock()
    response._content_consumed = True
    return response


def listing(*resources):
    return json_reply({"value": list(resources)})


class ShapeTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.credential = FakeCredential()
        self.details = {
            "credential": self.credential,
            "subscriptionId": SUBSCRIPTION,
            "resourceGroupName": GROUP,
        }
        # The mapping of resource types is cached on the function for a run
        self.addCleanup(self.forget_type_cache)
        self.forget_type_cache()

    def forget_type_cache(self):
        if hasattr(build_azure_resource_inventory, "_resource_type_cache"):
            del build_azure_resource_inventory._resource_type_cache

    def report(self, *tables):
        report = self.tmp / "report"
        (report / "data").mkdir(parents=True, exist_ok=True)
        raw = self.tmp / "raw"
        raw.mkdir(exist_ok=True)
        conn = sqlite3.connect(report / "data" / "assessment.db")
        conn.executescript(";".join(tables))
        conn.commit()
        conn.close()
        return str(report), str(raw)

    def rows(self, report, sql):
        conn = sqlite3.connect(Path(report) / "data" / "assessment.db")
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class ClientSurfaceTests(ShapeTestCase):
    def test_the_factories_build_clients_with_the_operations_the_cli_calls(self):
        resource_client = create_resource_client(self.credential, SUBSCRIPTION)
        self.assertTrue(callable(resource_client.resources.list_by_resource_group))
        self.assertTrue(callable(resource_client.resources.get_by_id))
        self.assertTrue(callable(resource_client.resource_groups.list))

        subscription_client = create_subscription_client(self.credential)
        self.assertTrue(callable(subscription_client.subscriptions.list))

        cost_client = create_cost_client(self.credential)
        self.assertTrue(callable(cost_client.query.usage))

    def test_the_clients_come_from_the_packages_the_sdk_now_splits_them_into(self):
        self.assertEqual(
            type(create_resource_client(self.credential, SUBSCRIPTION)).__module__,
            "azure.mgmt.resource.resources._client",
        )
        self.assertEqual(
            type(create_subscription_client(self.credential)).__module__,
            "azure.mgmt.resource.subscriptions._client",
        )

    def test_the_authorization_client_still_has_role_assignments(self):
        from azure.mgmt.authorization import AuthorizationManagementClient

        client = AuthorizationManagementClient(self.credential, SUBSCRIPTION)
        self.assertTrue(callable(client.role_assignments.list_for_scope))


class ListedResourceTests(ShapeTestCase):
    def listed(self, *resources):
        client = create_resource_client(self.credential, SUBSCRIPTION)
        with fake_http(listing(*resources)) as fake:
            return list(client.resources.list_by_resource_group(GROUP)), fake

    def test_a_listed_resource_exposes_what_the_inventory_reads(self):
        resources, fake = self.listed(STORAGE_ACCOUNT, VIRTUAL_NETWORK)

        storage, network = resources
        self.assertEqual(storage.type, "Microsoft.Storage/storageAccounts")
        self.assertEqual(storage.location, "westeurope")
        self.assertEqual(storage.id, STORAGE_ID)
        self.assertEqual(storage.name, "stdemo")
        self.assertEqual(network.location, " WestEurope ")
        self.assertIn(
            f"/resourcegroups/{GROUP}/resources", fake.requests[0].url.lower()
        )
        self.assertEqual(fake.requests[0].method, "GET")

    def test_the_sku_name_is_read_the_way_the_egress_collector_reads_it(self):
        (storage,), _ = self.listed(STORAGE_ACCOUNT)
        self.assertEqual(
            getattr(getattr(storage, "sku", None), "name", None), "Standard_GRS"
        )

        (network,), _ = self.listed(VIRTUAL_NETWORK)
        self.assertIsNone(getattr(getattr(network, "sku", None), "name", None))

    def test_a_resource_without_a_location_reads_as_none(self):
        (resource,), _ = self.listed({k: v for k, v in DISK.items() if k != "location"})
        self.assertIsNone(resource.location)

    def test_the_raw_data_form_is_plain_json_with_the_rest_names(self):
        resources, _ = self.listed(STORAGE_ACCOUNT, VIRTUAL_NETWORK)

        plain = json.loads(json.dumps([plain_json(r) for r in resources]))

        self.assertEqual(plain[0], STORAGE_ACCOUNT)
        self.assertEqual(plain[1], VIRTUAL_NETWORK)

    def test_plain_json_turns_models_lists_and_dates_into_json_types(self):
        (storage,), _ = self.listed(STORAGE_ACCOUNT)
        self.assertEqual(
            plain_json(storage.identity)["principalId"],
            STORAGE_ACCOUNT["identity"]["principalId"],
        )
        self.assertEqual(plain_json((1, [date(2026, 1, 2)])), [1, ["2026-01-02"]])
        self.assertEqual(plain_json(None), None)


class ResourceInventoryTests(ShapeTestCase):
    TABLES = (
        "CREATE TABLE resourcetype (id INTEGER PRIMARY KEY, csp INTEGER, code TEXT, "
        "name TEXT, icon TEXT, status TEXT)",
        "INSERT INTO resourcetype VALUES "
        "(11, 1, 'Microsoft.Storage/storageAccounts', 'Storage Account', '/i.png', 't')",
        "INSERT INTO resourcetype VALUES "
        "(12, 1, 'microsoft.network/virtualnetworks', 'Virtual Network', '/i.png', 't')",
        "CREATE TABLE resource_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "resource_type INTEGER NOT NULL, location TEXT NOT NULL, "
        "count INTEGER NOT NULL, UNIQUE(resource_type, location))",
    )

    def test_types_and_locations_are_counted_as_before(self):
        report, raw = self.report(*self.TABLES)
        resources = [STORAGE_ACCOUNT, VIRTUAL_NETWORK, VIRTUAL_NETWORK_TWO]
        # One listing for the emptiness check, one for the inventory
        with fake_http(listing(*resources), listing(*resources)) as fake:
            build_azure_resource_inventory(1, self.details, report, raw)

        self.assertEqual(
            self.rows(
                report,
                "SELECT resource_type, location, count FROM resource_inventory ORDER BY 1",
            ),
            [(11, "westeurope", 1), (12, "westeurope", 2)],
        )
        self.assertEqual(len(fake.requests), 2)

    def test_a_resource_without_a_location_is_counted_as_global(self):
        report, raw = self.report(*self.TABLES)
        global_resource = {k: v for k, v in VIRTUAL_NETWORK.items() if k != "location"}
        blank_resource = dict(VIRTUAL_NETWORK, id=NETWORK_ID + "-2", location="  ")
        resources = [STORAGE_ACCOUNT, global_resource, blank_resource]
        with fake_http(listing(*resources), listing(*resources)):
            build_azure_resource_inventory(1, self.details, report, raw)

        self.assertEqual(
            self.rows(
                report,
                "SELECT resource_type, location, count FROM resource_inventory ORDER BY 1, 2",
            ),
            [(11, "westeurope", 1), (12, "global", 2)],
        )

    def test_the_raw_file_holds_the_rest_shaped_resources(self):
        report, raw = self.report(*self.TABLES)
        with fake_http(listing(STORAGE_ACCOUNT), listing(STORAGE_ACCOUNT)):
            build_azure_resource_inventory(1, self.details, report, raw)

        saved = json.loads(
            (Path(raw) / "resource_inventory_raw_data.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved, [STORAGE_ACCOUNT])

    def test_an_empty_group_is_detected(self):
        with fake_http(listing()):
            self.assertTrue(
                is_resource_inventory_empty(self.credential, SUBSCRIPTION, GROUP)
            )
        with fake_http(listing(DISK)):
            self.assertFalse(
                is_resource_inventory_empty(self.credential, SUBSCRIPTION, GROUP)
            )


class SelectionTests(ShapeTestCase):
    def test_subscriptions_and_resource_groups_are_read_by_the_selection_prompts(self):
        subscription_client = create_subscription_client(self.credential)
        subscription = {
            "id": f"/subscriptions/{SUBSCRIPTION}",
            "subscriptionId": SUBSCRIPTION,
            "displayName": "Demo subscription",
            "state": "Enabled",
        }
        with fake_http(listing(subscription)):
            subscriptions = list(subscription_client.subscriptions.list())

        resource_client = create_resource_client(self.credential, SUBSCRIPTION)
        group = {"id": GROUP_ID, "name": GROUP, "location": "westeurope"}
        with fake_http(listing(group)):
            groups = list(resource_client.resource_groups.list())

        with (
            mock.patch("builtins.input", return_value="1"),
            mock.patch("utils.azure.console.print") as printed,
        ):
            chosen = select_subscription(subscriptions)
            name = select_resource_group(groups)

        self.assertEqual(chosen.subscription_id, SUBSCRIPTION)
        self.assertEqual(chosen.display_name, "Demo subscription")
        self.assertEqual(name, GROUP)
        self.assertIn(
            f"1. Demo subscription ({SUBSCRIPTION})",
            [call.args[0] for call in printed.call_args_list],
        )


class CostQueryTests(ShapeTestCase):
    TABLES = (
        "CREATE TABLE cost_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "month TEXT NOT NULL, cost REAL NOT NULL, currency TEXT NOT NULL, "
        "UNIQUE(month))",
    )

    def months(self, count):
        first = date.today().replace(day=1)
        return [first - relativedelta(months=back) for back in range(count)]

    def result(self, rows):
        return json_reply(
            {
                "id": "providers/Microsoft.CostManagement/query/demo",
                "name": "demo",
                "type": "Microsoft.CostManagement/query",
                "properties": {
                    "columns": [
                        {"name": "Cost", "type": "Number"},
                        {"name": "UsageDate", "type": "Datetime"},
                        {"name": "Currency", "type": "String"},
                    ],
                    "rows": rows,
                },
            }
        )

    def test_the_query_is_sent_with_its_period_and_dataset(self):
        report, raw = self.report(*self.TABLES)
        with fake_http(self.result([])) as fake:
            build_azure_cost_inventory(1, self.details, report, raw)

        (request,) = fake.requests
        self.assertEqual(request.method, "POST")
        self.assertIn(
            f"{GROUP_ID}/providers/Microsoft.CostManagement/query", request.url
        )
        body = json.loads(request.kwargs["data"])
        self.assertEqual(body["type"], "Usage")
        self.assertEqual(body["timeframe"], "Custom")
        self.assertEqual(
            sorted(body["timePeriod"]),
            ["from", "to"],
        )
        self.assertEqual(body["dataset"]["granularity"], "Monthly")
        self.assertEqual(
            body["dataset"]["aggregation"],
            {"totalCost": {"name": "Cost", "function": "Sum"}},
        )

    def test_cost_rows_become_months_and_the_gaps_are_filled_with_zero(self):
        report, raw = self.report(*self.TABLES)
        current, previous = self.months(2)
        rows = [
            [100.5, f"{previous.isoformat()}T00:00:00", "EUR"],
            [20.25, f"{current.isoformat()}T00:00:00", "EUR"],
        ]
        with fake_http(self.result(rows)):
            build_azure_cost_inventory(1, self.details, report, raw)

        stored = dict(
            (month, (cost, currency))
            for month, cost, currency in self.rows(
                report, "SELECT month, cost, currency FROM cost_inventory"
            )
        )
        self.assertEqual(stored[previous.isoformat()], (100.5, "EUR"))
        self.assertEqual(stored[current.isoformat()], (20.25, "EUR"))
        zero_months = [m for m, (cost, _) in stored.items() if cost == 0]
        self.assertEqual(len(zero_months), 4)
        self.assertEqual(len(stored), 6)

    def test_the_raw_file_keeps_the_columns_and_rows_at_the_top(self):
        report, raw = self.report(*self.TABLES)
        current = self.months(1)[0]
        rows = [[1.5, f"{current.isoformat()}T00:00:00", "USD"]]
        with fake_http(self.result(rows)):
            build_azure_cost_inventory(1, self.details, report, raw)

        saved = json.loads(
            (Path(raw) / "cost_inventory_raw_data.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["rows"], rows)
        self.assertEqual(
            [c["name"] for c in saved["columns"]], ["Cost", "UsageDate", "Currency"]
        )
        self.assertNotIn("properties", saved)

    def test_cost_result_dict_flattens_the_properties(self):
        client = create_cost_client(self.credential)
        with fake_http(self.result([[1, "2026-01-01T00:00:00", "USD"]])):
            result = client.query.usage(
                GROUP_ID,
                utils_azure.QueryDefinition(
                    type="Usage",
                    timeframe=utils_azure.TimeframeType.CUSTOM,
                    time_period={
                        "from": "2026-01-01T00:00:00Z",
                        "to": "2026-02-01T00:00:00Z",
                    },
                    dataset={"granularity": "Monthly"},
                ),
            )

        self.assertEqual(result.rows, [[1, "2026-01-01T00:00:00", "USD"]])
        flat = cost_result_dict(result)
        self.assertEqual(flat["rows"], result.rows)
        self.assertIn("columns", flat)
        self.assertNotIn("properties", flat)


REGISTRY = {
    "microsoft.storage/storageaccounts": {
        "category": "object",
        "label": "Storage Account",
        "strategy": "storage_account_metrics",
    },
    "microsoft.compute/disks": {
        "category": "block",
        "label": "Managed Disks",
        "strategy": "allocated_size_property",
        "api_version": "2024-03-02",
        "size_property": "diskSizeGB",
    },
}


@mock.patch("core.utils_egress_azure.load_egress_registry", return_value=REGISTRY)
class EgressSizingTests(ShapeTestCase):
    def test_an_allocated_size_is_read_with_the_api_version_as_a_keyword(self, _):
        client = create_resource_client(self.credential, SUBSCRIPTION)
        disk = dict(DISK, properties={"diskSizeGB": 128})
        with fake_http(listing(DISK), json_reply(disk)) as fake:
            resources = list(client.resources.list_by_resource_group(GROUP))
            rows, findings = build_egress_inventory(self.credential, client, resources)

        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["size_bytes"], 128 * GIB)
        self.assertIn("allocated (upper bound)", rows[0]["flags"])
        lookup = fake.requests[1]
        self.assertEqual(lookup.method, "GET")
        self.assertIn(DISK_ID, lookup.url)
        self.assertIn("api-version=2024-03-02", lookup.url)

    def test_a_storage_account_sku_marks_geo_replication(self, _):
        client = create_resource_client(self.credential, SUBSCRIPTION)
        with fake_http(listing(STORAGE_ACCOUNT)):
            resources = list(client.resources.list_by_resource_group(GROUP))
        metrics = [
            {"UsedCapacity": [{"dimension": None, "value": float(GIB)}]},
            {"BlobCapacity": []},
        ]
        with mock.patch(
            "core.utils_egress_azure.fetch_monitor_metrics", side_effect=metrics
        ):
            rows, findings = build_egress_inventory(self.credential, client, resources)

        self.assertEqual(rows[0]["size_bytes"], GIB)
        self.assertIn("geo-replicated (Standard_GRS)", rows[0]["notes"])
        self.assertEqual([f["severity"] for f in findings], ["info"])


if __name__ == "__main__":
    unittest.main()
