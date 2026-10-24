import json
import re
import unittest
from urllib.parse import urlsplit

import yaml

from core import api_v2
from core.api_v2 import ERROR_EXIT_CODES, EXIT_STRATEGIES, PENDING_STATUSES
from core.spool import Spool, flush
from core.sync_document import PROVIDERS, SCHEMA_VERSION
from core.upload import upload
from tests.platform import test_client as base
from tests.cli import test_upload_modes as modes
from tests.support.api import (
    HOST,
    KEY,
    PLATFORM_SYNC_ID,
    created_reply,
    error_body,
    meta_body,
    meta_reply,
    reply,
    sync_body,
    whoami_body,
    whoami_reply,
)
from tests.support.http import fake_http
from tests.support.sync import DEFINITION, schema_errors
from utils import codes
from utils.settings import Setting, Settings
from tests.support.paths import ROOT

SPEC = yaml.safe_load((DEFINITION / "openapi.yaml").read_text(encoding="utf-8"))
ERRORS = yaml.safe_load((DEFINITION / "errors.yaml").read_text(encoding="utf-8"))
SERVER_PATH = urlsplit(SPEC["servers"][0]["url"].replace("{host}", "h")).path

# The definition version these tests were last reviewed against. A new version
# fails the guard below until someone has read what changed.
REVIEWED_DEFINITION = "2.0.0"

# Operations the CLI calls. The others read what a host stores and are not
# part of collecting and pushing.
USED_OPERATIONS = {
    ("get", "/meta"),
    ("get", "/whoami"),
    ("post", "/syncs"),
    ("get", "/syncs/{sync_id}"),
}
NOT_APPLICABLE_OPERATIONS = {
    ("get", "/assessments"): "listing assessments is not part of a push",
    (
        "get",
        "/assessments/{assessment_id}",
    ): "reading an assessment is not part of a push",
    (
        "get",
        "/assessments/{assessment_id}/syncs",
    ): "listing syncs is not part of a push",
}
# Error codes the CLI cannot meet on /api/v2
NOT_APPLICABLE_ERRORS = {
    "assessment_key_v1": "returned by /api/v1 in the v1 envelope, never by /api/v2",
}
# Where each warning of the definition reaches the CLI
WARNING_ROUTES = {
    "unknown_resource_types": "sync",
    "resource_type_inactive": "sync",
    "resource_type_code_mismatch": "sync",
    "duplicate_items_merged": "sync",
    "tier_downgraded": "sync",
    "name_ignored": "sync",
    "key_expiring": "whoami",
    "client_version_deprecated": "sync",
    "collector_catalogue_outdated": "sync",
    "host_catalogue_outdated": "sync",
    "collector_catalogue_unknown": "sync",
}
# Statuses of a finished sync that end the run well, and the one that does not
SUCCESS_STATUSES = ("completed", "completed_with_warnings")
FAILED_STATUS = "failed"
# Vectors the CLI has tests for, and the one it has no use for
VECTORS = {
    "scope_key.json": "tests/platform/test_document.py",
    "content_sha256.json": None,
}


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        target = SPEC
        for part in node["$ref"][2:].split("/"):
            target = target[part]
        node = target
    return node


def find_operation(method, path):
    for template, operations in SPEC["paths"].items():
        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$"
        if re.fullmatch(pattern, path) and method.lower() in operations:
            return template, operations[method.lower()]
    return None, None


def settings_for(**changes):
    fields = dict(
        host=Setting(HOST, "test"),
        api_key=Setting(KEY, "test"),
        upload_budget=Setting(0.0, "test"),
    )
    fields.update(changes)
    return Settings(**fields)


class ContractCase(base.ClientTestCase):
    def check_request(self, recorded, client, key=KEY):
        parsed = urlsplit(recorded.url)
        self.assertTrue(parsed.path.startswith(SERVER_PATH), recorded.url)
        template, operation = find_operation(
            recorded.method, parsed.path[len(SERVER_PATH) :]
        )
        self.assertIsNotNone(
            operation, f"{recorded.method} {parsed.path} is not in the definition"
        )

        headers = {
            name.lower(): value
            for name, value in {
                **client.session.headers,
                **recorded.kwargs.get("headers", {}),
            }.items()
        }
        if operation.get("security", SPEC.get("security")):
            self.assertEqual(headers.get("authorization"), f"Bearer {key}")
        else:
            self.assertNotIn("authorization", headers)

        declared = [resolve(p) for p in operation.get("parameters", [])]
        for parameter in declared:
            if parameter["in"] != "header":
                continue
            name = parameter["name"].lower()
            if parameter.get("required"):
                self.assertIn(name, headers, f"{template} needs {parameter['name']}")
            pattern = parameter.get("schema", {}).get("pattern")
            if name in headers and pattern:
                self.assertRegex(headers[name], pattern, parameter["name"])
        queries = {p["name"] for p in declared if p["in"] == "query"}
        sent = {q.split("=")[0] for q in parsed.query.split("&") if q}
        self.assertLessEqual(sent, queries)
        self.assertTrue(headers.get("user-agent"))

        if "requestBody" in operation:
            media = resolve(operation["requestBody"])["content"]
            self.assertIn(headers.get("content-type"), media)
            body = json.loads(recorded.kwargs["data"].decode("utf-8"))
            self.assertEqual(schema_errors(body), [])
            self.assertEqual(headers["idempotency-key"], body["sync"]["id"])
            return body
        self.assertIsNone(recorded.kwargs.get("data"))
        return None


class RequestContractTests(ContractCase):
    def test_meta_and_whoami(self):
        client = self.client()
        with fake_http(meta_reply(), whoami_reply()) as fake:
            client.meta()
            client.whoami()

        meta, who = fake.requests
        self.check_request(meta, client)
        self.check_request(who, client)
        self.assertEqual(find_operation("get", "/meta")[0], "/meta")

    def test_meta_is_asked_without_the_key(self):
        client = self.client()
        with fake_http(meta_reply()) as fake:
            client.meta()
        self.assertNotIn("Authorization", fake.requests[0].kwargs.get("headers", {}))

    def test_a_retried_push_repeats_the_same_request(self):
        client = self.client(budget=100.0, max_attempts=2)
        body = self.document()
        failed = reply(503, error_body("service_unavailable", retryable=True))
        with fake_http(meta_reply(), whoami_reply(), failed, created_reply()) as fake:
            client.push(body, name="n", exit_strategy=1)

        first, second = fake.requests[2:]
        self.assertEqual(first.kwargs["data"], second.kwargs["data"])
        self.assertEqual(
            first.kwargs["headers"]["Idempotency-Key"],
            second.kwargs["headers"]["Idempotency-Key"],
        )
        self.assertEqual(first.kwargs["headers"]["Idempotency-Key"], body["sync"]["id"])

    def test_the_poll_of_a_pending_sync_follows_the_definition(self):
        client = self.client()
        accepted = reply(202, sync_body(status="accepted"), **{"Retry-After": "1"})
        with fake_http(
            meta_reply(), whoami_reply(), accepted, reply(200, sync_body())
        ) as fake:
            client.push(self.document(), name="n", exit_strategy=1)

        poll = fake.requests[3]
        template, _ = find_operation(
            poll.method, urlsplit(poll.url).path[len(SERVER_PATH) :]
        )
        self.assertEqual(template, "/syncs/{sync_id}")
        self.check_request(poll, client)
        self.assertTrue(poll.url.endswith(PLATFORM_SYNC_ID))


class ExampleContractTests(base.ExampleSupport):
    def sent(self):
        return [
            number
            for number, example in self.examples.items()
            if example["request"]["method"] == "POST"
            and number not in base.NEVER_SENT
            and number not in base.REFUSED_LOCALLY
        ]

    def test_every_request_the_cli_sends_matches_the_definition(self):
        check = ContractCase()
        for number in self.sent():
            with self.subTest(number=number):
                _, request, fake, _, _ = self.run_example(number)
                post = fake.requests[2]
                body = check.check_request(
                    post, self.last_client, self.last_client._key
                )
                self.assertEqual(body, request["body"])

    def test_the_first_requests_of_a_push_match_the_definition_too(self):
        check = ContractCase()
        _, _, fake, _, _ = self.run_example("25")
        for recorded in fake.requests[:2]:
            check.check_request(recorded, self.last_client, self.last_client._key)

    def test_errors_are_retried_exactly_when_the_registry_says_so(self):
        checked = 0
        for number in self.sent():
            example = self.examples[number]
            subset = example["expect"].get("body_subset") or {}
            if subset.get("object") != "error":
                continue
            entry = ERRORS["errors"][subset["error"]["code"]]
            attempts = 3 if entry["retryable"] else 1
            with self.subTest(number=number, code=subset["error"]["code"]):
                self.slept.clear()
                _, _, fake, result, error = self.run_example(
                    number, attempts=attempts, budget=1000.0, max_attempts=3
                )
                self.assertIsNone(result)
                self.assertEqual(len(fake.requests) - 2, attempts)
                self.assertEqual(error.attempts, attempts)
                self.assertEqual(error.exit_code, entry["cli_exit"])
                self.assertEqual(len(self.slept), attempts - 1)
                checked += 1
        self.assertGreater(checked, 10)

    def test_the_replay_example_is_a_success_and_is_not_retried(self):
        example, _, fake, result, error = self.run_example("07")

        self.assertIsNone(error)
        self.assertTrue(result.replayed)
        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(example["expect"]["headers"]["Idempotent-Replayed"], "true")

    def test_the_warnings_of_every_example_reach_the_result(self):
        found = set()
        for number in self.sent():
            expect = self.examples[number]["expect"]
            wanted = [
                w["code"] for w in (expect.get("body_subset") or {}).get("warnings", [])
            ]
            if expect["status"] not in (200, 201) or not wanted:
                continue
            with self.subTest(number=number):
                _, _, _, result, error = self.run_example(number)
                self.assertIsNone(error)
                self.assertEqual([w["code"] for w in result.warning_items], wanted)
                found.update(wanted)
        self.assertGreaterEqual(len(found), 4)


class ErrorMatrixTests(base.ClientTestCase):
    def codes(self):
        return {
            code: entry
            for code, entry in ERRORS["errors"].items()
            if code not in NOT_APPLICABLE_ERRORS
        }

    def test_every_error_of_the_registry_is_retried_by_its_flag_and_honours_retry_after(
        self,
    ):
        for code, entry in self.codes().items():
            attempts = 3 if entry["retryable"] else 1
            with self.subTest(code=code):
                self.slept.clear()
                client = self.client(
                    budget=1000.0, max_attempts=3, cache_path=self.tmp / code
                )
                replies = [meta_reply(), whoami_reply()] + [
                    reply(
                        entry["http"],
                        error_body(code, retryable=entry["retryable"]),
                        **{"Retry-After": "7"},
                    )
                    for _ in range(attempts)
                ]
                with fake_http(*replies) as fake:
                    with self.assertRaises(api_v2.ApiError) as ctx:
                        client.push(self.document(), name="n", exit_strategy=1)

                self.assertEqual(len(fake.requests) - 2, attempts)
                self.assertEqual(ctx.exception.exit_code, entry["cli_exit"])
                self.assertEqual(ctx.exception.retryable, entry["retryable"])
                # Retry-After is waited as announced, and ignored when nothing is retried
                self.assertEqual(self.slept, [7.0] * (attempts - 1))

    def test_a_retryable_error_that_goes_away_ends_in_success(self):
        for code, entry in self.codes().items():
            if not entry["retryable"]:
                continue
            with self.subTest(code=code):
                client = self.client(
                    budget=1000.0, max_attempts=3, cache_path=self.tmp / code
                )
                failed = reply(entry["http"], error_body(code, retryable=True))
                with fake_http(meta_reply(), whoami_reply(), failed, created_reply()):
                    result = client.push(self.document(), name="n", exit_strategy=1)
                self.assertEqual(result.status, "completed")

    def test_the_exit_code_never_depends_on_the_wording(self):
        for code, entry in self.codes().items():
            with self.subTest(code=code):
                body = error_body(code, "Das hat nicht geklappt.", entry["retryable"])
                error, _ = self.failure(
                    [meta_reply(), whoami_reply(), reply(entry["http"], body)],
                    client=self.client(cache_path=self.tmp / code),
                )
                self.assertEqual(error.exit_code, entry["cli_exit"])


class WarningContractTests(modes.ModeTestCase):
    def item(self, code):
        return {
            "code": code,
            "message": f"The platform warns: {code}.",
            "details": {"type_ids": [981]},
        }

    def replies(self, code):
        item = self.item(code)
        if WARNING_ROUTES[code] == "whoami":
            return [meta_reply(), whoami_reply(warnings=[item]), created_reply()]
        return [meta_reply(), whoami_reply(), created_reply(warnings=[item])]

    def test_every_warning_is_shown_and_does_not_fail_a_normal_run(self):
        for code in WARNING_ROUTES:
            with self.subTest(code=code):
                self.run_assessment(
                    self.report(),
                    settings=modes.flow.CONFIGURED,
                    replies=self.replies(code),
                )

                shown = [
                    kwargs.get("logs")
                    for args, kwargs in self.steps
                    if args[0] == "Platform warning"
                ]
                self.assertEqual(shown, [api_v2.warning_line({**self.item(code)})])
                self.assertEqual(
                    [i["code"] for i in main_run()["upload"].warning_items], [code]
                )

    def test_every_warning_fails_a_strict_run_after_the_report(self):
        for code in WARNING_ROUTES:
            with self.subTest(code=code):
                report = self.report()
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(
                        report,
                        settings=modes.flow.CONFIGURED,
                        replies=self.replies(code),
                        strict=True,
                    )

                self.assertEqual(ctx.exception.code, codes.UPLOAD_REJECTED)
                self.assert_local_report(report)


def main_run():
    import main

    return main.RUN


class ReplayContractTests(base.ClientTestCase):
    def setUp(self):
        super().setUp()
        self.spool = Spool(self.tmp / "spool")
        self.settings = settings_for()

    def options(self):
        return {
            "cache_path": self.tmp / "cache",
            "sleep": self.sleep,
            "clock": lambda: self.now,
            "environ": {},
        }

    def replayed(self):
        return reply(200, sync_body(), **{"Idempotent-Replayed": "true"})

    def upload(self, replies, **kwargs):
        with fake_http(*replies):
            return upload(
                self.document(),
                self.settings,
                name="n",
                meta={"exit_strategy": 1},
                spool=self.spool,
                **self.options(),
                **kwargs,
            )

    def test_a_replayed_answer_is_a_success_and_spools_nothing(self):
        result = self.upload([meta_reply(), whoami_reply(), self.replayed()])

        self.assertTrue(result.success)
        self.assertTrue(result.replayed)
        self.assertEqual(self.spool.entries(), [])

    def test_a_spooled_sync_delivered_as_a_replay_leaves_no_second_entry(self):
        failed = reply(503, error_body("service_unavailable", retryable=True))
        first = self.upload([meta_reply(), whoami_reply(), failed])
        self.assertTrue(first.spooled)
        self.assertEqual(len(self.spool.entries()), 1)

        # The same sync fails again: still one entry for its id
        again = reply(503, error_body("service_unavailable", retryable=True))
        self.upload([whoami_reply(), again])
        self.assertEqual(len(self.spool.entries()), 1)

        with fake_http(whoami_reply(), self.replayed()):
            result = flush(self.settings, self.spool, **self.options())

        self.assertEqual(len(result.uploaded), 1)
        self.assertEqual(self.spool.entries(), [])


class ForwardCompatibilityTests(base.ClientTestCase):
    def test_unknown_fields_in_every_answer_are_ignored(self):
        meta = meta_body()
        meta["future_block"] = {"a": 1}
        meta["limits"]["max_future_things"] = 5
        meta["clients"]["cloudexit"]["future_policy"] = True
        who = whoami_body()
        who["key"]["future_flag"] = "x"
        who["future_block"] = [1, 2]
        sync = sync_body()
        sync["future_block"] = {"nested": {"deep": True}}
        sync["assessment"]["future_field"] = 1
        sync["catalogue"]["future_field"] = []
        sync["results"]["future_field"] = None

        with fake_http(reply(200, meta), reply(200, who), reply(201, sync)):
            result = self.client().push(self.document(), name="n", exit_strategy=1)

        self.assertEqual(result.status, "completed")

    def test_an_unknown_warning_code_is_shown_and_kept(self):
        item = {
            "code": "brand_new_warning",
            "message": "Something we have not met yet.",
            "details": {"anything": {"at": ["all"]}},
        }
        result, _ = self.push(
            [meta_reply(), whoami_reply(), created_reply(warnings=[item])]
        )

        self.assertEqual(
            [i["code"] for i in result.warning_items], ["brand_new_warning"]
        )
        self.assertTrue(
            any("Something we have not met yet." in w for w in result.warnings)
        )

    def test_an_unknown_error_code_is_judged_by_its_status_and_flag(self):
        for status, retryable, exit_code in (
            (422, False, codes.UPLOAD_REJECTED),
            (403, False, codes.UPLOAD_AUTH),
            (503, True, codes.UPLOAD_UNAVAILABLE),
            (429, True, codes.UPLOAD_UNAVAILABLE),
            (402, False, codes.UPLOAD_QUOTA),
        ):
            with self.subTest(status=status):
                body = error_body(
                    "brand_new_error", retryable=retryable, future={"x": [1]}
                )
                body["error"]["details"] = [{"code": "new_detail", "extra": 1}]
                error, _ = self.failure(
                    [meta_reply(), whoami_reply(), reply(status, body)],
                    client=self.client(cache_path=self.tmp / str(status)),
                )
                self.assertEqual(error.code, "brand_new_error")
                self.assertEqual(error.exit_code, exit_code)

    def test_a_new_status_value_is_not_a_crash(self):
        result, _ = self.push(
            [meta_reply(), whoami_reply(), reply(201, sync_body(status="quarantined"))]
        )
        self.assertEqual(result.status, "quarantined")

    def test_every_status_of_the_definition_has_an_outcome(self):
        statuses = resolve({"$ref": "#/components/schemas/SyncStatus"})["enum"]
        for status in statuses:
            with self.subTest(status=status):
                if status in PENDING_STATUSES:
                    accepted = reply(
                        202, sync_body(status=status), **{"Retry-After": "0"}
                    )
                    replies = [
                        meta_reply(),
                        whoami_reply(),
                        accepted,
                        reply(200, sync_body()),
                    ]
                    result, _ = self.push(
                        replies, client=self.client(cache_path=self.tmp / status)
                    )
                    self.assertEqual(result.status, "completed")
                elif status in SUCCESS_STATUSES:
                    result, _ = self.push(
                        [
                            meta_reply(),
                            whoami_reply(),
                            reply(201, sync_body(status=status)),
                        ],
                        client=self.client(cache_path=self.tmp / status),
                    )
                    self.assertEqual(result.status, status)
                else:
                    self.assertEqual(status, FAILED_STATUS)
                    failed = sync_body(status=status)
                    failed["error"] = {
                        "code": "internal_error",
                        "message": "It broke.",
                        "retryable": True,
                    }
                    error, _ = self.failure(
                        [meta_reply(), whoami_reply(), reply(201, failed)],
                        client=self.client(cache_path=self.tmp / status),
                    )
                    self.assertEqual(error.exit_code, codes.UPLOAD_UNAVAILABLE)


class DefinitionGuardTests(unittest.TestCase):
    def test_a_new_definition_version_needs_a_review_of_these_tests(self):
        version = (DEFINITION / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(
            version,
            REVIEWED_DEFINITION,
            "the vendored definition changed: read what it adds, extend the "
            "contract tests or the lists above, then update REVIEWED_DEFINITION",
        )
        self.assertEqual(SPEC["info"]["version"], version)

    def test_the_cli_speaks_a_schema_version_of_the_definitions_major(self):
        major, minor = SCHEMA_VERSION.split(".")
        version = (
            (DEFINITION / "VERSION").read_text(encoding="utf-8").strip().split(".")
        )
        self.assertEqual(major, version[0])
        self.assertLessEqual(int(minor), int(version[1]))
        self.assertIn(SPEC["servers"][0]["url"].split("/")[-1], ("v" + major,))

    def test_every_error_code_is_known_to_the_cli_or_listed_as_not_applicable(self):
        listed = set(ERRORS["errors"])
        enum = set(resolve({"$ref": "#/components/schemas/ErrorCode"})["enum"])
        self.assertEqual(listed, enum)
        self.assertLessEqual(set(NOT_APPLICABLE_ERRORS), listed)
        for code in listed - set(NOT_APPLICABLE_ERRORS):
            self.assertIn(code, ERROR_EXIT_CODES, f"teach the CLI {code}")
        for code in NOT_APPLICABLE_ERRORS:
            self.assertEqual(ERRORS["errors"][code].get("api"), "v1", code)

    def test_every_warning_code_has_a_route_in_these_tests(self):
        enum = set(resolve({"$ref": "#/components/schemas/WarningCode"})["enum"])
        self.assertEqual(set(ERRORS["warnings"]), enum)
        self.assertEqual(set(WARNING_ROUTES), enum)

    def test_every_operation_is_used_by_the_cli_or_listed_as_not_applicable(self):
        operations = {
            (method, template)
            for template, found in SPEC["paths"].items()
            for method in found
            if method != "parameters"
        }
        self.assertEqual(operations, USED_OPERATIONS | set(NOT_APPLICABLE_OPERATIONS))

    def test_every_example_and_what_it_expects_is_in_the_definition(self):
        examples = base.load_examples()
        seen = set()
        for number, example in examples.items():
            request = example["request"]
            path = re.sub(r"\{\{[^}]*\}\}", "x", request["path"]).split("?")[0]
            template, _ = find_operation(request["method"], path[len(SERVER_PATH) :])
            self.assertIsNotNone(template, f"example {number}")
            seen.add((request["method"].lower(), template))
            subset = example["expect"].get("body_subset") or {}
            if subset.get("object") == "error":
                self.assertIn(subset["error"]["code"], ERRORS["errors"], number)
            for warning in subset.get("warnings", []):
                self.assertIn(warning["code"], ERRORS["warnings"], number)
        self.assertLessEqual(seen, USED_OPERATIONS | set(NOT_APPLICABLE_OPERATIONS))
        self.assertGreaterEqual(len(examples), 51)

    def test_the_examples_the_cli_does_not_act_on_are_all_named(self):
        examples = base.load_examples()
        named = (
            set(base.NEVER_SENT)
            | set(base.REFUSED_LOCALLY)
            | base.KEY_CHECKED_LOCALLY
            | base.READS
            | base.NOT_USED
        )
        for number in named:
            self.assertIn(number, examples)
        for number in set(examples) - named:
            self.assertEqual(examples[number]["request"]["method"], "POST", number)

    def test_the_enums_the_cli_maps_are_the_definitions(self):
        def enum(name):
            return set(resolve({"$ref": f"#/components/schemas/{name}"})["enum"])

        self.assertEqual(set(PROVIDERS.values()), enum("Provider"))
        self.assertEqual(set(EXIT_STRATEGIES.values()), enum("ExitStrategy"))
        self.assertEqual(
            set(PENDING_STATUSES) | set(SUCCESS_STATUSES) | {FAILED_STATUS},
            enum("SyncStatus"),
        )

    def test_the_vectors_have_a_test_or_a_reason_not_to(self):
        listed = {path.name for path in (DEFINITION / "vectors").glob("*.json")}
        self.assertEqual(listed, set(VECTORS))
        for name, test in VECTORS.items():
            if test:
                self.assertIn(name, (ROOT / test).read_text(encoding="utf-8"))

    def test_the_cli_does_not_compute_the_content_hash(self):
        for path in (ROOT / "core").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("content_sha256", text, path.name)


if __name__ == "__main__":
    unittest.main()
