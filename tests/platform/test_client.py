import json
import logging
import os
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import requests
import yaml

from core import api_v2
from core.api_v2 import (
    ERROR_EXIT_CODES,
    EXIT_STRATEGIES,
    ApiClient,
    ApiError,
    check_key,
    exit_code_for,
    resolve_ca_bundle,
)
from tests.support.api import (
    ASSESSMENT_ID,
    EU_KEY,
    HOST,
    KEY,
    PLATFORM_SYNC_ID,
    REQUEST_ID,
    US_KEY,
    component_errors,
    corrupt,
    created_reply,
    error_body,
    make_key,
    merged,
    meta_body,
    meta_reply,
    minimal_document,
    reply,
    success_replies,
    sync_body,
    whoami_body,
    whoami_reply,
)
from tests.support.http import fake_http, make_response
from tests.support.sync import DEFINITION, schema_errors
from cloudexit import codes
from cloudexit.settings import Setting

SYNC_ID = "01933b9e-7c2a-7d41-9a51-3c8f0a6e0001"
ERRORS = yaml.safe_load((DEFINITION / "errors.yaml").read_text(encoding="utf-8"))


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.now = 1_800_000_000.0
        self.slept = []
        self.clock = [0.0]
        self.clients = 0
        self.cache = self.tmp / "cache"

    def client(self, key=KEY, host=HOST, **options):
        self.clients += 1
        options.setdefault("cache_path", self.tmp / f"cache-{self.clients}")
        options.setdefault("environ", {})
        # One attempt: the tests that exercise the retries ask for a budget
        options.setdefault("budget", 0.0)
        options.setdefault("clock", lambda: self.now)
        options.setdefault("sleep", self.sleep)
        options.setdefault("monotonic", lambda: self.clock[0])
        return ApiClient(host, key, **options)

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.clock[0] += max(seconds, 0.001)

    def document(self, **changes):
        document = minimal_document(SYNC_ID, int(self.now) - 60)
        return merged(document, changes)

    def push(self, replies, client=None, **kwargs):
        kwargs.setdefault("name", "Flow Test")
        kwargs.setdefault("exit_strategy", 1)
        with fake_http(*replies) as fake:
            result = (client or self.client()).push(self.document(), **kwargs)
        return result, fake

    def failure(self, replies, client=None, **kwargs):
        kwargs.setdefault("name", "Flow Test")
        kwargs.setdefault("exit_strategy", 1)
        with fake_http(*replies) as fake:
            with self.assertRaises(ApiError) as ctx:
                (client or self.client()).push(self.document(), **kwargs)
        return ctx.exception, fake

    def body_of(self, request):
        return json.loads(request.kwargs["data"].decode("utf-8"))


class KeyTests(ClientTestCase):
    def test_every_issued_prefix_is_accepted_and_names_its_platform(self):
        for key, product, region in (
            (KEY, "escapecloud", None),
            (EU_KEY, "exitcloud", "eu"),
            (US_KEY, "exitcloud", "us"),
        ):
            with self.subTest(product=product, region=region):
                info = check_key(key)
                self.assertEqual((info.product, info.region), (product, region))
                self.assertEqual(info.prefix, key[: key.index("sk_") + 3])

    def test_a_key_that_cannot_be_right_is_refused_with_exit_11(self):
        bad = {
            "none": None,
            "empty": "",
            "not a key": "not-a-key",
            "wrong prefix": "abc_sk_" + KEY[7:],
            "too short": KEY[:-1],
            "too long": KEY + "A",
            "bad checksum": corrupt(KEY),
            "typo in the body": KEY[:10] + ("B" if KEY[10] != "B" else "C") + KEY[11:],
            "symbol": KEY[:20] + "-" + KEY[21:],
            "non ascii": KEY[:20] + "é" + KEY[21:],
            "trailing newline": KEY + "\n",
            "prefix only": "esc_sk_",
        }
        for label, key in bad.items():
            with self.subTest(label):
                with self.assertRaises(ApiError) as ctx:
                    check_key(key)
                self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_AUTH)
                self.assertEqual(ctx.exception.code, "invalid_api_key")
                self.assertNotIn(KEY[8:], ctx.exception.message)

    def test_the_checksum_is_the_crc32_of_everything_before_it_in_base62(self):
        self.assertEqual(api_v2.checksum("esc_sk_" + "A" * 48), KEY[-6:])
        self.assertEqual(len(api_v2.checksum("x")), 6)
        self.assertEqual(api_v2.checksum(""), "000000")

    def test_a_typo_never_reaches_the_network(self):
        with fake_http() as fake:
            for key in (corrupt(KEY), corrupt(EU_KEY), "not-a-key"):
                with self.assertRaises(ApiError):
                    ApiClient("https://platform.example.com", key, environ={})
        self.assertEqual(fake.requests, [])

    def sent(self, key, url, **meta_changes):
        client = self.client(key=key, host=url)
        with fake_http(meta_reply(**meta_changes), whoami_reply()) as fake:
            client.whoami()
        return fake

    def refused(self, key, url, **meta_changes):
        client = self.client(key=key, host=url)
        with fake_http(meta_reply(**meta_changes)) as fake:
            with self.assertRaises(ApiError) as ctx:
                client.whoami()
        # Only /meta was asked, and the key was not sent anywhere
        self.assertEqual([r.method for r in fake.requests], ["GET"])
        for request in fake.requests:
            self.assertNotIn("Authorization", request.kwargs["headers"])
        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_AUTH)
        self.assertEqual(ctx.exception.code, "wrong_region")
        self.assertNotIn(key, ctx.exception.message)
        return ctx.exception

    def test_an_escapecloud_key_works_on_any_host_that_says_it_is_escapecloud(self):
        hosts = (
            "https://escape.corp.internal",
            "https://10.0.4.17:8443",
            "http://localhost:8000",
            "https://platform.example.com",
            "platform.example.com",
        )
        for host in hosts:
            for region in (None, "eu", "us"):
                with self.subTest(host=host, region=region):
                    fake = self.sent(
                        KEY, host, host={"product": "escapecloud", "region": region}
                    )
                    self.assertEqual(
                        fake.requests[1].kwargs["headers"]["Authorization"],
                        f"Bearer {KEY}",
                    )

    def test_a_regional_key_works_on_a_host_of_its_region_whatever_its_name(self):
        for key, region in ((EU_KEY, "eu"), (US_KEY, "us")):
            for host in (
                f"https://{region}.platform.example.com",
                "https://staging.example.net",
                "https://10.0.4.17:8443",
                "http://localhost:8000",
            ):
                for reported in (region, region.upper()):
                    with self.subTest(region=region, host=host, reported=reported):
                        self.sent(
                            key, host, host={"product": "exitcloud", "region": reported}
                        )

    def test_a_key_of_another_region_is_refused_naming_what_the_host_reports(self):
        error = self.refused(
            EU_KEY,
            "https://platform.example.com",
            host={"product": "exitcloud", "region": "us"},
        )
        self.assertEqual(
            error.message,
            "This is an ExitCloud EU key, but the platform reports ExitCloud US.",
        )
        error = self.refused(
            US_KEY,
            "https://10.0.4.17:8443",
            host={"product": "exitcloud", "region": "eu"},
        )
        self.assertEqual(
            error.message,
            "This is an ExitCloud US key, but the platform reports ExitCloud EU.",
        )

    def test_a_key_of_the_other_product_is_refused(self):
        error = self.refused(
            KEY,
            "https://platform.example.com",
            host={"product": "exitcloud", "region": "eu"},
        )
        self.assertEqual(
            error.message,
            "This is an EscapeCloud key, but the platform reports ExitCloud EU.",
        )
        error = self.refused(
            EU_KEY,
            "https://escape.corp.internal",
            host={"product": "escapecloud", "region": None},
        )
        self.assertEqual(
            error.message,
            "This is an ExitCloud EU key, but the platform reports EscapeCloud.",
        )

    def test_a_platform_that_does_not_say_what_it_is_decides_for_itself(self):
        no_host = meta_body()
        del no_host["host"]
        cases = {
            "no host block": reply(200, no_host),
            "no product": reply(200, meta_body(host={"product": None})),
            "an unknown product": reply(200, meta_body(host={"product": "other"})),
            "host is not an object": reply(200, merged(meta_body(), {"host": "x"})),
        }
        for label, answer in cases.items():
            for key in (KEY, EU_KEY, US_KEY):
                with self.subTest(label, key=key[:6]):
                    client = self.client(key=key, host="https://platform.example.com")
                    with fake_http(answer, whoami_reply()):
                        client.whoami()

    def test_a_regional_key_on_a_host_without_a_region_is_left_to_the_platform(self):
        self.sent(
            EU_KEY,
            "https://platform.example.com",
            host={"product": "exitcloud", "region": None},
        )

    def test_the_key_is_not_sent_by_a_push_to_the_wrong_platform_either(self):
        client = self.client(key=US_KEY, host="https://platform.example.com")
        error, fake = self.failure(
            [meta_reply(host={"product": "exitcloud", "region": "eu"})], client=client
        )

        self.assertEqual((error.exit_code, error.code), (11, "wrong_region"))
        self.assertEqual(len(fake.requests), 1)
        self.assertNotIn("Authorization", fake.requests[0].kwargs["headers"])

    def test_the_platform_is_checked_once_per_client(self):
        client = self.client(key=EU_KEY, host="https://platform.example.com")
        eu = {"host": {"product": "exitcloud", "region": "eu"}}
        with fake_http(meta_reply(**eu), whoami_reply(), whoami_reply()) as fake:
            client.whoami()
            client.whoami()
        self.assertEqual(
            [r.url.rsplit("/", 1)[1] for r in fake.requests],
            ["meta", "whoami", "whoami"],
        )

    def test_a_missing_host_or_key_is_not_configured(self):
        class Settings:
            host = Setting(None)
            api_key = Setting(KEY)

        with self.assertRaises(ApiError) as ctx:
            ApiClient.from_settings(Settings())
        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_AUTH)


class RequestShapeTests(ClientTestCase):
    def test_every_call_carries_the_agreed_headers(self):
        client = self.client()
        with fake_http(meta_reply(), whoami_reply(), whoami_reply()) as fake:
            client.meta()
            client.whoami()
            client.whoami()

        meta_call, whoami_call, _ = fake.requests
        self.assertNotIn("Authorization", meta_call.kwargs["headers"])
        self.assertEqual(
            whoami_call.kwargs["headers"]["Authorization"], f"Bearer {KEY}"
        )
        ids = [r.kwargs["headers"]["X-Request-ID"] for r in fake.requests]
        self.assertEqual(len(set(ids)), len(ids))
        for request_id in ids:
            self.assertRegex(request_id, r"^[A-Za-z0-9._-]{8,64}$")
        for request in fake.requests:
            self.assertEqual(request.kwargs["headers"]["Accept"], "application/json")
            self.assertIs(request.kwargs["allow_redirects"], False)
            self.assertEqual(request.kwargs["timeout"], (10.0, 60.0))
            self.assertIs(request.kwargs["verify"], True)

    def test_the_user_agent_names_the_client_python_system_and_ci(self):
        pattern = r"^cloudexit/\S+ \(python/3\.\d+; [a-z0-9]+; ci=%s\)$"
        for environ, ci in (
            ({}, "none"),
            ({"GITHUB_ACTIONS": "true"}, "github"),
            ({"GITLAB_CI": "true"}, "gitlab"),
            ({"TF_BUILD": "True"}, "azure"),
            ({"GITHUB_ACTIONS": "false"}, "none"),
        ):
            with self.subTest(ci=ci):
                agent = self.client(environ=environ).session.headers["User-Agent"]
                self.assertRegex(agent, pattern % ci)

    def test_a_push_sends_the_idempotency_key_and_the_document_as_json(self):
        _, fake = self.push(success_replies())

        post = fake.requests[2]
        headers = post.kwargs["headers"]
        self.assertEqual((post.method, post.url), ("POST", f"{HOST}/api/v2/syncs"))
        self.assertEqual(headers["Idempotency-Key"], SYNC_ID)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Prefer"], "wait=20")
        self.assertEqual(self.body_of(post), self.document())
        self.assertNotIn(b" ", post.kwargs["data"].split(b'"sync"')[0])
        for request in fake.requests[:2]:
            self.assertNotIn("Idempotency-Key", request.kwargs["headers"])

    def test_the_body_is_utf8_without_escaping(self):
        document = self.document(inventory={"resources": [], "note": "é"})
        with fake_http(*success_replies()) as fake:
            self.client().push(document, name="n", exit_strategy=1)
        self.assertIn("é".encode(), fake.requests[2].kwargs["data"])

    def test_a_redirect_is_never_followed(self):
        error, _ = self.failure(
            [
                meta_reply(),
                make_response(302, headers={"Location": "https://elsewhere.test/"}),
            ]
        )
        self.assertEqual(error.exit_code, codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(error.code, "http_302")

    def test_the_netrc_cannot_replace_the_bearer_header(self):
        session = self.client().session
        self.assertTrue(session.auth)
        prepared = session.prepare_request(
            requests.Request("GET", HOST, headers={"Authorization": "Bearer x"})
        )
        self.assertEqual(prepared.headers["Authorization"], "Bearer x")


class TransportTests(ClientTestCase):
    def test_ca_bundle_precedence(self):
        environ = {"REQUESTS_CA_BUNDLE": "/r.pem", "SSL_CERT_FILE": "/s.pem"}
        self.assertEqual(resolve_ca_bundle("/flag.pem", environ), "/flag.pem")
        self.assertEqual(resolve_ca_bundle(None, environ), "/r.pem")
        self.assertEqual(resolve_ca_bundle("", environ), "/r.pem")
        self.assertEqual(resolve_ca_bundle(None, {"SSL_CERT_FILE": "/s.pem"}), "/s.pem")
        self.assertEqual(resolve_ca_bundle(None, {"REQUESTS_CA_BUNDLE": " "}), True)
        self.assertIs(resolve_ca_bundle(None, {}), True)

    def test_the_chosen_bundle_is_what_every_request_verifies_with(self):
        flag, requests_bundle, ssl_file = (
            self.tmp / "flag.pem",
            self.tmp / "requests.pem",
            self.tmp / "ssl.pem",
        )
        for path in (flag, requests_bundle, ssl_file):
            path.write_text("pem")
        environ = {
            "REQUESTS_CA_BUNDLE": str(requests_bundle),
            "SSL_CERT_FILE": str(ssl_file),
        }
        cases = (
            (str(flag), environ, str(flag)),
            (None, environ, str(requests_bundle)),
            (None, {"SSL_CERT_FILE": str(ssl_file)}, str(ssl_file)),
            (None, {}, True),
        )
        for configured, env, expected in cases:
            with self.subTest(expected=str(expected)[-10:]):
                client = self.client(ca_bundle=configured, environ=env)
                with fake_http(meta_reply(), whoami_reply()) as fake:
                    client.whoami()
                for request in fake.requests:
                    self.assertEqual(request.kwargs["verify"], expected)

    def test_verification_cannot_be_switched_off(self):
        for configured, env in (
            ("false", {}),
            (None, {"REQUESTS_CA_BUNDLE": "0"}),
            (None, {"CURL_CA_BUNDLE": ""}),
        ):
            with self.subTest(configured=configured, env=env):
                try:
                    client = self.client(ca_bundle=configured, environ=env)
                except ApiError:
                    continue
                self.assertIsNot(client.verify, False)
                self.assertTrue(client.verify is True or isinstance(client.verify, str))
        source = Path(api_v2.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"verify\s*=\s*False")

    def test_a_ca_bundle_that_does_not_exist_is_a_clear_failure(self):
        with self.assertRaises(ApiError) as ctx:
            self.client(ca_bundle=str(self.tmp / "missing.pem"))
        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(ctx.exception.code, "tls_error")

    def test_the_standard_proxy_variables_are_honoured(self):
        session = self.client().session
        self.assertTrue(session.trust_env)
        environ = {
            "HTTPS_PROXY": "http://proxy.test:3128",
            "NO_PROXY": "internal.test",
        }
        with patch.dict(os.environ, environ):
            proxied = session.merge_environment_settings(
                f"{HOST}/api/v2/meta", {}, None, True, None
            )["proxies"]
            direct = session.merge_environment_settings(
                "https://internal.test/api/v2/meta", {}, None, True, None
            )["proxies"]
        self.assertEqual(proxied["https"], "http://proxy.test:3128")
        self.assertNotIn("https", direct)

    def test_timeouts_default_to_10_and_60_and_the_setting_overrides_both(self):
        self.assertEqual(api_v2.request_timeout(Setting(10.0, "default")), (10.0, 60.0))
        self.assertEqual(api_v2.request_timeout(None), (10.0, 60.0))
        self.assertEqual(
            api_v2.request_timeout(Setting(5.0, "flag --timeout")), (5.0, 5.0)
        )

        client = self.client(timeout=(2.0, 3.0))
        with fake_http(meta_reply(), whoami_reply()) as fake:
            client.whoami()
        for request in fake.requests:
            self.assertEqual(request.kwargs["timeout"], (2.0, 3.0))

    def test_transport_failures_are_exit_12(self):
        cases = (
            (requests.ConnectionError("refused"), "connection_failed"),
            (requests.ConnectTimeout("slow"), "timeout"),
            (requests.ReadTimeout("slow"), "timeout"),
            (requests.exceptions.SSLError("bad cert"), "tls_error"),
            (requests.exceptions.ProxyError("proxy"), "connection_failed"),
            (
                OSError("Could not find a suitable TLS CA certificate bundle"),
                "connection_failed",
            ),
        )
        for exception, code in cases:
            with self.subTest(code=code, exception=type(exception).__name__):
                with fake_http(exception):
                    with self.assertRaises(ApiError) as ctx:
                        self.client().whoami()
                self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_UNAVAILABLE)
                self.assertEqual(ctx.exception.code, code)
                self.assertIn("platform.test", ctx.exception.message)
                self.assertNotIn(KEY, ctx.exception.message)

    def test_a_tls_failure_points_at_the_ca_bundle_options(self):
        with fake_http(requests.exceptions.SSLError("x")):
            with self.assertRaises(ApiError) as ctx:
                self.client().whoami()
        self.assertIn("--ca-bundle", ctx.exception.message)
        self.assertIn("CLOUDEXIT_CA_BUNDLE", ctx.exception.message)


class MetaTests(ClientTestCase):
    def test_meta_is_cached_for_five_minutes(self):
        with fake_http(meta_reply()) as fake:
            first = self.client(cache_path=self.cache).meta()
            self.now += 299
            second = self.client(cache_path=self.cache).meta()
        self.assertEqual(first, second)
        self.assertEqual(len(fake.requests), 1)

        self.now += 2
        with fake_http(meta_reply()) as fake:
            self.client(cache_path=self.cache).meta()
        self.assertEqual(len(fake.requests), 1)

    def test_one_client_asks_once(self):
        client = self.client(cache_path=self.cache)
        with fake_http(meta_reply()) as fake:
            client.meta()
            client.meta()
        self.assertEqual(len(fake.requests), 1)

    def test_the_cache_is_per_host_and_holds_no_key(self):
        with fake_http(meta_reply(), meta_reply()) as fake:
            self.client(cache_path=self.cache).meta()
            self.client(host="https://other.test", cache_path=self.cache).meta()
        self.assertEqual(len(fake.requests), 2)

        files = list(self.cache.glob("meta-*.json"))
        self.assertEqual(len(files), 2)
        for path in files:
            self.assertNotIn(KEY, path.read_text(encoding="utf-8"))
            self.assertNotIn(KEY[8:], path.read_text(encoding="utf-8"))

    def test_a_broken_cache_is_ignored_and_replaced(self):
        path = self.client(cache_path=self.cache)._meta_file()
        path.parent.mkdir(parents=True)
        for content in ("{", "[]", json.dumps({"base_url": HOST}), "null"):
            with self.subTest(content=content):
                path.write_text(content)
                with fake_http(meta_reply()) as fake:
                    self.client(cache_path=self.cache).meta()
                self.assertEqual(len(fake.requests), 1)
                self.assertEqual(json.loads(path.read_text())["base_url"], HOST)

    def test_a_cache_from_the_future_is_not_trusted(self):
        with fake_http(meta_reply(), meta_reply()) as fake:
            self.client(cache_path=self.cache).meta()
            self.now -= 1000
            self.client(cache_path=self.cache).meta()
        self.assertEqual(len(fake.requests), 2)

    def test_an_unwritable_cache_does_not_stop_the_run(self):
        blocker = self.tmp / "blocked"
        blocker.write_text("a file, not a directory")
        with fake_http(meta_reply()):
            meta = self.client(cache_path=blocker / "sub").meta()
        self.assertEqual(meta["object"], "meta")

    def test_something_that_is_not_meta_is_refused(self):
        with fake_http(reply(200, {"object": "whoami"})):
            with self.assertRaises(ApiError) as ctx:
                self.client(cache_path=self.cache).meta()
        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_UNAVAILABLE)

    @unittest.skipIf(os.name == "nt", "absolute /xdg paths are POSIX paths")
    def test_the_cache_directory_follows_the_platform_conventions(self):
        self.assertEqual(
            api_v2.cache_dir({"XDG_CACHE_HOME": "/xdg"}, platform="linux"),
            Path("/xdg/cloudexit"),
        )
        self.assertEqual(
            api_v2.cache_dir({"XDG_CACHE_HOME": "relative"}, platform="linux"),
            Path.home() / ".cache" / "cloudexit",
        )
        self.assertEqual(
            api_v2.cache_dir({"LOCALAPPDATA": "C:/L"}, platform="win32"),
            Path("C:/L") / "cloudexit" / "cache",
        )

    @unittest.skipIf(os.name == "nt", "Windows keeps the cache under LOCALAPPDATA")
    def test_the_default_cache_is_in_the_users_cache_directory(self):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "xdg")}):
            client = ApiClient(HOST, KEY)
        self.assertEqual(client._cache_dir, self.tmp / "xdg" / "cloudexit")


class PolicyTests(ClientTestCase):
    def setUp(self):
        super().setUp()
        # The policies below are written against this client version, not the
        # one in cloudexit/version.py, which changes with every release
        self.enterContext(patch.object(api_v2, "__version__", "1.1.7"))

    def test_a_blocked_or_too_old_client_is_refused_before_the_push(self):
        for label, clients in (
            ("blocked", {"blocked": ["1.1.7"]}),
            ("blocked with a tag", {"blocked": ["v1.1.7"]}),
            ("min supported", {"min_supported": "2.0.0"}),
        ):
            with self.subTest(label):
                body = meta_body()
                body["clients"]["cloudexit"].update(clients)
                error, fake = self.failure([reply(200, body)])
                self.assertEqual(error.exit_code, codes.UPLOAD_UNSUPPORTED)
                self.assertEqual(error.code, "client_version_unsupported")
                self.assertEqual(len(fake.requests), 1)
                self.assertIn("2.0.0", error.message)

    def test_a_deprecated_client_gets_one_warning_and_still_uploads(self):
        body = meta_body()
        body["clients"]["cloudexit"]["deprecated_below"] = "2.0.0"
        result, _ = self.push([reply(200, body), whoami_reply(), created_reply()])
        (warning,) = result.warnings
        self.assertIn("deprecated", warning)
        self.assertIn("2.0.0", warning)

    def test_a_current_client_and_a_host_without_a_policy_have_no_warning(self):
        body = meta_body()
        body["clients"] = {}
        result, _ = self.push([reply(200, body), whoami_reply(), created_reply()])
        self.assertEqual(result.warnings, ())
        result, _ = self.push(
            success_replies(), client=self.client(cache_path=self.tmp / "c2")
        )
        self.assertEqual(result.warnings, ())

    def test_version_parsing(self):
        self.assertEqual(api_v2._version("v2.0.0rc1"), (2, 0, 0))
        self.assertEqual(api_v2._version("2.1"), (2, 1, 0))
        self.assertEqual(api_v2._version("1.1.7-dev+abc"), (1, 1, 7))
        self.assertIsNone(api_v2._version(None))
        self.assertIsNone(api_v2._version("latest"))

    def test_a_schema_version_the_host_does_not_list_is_exit_15_without_a_push(self):
        with fake_http(meta_reply()) as fake:
            with self.assertRaises(ApiError) as ctx:
                self.client().push(
                    self.document(schema_version="2.7"), name="n", exit_strategy=1
                )
        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_UNSUPPORTED)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")
        self.assertEqual(len(fake.requests), 1)

    def test_a_newer_schema_version_the_host_lists_is_sent(self):
        body = meta_body(contract={"schema_versions": ["2.0", "2.1"]})
        result, _ = self.push([reply(200, body), whoami_reply(), created_reply()])
        self.assertEqual(result.status, "completed")


class LimitTests(ClientTestCase):
    def refused(self, document, meta=None, **kwargs):
        replies = [reply(200, meta or meta_body()), whoami_reply(**kwargs)]
        with fake_http(*replies) as fake:
            with self.assertRaises(ApiError) as ctx:
                self.client().push(document, name="n", exit_strategy=1)
        self.assertEqual([r.method for r in fake.requests], ["GET", "GET"])
        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_REJECTED)
        self.assertEqual(ctx.exception.code, "limit_exceeded")
        return ctx.exception.message

    def test_a_document_over_a_limit_never_leaves_the_machine(self):
        resource = {"type_id": 1, "type_code": "A", "location": "x", "count": 1}
        cases = {
            "resource items": self.document(inventory={"resources": [resource] * 1001}),
            "count": self.document(
                inventory={"resources": [dict(resource, count=10001)]}
            ),
            "location": self.document(
                inventory={"resources": [dict(resource, location="x" * 256)]}
            ),
            "costs": self.document(inventory={"costs": [{}] * 13}),
            "egress items": self.document(inventory={"egress": {"items": [{}] * 1001}}),
            "flags": self.document(
                inventory={"egress": {"items": [{"flags": ["f"] * 11}]}}
            ),
            "notes": self.document(
                inventory={"egress": {"items": [{"notes": ["n"] * 11}]}}
            ),
            "tiers": self.document(
                inventory={"egress": {"items": [{"tiers": [{}] * 11}]}}
            ),
            "type ids": self.document(
                sync={
                    "collector": {"catalogue": {"resource_type_ids": list(range(5001))}}
                }
            ),
            "too old": self.document(sync={"completed_at": int(self.now) - 31 * 86400}),
            "ahead of the clock": self.document(
                sync={"completed_at": int(self.now) + 86401}
            ),
            "body": self.document(inventory={"note": "x" * 1_000_001}),
        }
        for label, document in cases.items():
            with self.subTest(label):
                self.assertTrue(self.refused(document))

    def test_the_announced_limits_win_over_the_defaults(self):
        document = self.document(
            inventory={"resources": [{"type_id": 1, "location": "x", "count": 1}] * 3}
        )
        meta = meta_body(limits={"max_resource_items": 2})
        self.assertIn("limit of 2", self.refused(document, meta))

        generous = meta_body(limits={"max_resource_items": 5000})
        document = self.document(
            inventory={
                "resources": [{"type_id": 1, "location": "x", "count": 1}] * 1001
            }
        )
        with fake_http(reply(200, generous), whoami_reply(), created_reply()):
            self.client().push(document, name="n", exit_strategy=1)

    def test_a_limit_the_host_leaves_out_falls_back_to_the_default(self):
        meta = meta_body()
        del meta["limits"]["max_cost_items"]
        document = self.document(inventory={"costs": [{}] * 13})
        self.assertIn("limit of 12", self.refused(document, meta))

    def test_an_assessment_name_over_the_limit_is_refused_for_an_account_key(self):
        replies = [meta_reply(), whoami_reply(kind="account")]
        with fake_http(*replies):
            with self.assertRaises(ApiError) as ctx:
                self.client().push(self.document(), name="n" * 256, exit_strategy=1)
        self.assertEqual(ctx.exception.code, "limit_exceeded")

    def test_the_messages_never_contain_the_document(self):
        document = self.document(
            inventory={
                "resources": [{"type_id": 1, "location": "secret-loc", "count": 10001}]
            }
        )
        message = self.refused(document)
        self.assertNotIn("secret-loc", message)


class TargetTests(ClientTestCase):
    def target_of(self, replies, **kwargs):
        _, fake = self.push(replies, **kwargs)
        return self.body_of(fake.requests[2]).get("target")

    def test_an_assessment_key_sends_no_target(self):
        self.assertIsNone(self.target_of(success_replies()))

    def test_an_account_key_creates_an_assessment_with_the_strategy_name(self):
        for number, name in EXIT_STRATEGIES.items():
            with self.subTest(number=number):
                replies = [
                    meta_reply(),
                    whoami_reply(kind="account"),
                    created_reply(),
                ]
                target = self.target_of(
                    replies,
                    exit_strategy=number,
                    client=self.client(cache_path=self.tmp / f"c{number}"),
                )
                self.assertEqual(
                    target, {"create": {"name": "Flow Test", "exit_strategy": name}}
                )

    def test_an_assessment_id_names_the_target_for_either_key(self):
        for kind in ("account", "assessment"):
            with self.subTest(kind=kind):
                replies = [meta_reply(), whoami_reply(kind=kind), created_reply()]
                target = self.target_of(
                    replies,
                    assessment_id=ASSESSMENT_ID.upper(),
                    client=self.client(cache_path=self.tmp / kind),
                )
                self.assertEqual(target, {"assessment_id": ASSESSMENT_ID})

    def test_an_assessment_id_that_is_not_an_id_is_exit_14_without_a_push(self):
        error, fake = self.failure(
            [meta_reply(), whoami_reply(kind="account")], assessment_id="not-an-id"
        )
        self.assertEqual((error.exit_code, error.code), (14, "invalid_assessment_id"))
        self.assertEqual(len(fake.requests), 2)

    def test_an_unknown_exit_strategy_is_not_sent(self):
        error, fake = self.failure(
            [meta_reply(), whoami_reply(kind="account")], exit_strategy=9
        )
        self.assertEqual(error.exit_code, codes.UPLOAD_REJECTED)
        self.assertEqual(len(fake.requests), 2)

    def test_the_document_is_not_changed_and_an_old_target_is_replaced(self):
        document = self.document(target={"assessment_id": "old"})
        before = json.dumps(document, sort_keys=True)
        with fake_http(*success_replies()) as fake:
            self.client().push(document, name="n", exit_strategy=1)
        self.assertEqual(json.dumps(document, sort_keys=True), before)
        self.assertNotIn("target", self.body_of(fake.requests[2]))


class ResponseTests(ClientTestCase):
    def test_201_carries_the_ids_the_link_and_the_status(self):
        result, _ = self.push(success_replies())
        self.assertEqual(
            (
                result.status,
                result.sync_id,
                result.assessment_id,
                result.assessment_name,
                result.assessment_created,
                result.url,
                result.replayed,
                result.pending,
                result.request_id,
            ),
            (
                "completed",
                PLATFORM_SYNC_ID,
                ASSESSMENT_ID,
                "Flow Test",
                False,
                "https://platform.test/assessments/a-1",
                False,
                False,
                REQUEST_ID,
            ),
        )

    def test_200_with_idempotent_replayed_is_a_replay(self):
        replayed = reply(200, sync_body(), **{"Idempotent-Replayed": "true"})
        result, _ = self.push([meta_reply(), whoami_reply(), replayed])
        self.assertTrue(result.replayed)
        self.assertFalse(result.pending)

    def test_completed_with_warnings_is_a_success_and_the_warnings_come_back(self):
        warnings = [
            {
                "code": "unknown_resource_types",
                "message": "Unknown types.",
                "details": {},
            },
            {"code": "tier_downgraded", "message": "Granted\n  Basic."},
        ]
        body = sync_body(status="completed_with_warnings", warnings=warnings)
        whoami = whoami_body(warnings=[{"code": "key_expiring", "message": "Soon."}])
        result, _ = self.push([meta_reply(), reply(200, whoami), reply(201, body)])
        self.assertEqual(result.status, "completed_with_warnings")
        self.assertEqual(
            result.warnings,
            (
                "Soon. (key_expiring)",
                "Unknown types. (unknown_resource_types)",
                "Granted Basic. (tier_downgraded)",
            ),
        )

    def test_202_is_polled_until_it_completes(self):
        accepted = reply(
            202,
            sync_body(status="accepted", results=None),
            **{"Retry-After": "3", "Location": "/api/v2/syncs/x"},
        )
        processing = reply(200, sync_body(status="processing"), **{"Retry-After": "7"})
        done = reply(200, sync_body())
        result, fake = self.push(
            [meta_reply(), whoami_reply(), accepted, processing, done]
        )

        self.assertEqual(result.status, "completed")
        self.assertFalse(result.pending)
        self.assertEqual(self.slept, [3.0, 7.0])
        polls = fake.requests[3:]
        self.assertEqual(
            [(r.method, r.url) for r in polls],
            [("GET", f"{HOST}/api/v2/syncs/{PLATFORM_SYNC_ID}")] * 2,
        )
        for poll in polls:
            self.assertNotIn("Idempotency-Key", poll.kwargs["headers"])
            self.assertEqual(poll.kwargs["headers"]["Authorization"], f"Bearer {KEY}")

    def test_polling_without_a_retry_after_waits_a_default_and_caps_a_long_one(self):
        accepted = reply(202, sync_body(status="accepted"))
        slow = reply(200, sync_body(status="processing"), **{"Retry-After": "900"})
        result, _ = self.push(
            [meta_reply(), whoami_reply(), accepted, slow, reply(200, sync_body())]
        )
        self.assertEqual(self.slept, [2.0, 30.0])
        self.assertEqual(result.status, "completed")

    def test_a_sync_still_processing_when_the_budget_runs_out_is_pending(self):
        def still():
            return reply(200, sync_body(status="processing"), **{"Retry-After": "2"})

        accepted = reply(202, sync_body(status="accepted"), **{"Retry-After": "2"})
        client = self.client(timeout=(10.0, 5.0))
        with fake_http(
            meta_reply(), whoami_reply(), accepted, still(), still(), still()
        ) as fake:
            result = client.push(self.document(), name="n", exit_strategy=1)

        self.assertTrue(result.pending)
        self.assertEqual(result.status, "processing")
        self.assertEqual(result.url, "https://platform.test/assessments/a-1")
        self.assertEqual(self.slept, [2.0, 2.0, 1.0])
        self.assertEqual(len(fake.requests), 6)

    def test_a_pending_sync_without_an_id_cannot_be_polled(self):
        body = sync_body(status="accepted")
        del body["id"]
        result, fake = self.push([meta_reply(), whoami_reply(), reply(202, body)])
        self.assertTrue(result.pending)
        self.assertEqual(len(fake.requests), 3)

    def test_a_poll_that_fails_is_mapped_like_any_error(self):
        accepted = reply(202, sync_body(status="accepted"))
        gone = reply(404, error_body("sync_not_found"))
        error, _ = self.failure([meta_reply(), whoami_reply(), accepted, gone])
        self.assertEqual((error.exit_code, error.code), (14, "sync_not_found"))

    def test_a_sync_that_failed_is_mapped_from_its_error(self):
        failed = sync_body(
            status="failed",
            error=error_body("internal_error", "Scoring failed.", True)["error"],
        )
        error, _ = self.failure([meta_reply(), whoami_reply(), reply(201, failed)])
        self.assertEqual((error.exit_code, error.code), (12, "internal_error"))
        self.assertEqual(error.message, "Scoring failed.")

        no_detail = sync_body(status="failed")
        error, _ = self.failure(
            [meta_reply(), whoami_reply(), reply(201, no_detail)],
            client=self.client(cache_path=self.tmp / "other"),
        )
        self.assertEqual(error.code, "sync_failed")

    def test_a_2xx_without_a_json_object_is_exit_12(self):
        for answer in (
            make_response(201, text="<html>proxy</html>"),
            reply(201, ["not", "an", "object"]),
        ):
            with self.subTest(answer=answer.text[:12]):
                error, _ = self.failure(
                    [meta_reply(), whoami_reply(), answer],
                    client=self.client(cache_path=self.tmp / uuid.uuid4().hex),
                )
                self.assertEqual(
                    (error.exit_code, error.code), (12, "invalid_response")
                )

    def test_an_unsafe_link_is_dropped_and_a_missing_one_is_none(self):
        for url in ("javascript:alert(1)", "//evil.test/x", None, 5):
            with self.subTest(url=url):
                result, _ = self.push(
                    success_replies(assessment={"url": url}),
                    client=self.client(cache_path=self.tmp / uuid.uuid4().hex),
                )
                self.assertIsNone(result.url)


class ErrorMappingTests(ClientTestCase):
    def test_every_error_in_the_definition_maps_to_its_exit_code(self):
        self.assertEqual(set(ERROR_EXIT_CODES), set(ERRORS["errors"]))
        for code, entry in ERRORS["errors"].items():
            with self.subTest(code):
                self.assertEqual(ERROR_EXIT_CODES[code], entry["cli_exit"])
                self.assertEqual(
                    exit_code_for(code, entry["http"], entry["retryable"]),
                    entry["cli_exit"],
                )

    def test_the_exit_codes_in_the_definition_are_the_ones_in_codes_py(self):
        known = {
            10: codes.UPLOAD_REJECTED,
            11: codes.UPLOAD_AUTH,
            12: codes.UPLOAD_UNAVAILABLE,
            13: codes.UPLOAD_QUOTA,
            14: codes.UPLOAD_TARGET,
            15: codes.UPLOAD_UNSUPPORTED,
        }
        self.assertEqual(sorted(ERRORS["exit_codes"]), sorted(known))
        self.assertEqual(sorted(set(ERROR_EXIT_CODES.values())), sorted(known))

    def test_a_server_error_answer_reaches_the_caller_with_code_message_and_id(self):
        for code, entry in ERRORS["errors"].items():
            with self.subTest(code):
                body = error_body(code, f"message for {code}", entry["retryable"])
                error, _ = self.failure(
                    [meta_reply(), whoami_reply(), reply(entry["http"], body)],
                    client=self.client(cache_path=self.tmp / code),
                )
                self.assertEqual(error.code, code)
                self.assertEqual(error.message, f"message for {code}")
                self.assertEqual(error.request_id, REQUEST_ID)
                self.assertEqual(error.exit_code, entry["cli_exit"])
                self.assertEqual(error.status, entry["http"])
                self.assertEqual(error.retryable, entry["retryable"])

    def test_unknown_codes_fall_back_to_the_status_class_and_retryable(self):
        cases = (
            (400, False, 10),
            (413, False, 10),
            (422, False, 10),
            (409, False, 10),
            (401, False, 11),
            (403, False, 11),
            (402, False, 13),
            (404, False, 14),
            (410, False, 14),
            (429, True, 12),
            (409, True, 12),
            (500, False, 12),
            (503, False, 12),
            (502, True, 12),
        )
        for status, retryable, exit_code in cases:
            with self.subTest(status=status, retryable=retryable):
                self.assertEqual(
                    exit_code_for("brand_new_code", status, retryable), exit_code
                )
                body = error_body("brand_new_code", "A new thing.", retryable)
                error, _ = self.failure(
                    [meta_reply(), whoami_reply(), reply(status, body)],
                    client=self.client(cache_path=self.tmp / f"{status}{retryable}"),
                )
                self.assertEqual(
                    (error.code, error.exit_code), ("brand_new_code", exit_code)
                )
        self.assertEqual(exit_code_for(None, None, False), codes.UPLOAD_REJECTED)
        self.assertEqual(exit_code_for(None, None, True), codes.UPLOAD_UNAVAILABLE)

    def test_an_answer_that_is_not_an_error_object_uses_the_status_class(self):
        for status, text, exit_code in (
            (502, "<html>Bad gateway</html>", 12),
            (503, "", 12),
            (401, "Unauthorized", 11),
            (400, "{not json", 10),
            (404, '{"error": "plain string"}', 14),
        ):
            with self.subTest(status=status):
                error, _ = self.failure(
                    [meta_reply(), whoami_reply(), make_response(status, text=text)],
                    client=self.client(cache_path=self.tmp / str(status) / text[:3]),
                )
                self.assertEqual(error.exit_code, exit_code)
                self.assertEqual(error.code, f"http_{status}")
                self.assertEqual(error.message, f"HTTP status {status}")
                self.assertNotIn("Bad gateway", error.message)

    def test_the_request_id_falls_back_to_the_one_the_client_sent(self):
        answer = make_response(502, text="bad")
        error, fake = self.failure([meta_reply(), whoami_reply(), answer])
        self.assertEqual(
            error.request_id, fake.requests[2].kwargs["headers"]["X-Request-ID"]
        )

    def test_a_wrong_region_answer_names_the_expected_host(self):
        body = error_body(
            "wrong_region",
            "This key belongs to the other region.",
            details=[
                {
                    "code": "wrong_region",
                    "message": "m",
                    "expected_host": "https://eu.platform.example.com",
                }
            ],
        )
        error, _ = self.failure([meta_reply(), whoami_reply(), reply(403, body)])
        self.assertEqual(error.exit_code, codes.UPLOAD_AUTH)
        self.assertIn("https://eu.platform.example.com", error.message)

    def test_messages_are_one_line_and_never_echo_the_key(self):
        body = error_body("validation_failed", f"Bad\nvalue {KEY} here")
        error, _ = self.failure([meta_reply(), whoami_reply(), reply(422, body)])
        self.assertNotIn("\n", error.message)
        self.assertNotIn(KEY, error.message)
        self.assertIn("[api key]", error.message)


class LoggingTests(ClientTestCase):
    def test_nothing_secret_reaches_the_logs(self):
        secret_location = "secret-location-marker"
        document = self.document(
            inventory={
                "resources": [
                    {
                        "type_id": 1,
                        "type_code": "T",
                        "location": secret_location,
                        "count": 1,
                    }
                ]
            }
        )
        runs = (
            success_replies(),
            [
                meta_reply(),
                whoami_reply(),
                reply(422, error_body("validation_failed", "no")),
            ],
            [meta_reply(), requests.ConnectionError(f"failed with {KEY}")],
        )
        with self.assertLogs("core.engine.api_v2", level=logging.DEBUG) as logs:
            for index, replies in enumerate(runs):
                try:
                    with fake_http(*replies):
                        self.client(cache_path=self.tmp / str(index)).push(
                            document, name="n", exit_strategy=1
                        )
                except ApiError:
                    pass

        text = "\n".join(logs.output)
        self.assertTrue(logs.records)
        for secret in (KEY, KEY[8:], "Bearer", "Authorization", secret_location):
            self.assertNotIn(secret, text)
        self.assertIn("request_id=", text)
        self.assertRegex(text, r"body_bytes=\d+")
        self.assertIn(REQUEST_ID, text)

    def test_the_exception_text_does_not_carry_the_key_or_the_body(self):
        with fake_http(requests.ConnectionError(f"boom {KEY}")):
            with self.assertRaises(ApiError) as ctx:
                self.client().whoami()
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertNotIn(KEY, repr(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)


EXAMPLES = DEFINITION / "examples"
TOKEN = re.compile(r"\{\{([^}]*)\}\}")

# Requests the CLI never sends: the platform's refusal of them is not the
# client's to handle.
NEVER_SENT = {
    "09": "no Idempotency-Key",
    "10": "an Idempotency-Key that differs from sync.id",
    "27": "an account key without a target",
    "37": "a document that breaks the schema",
    "39": "invalid JSON",
    "40": "another media type",
}
# Requests the CLI refuses before sending, from what /meta announces.
REFUSED_LOCALLY = {
    "38": (codes.UPLOAD_REJECTED, 2),
    "41": (codes.UPLOAD_UNSUPPORTED, 1),
}
# Key checks that happen without a request.
KEY_CHECKED_LOCALLY = {"33", "34"}
# Reads the CLI makes: capabilities, identity, and the poll of a sync.
READS = {"01", "02", "03", "04", "31", "32", "42", "43"}
# Not part of what a push needs.
NOT_USED = {"35", "44", "45", "46", "47", "48", "49", "50"}


class Replay:
    def __init__(self, now):
        self.now = now
        self.ids = {
            "main": ASSESSMENT_ID,
            "first_sync": PLATFORM_SYNC_ID,
        }
        self.keys = {}

    def identifier(self, name):
        if name not in self.ids:
            self.ids[name] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"example/{name}"))
        return self.ids[name]

    def fill(self, value, keys=None):
        if isinstance(value, dict):
            return {k: self.fill(v, keys) for k, v in value.items()}
        if isinstance(value, list):
            return [self.fill(v, keys) for v in value]
        if not isinstance(value, str) or "{{" not in value:
            return value
        whole = TOKEN.fullmatch(value)
        if whole:
            return self.token(whole.group(1), keys)
        return TOKEN.sub(lambda m: str(self.token(m.group(1), keys)), value)

    def token(self, name, keys):
        if name.endswith("_id"):
            return self.identifier(name[: -len("_id")])
        offset = re.fullmatch(r"now([+-]\d+)?", name)
        if offset:
            return int(self.now) + int(offset.group(1) or 0)
        key_name = name.split("|")[0]
        if key_name in (keys or {}):
            key = keys[key_name]
            return corrupt(key) if name.endswith("|corrupt") else key
        raise AssertionError(f"unknown placeholder {{{{{name}}}}}")


def load_examples():
    return {
        path.name[:2]: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(EXAMPLES.glob("*.json"))
    }


class ExampleSupport(ClientTestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.examples = load_examples()

    def setUp(self):
        super().setUp()
        self.now = 1_800_000_000.0
        self.replay = Replay(self.now)

    def keys_for(self, example):
        keys = {}
        for index, spec in enumerate(example["fixtures"].get("keys", [])):
            prefix = "esc_sk_"
            keys[spec["name"]] = make_key(prefix, "ABCDEFGH"[index])
        return keys

    def whoami_for(self, example, key_name):
        spec = next(k for k in example["fixtures"]["keys"] if k["name"] == key_name)
        scopes = spec.get("scopes") or ["syncs:write"]
        return reply(
            200,
            whoami_body(
                kind=spec["kind"],
                scopes=scopes,
                assessment_id=self.replay.identifier(spec.get("assessment", "main")),
            ),
        )

    def response_for(self, example, keys):
        expect = self.replay.fill(example["expect"], keys)
        headers = dict(expect.get("headers") or {})
        headers.setdefault("X-Request-ID", REQUEST_ID)
        for name in expect.get("headers_present", []):
            headers.setdefault(name, "present")
        subset = expect.get("body_subset") or {}
        if subset.get("object") == "error":
            body = merged(error_body(subset["error"]["code"]), subset)
            body["error"].setdefault("request_id", REQUEST_ID)
        else:
            body = merged(sync_body(), subset)
        return make_response(expect["status"], json_body=body, headers=headers)

    def run_example(self, number, attempts=1, **options):
        example = self.examples[number]
        keys = self.keys_for(example)
        request = self.replay.fill(example["request"], keys)
        key_name = TOKEN.search(example["request"]["headers"]["Authorization"]).group(1)
        key = keys[key_name]
        body = request["body"]
        target = body.get("target") or {}
        create = target.get("create") or {}
        strategy = {name: n for n, name in EXIT_STRATEGIES.items()}[
            create.get("exit_strategy", "repatriation")
        ]
        document = {k: v for k, v in body.items() if k != "target"}
        options.setdefault("cache_path", self.tmp / number)
        client = self.last_client = self.client(key=key, **options)
        replies = [meta_reply(), self.whoami_for(example, key_name)] + [
            self.response_for(example, keys) for _ in range(attempts)
        ]
        with fake_http(*replies) as fake:
            try:
                result = client.push(
                    document,
                    name=create.get("name", "Example"),
                    exit_strategy=strategy,
                    assessment_id=target.get("assessment_id"),
                )
                error = None
            except ApiError as exc:
                result, error = None, exc
        return example, request, fake, result, error


class ExampleReplayTests(ExampleSupport):
    def test_every_example_is_accounted_for(self):
        sent = {
            number
            for number, example in self.examples.items()
            if example["request"]["method"] == "POST"
        }
        accounted = (
            set(NEVER_SENT)
            | set(REFUSED_LOCALLY)
            | KEY_CHECKED_LOCALLY
            | READS
            | NOT_USED
            | {n for n in sent if n not in NEVER_SENT and n not in REFUSED_LOCALLY}
        )
        self.assertEqual(set(self.examples), accounted)

    def test_what_the_cli_never_sends_really_differs_from_what_it_sends(self):
        for number in NEVER_SENT:
            with self.subTest(number):
                example = self.examples[number]
                replay = Replay(self.now)
                request = replay.fill(
                    example["request"], {"assessment_key": KEY, "account_key": KEY}
                )
                headers, body = request["headers"], request.get("body")
                differs = (
                    "Idempotency-Key" not in headers
                    or not isinstance(body, dict)
                    or headers["Idempotency-Key"] != (body.get("sync") or {}).get("id")
                    or headers.get("Content-Type") != "application/json"
                    or bool(schema_errors(body))
                    or (
                        request["headers"]["Authorization"] == f"Bearer {KEY}"
                        and "target" not in body
                        and number == "27"
                    )
                )
                self.assertTrue(differs, NEVER_SENT[number])

    def test_the_request_the_cli_sends_is_the_one_in_the_example(self):
        for number, example in self.examples.items():
            if (
                example["request"]["method"] != "POST"
                or number in NEVER_SENT
                or number in REFUSED_LOCALLY
            ):
                continue
            with self.subTest(number=number, name=example["name"]):
                _, request, fake, _, _ = self.run_example(number)
                post = fake.requests[2]
                self.assertEqual(
                    (post.method, post.url), ("POST", HOST + request["path"])
                )
                self.assertEqual(self.body_of(post), request["body"])
                self.assertEqual(schema_errors(self.body_of(post)), [])
                headers = post.kwargs["headers"]
                for name in ("Idempotency-Key", "Content-Type"):
                    self.assertEqual(headers[name], request["headers"][name])
                self.assertEqual(
                    headers["Idempotency-Key"], request["body"]["sync"]["id"]
                )
                self.assertTrue(headers["Authorization"].startswith("Bearer esc_sk_"))
                self.assertEqual(headers["Prefer"], "wait=20")

    def test_success_examples_end_in_a_result(self):
        for number, example in self.examples.items():
            if example["request"]["method"] != "POST" or example["expect"][
                "status"
            ] not in (200, 201, 202):
                continue
            with self.subTest(number=number, name=example["name"]):
                example, _, _, result, error = self.run_example(number)
                self.assertIsNone(error)
                subset = example["expect"].get("body_subset") or {}
                self.assertEqual(result.status, subset.get("status", "completed"))
                headers = example["expect"].get("headers") or {}
                self.assertEqual(
                    result.replayed,
                    example["expect"]["status"] == 200
                    and headers.get("Idempotent-Replayed") == "true",
                )
                created = (subset.get("assessment") or {}).get("created")
                if created is not None:
                    self.assertEqual(result.assessment_created, created)
                self.assertEqual(result.assessment_id, ASSESSMENT_ID)

    def test_error_examples_end_in_the_exit_code_of_the_registry(self):
        seen = set()
        for number, example in self.examples.items():
            subset = example["expect"].get("body_subset") or {}
            if (
                example["request"]["method"] != "POST"
                or subset.get("object") != "error"
                or number in NEVER_SENT
                or number in REFUSED_LOCALLY
            ):
                continue
            with self.subTest(number=number, name=example["name"]):
                _, _, _, result, error = self.run_example(number)
                code = subset["error"]["code"]
                seen.add(code)
                self.assertIsNone(result)
                self.assertEqual(error.code, code)
                self.assertEqual(error.exit_code, ERRORS["errors"][code]["cli_exit"])
                self.assertEqual(error.status, example["expect"]["status"])
                self.assertEqual(error.request_id, REQUEST_ID)
                self.assertTrue(error.message)
        self.assertLessEqual(
            {
                "idempotency_key_reused",
                "target_mismatch",
                "catalogue_mismatch",
                "catalogue_sha256_mismatch",
                "insufficient_scope",
                "assessment_not_found",
                "key_binding_violation",
                "payload_too_large",
                "insufficient_credits",
            },
            seen,
        )

    def test_examples_the_cli_refuses_locally_send_no_push(self):
        for number, (exit_code, reads) in REFUSED_LOCALLY.items():
            with self.subTest(number=number):
                _, _, fake, result, error = self.run_example_refused(number, reads)
                self.assertIsNone(result)
                self.assertEqual(error.exit_code, exit_code)
                self.assertEqual([r.method for r in fake.requests], ["GET"] * reads)

    def run_example_refused(self, number, reads):
        example = self.examples[number]
        keys = self.keys_for(example)
        request = self.replay.fill(example["request"], keys)
        key_name = TOKEN.search(example["request"]["headers"]["Authorization"]).group(1)
        client = self.client(key=keys[key_name], cache_path=self.tmp / number)
        replies = [meta_reply(), self.whoami_for(example, key_name)][:reads]
        with fake_http(*replies) as fake:
            try:
                result = client.push(request["body"], name="n", exit_strategy=1)
                error = None
            except ApiError as exc:
                result, error = None, exc
        return example, request, fake, result, error

    def test_key_examples_are_refused_locally_without_a_request(self):
        for number in KEY_CHECKED_LOCALLY:
            with self.subTest(number=number):
                example = self.examples[number]
                keys = self.keys_for(example)
                request = self.replay.fill(example["request"], keys)
                key = request["headers"]["Authorization"].removeprefix("Bearer ")
                with fake_http() as fake:
                    with self.assertRaises(ApiError) as ctx:
                        check_key(key)
                self.assertEqual(fake.requests, [])
                self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_AUTH)
                self.assertEqual(
                    ctx.exception.code,
                    example["expect"]["body_subset"]["error"]["code"],
                )

    def test_the_read_examples_match_what_the_client_does_with_them(self):
        for number in ("31", "32"):
            with self.subTest(number=number):
                example = self.examples[number]
                keys = self.keys_for(example)
                client = self.client(key=next(iter(keys.values())))
                answer = self.response_for(example, keys)
                with fake_http(meta_reply(), answer):
                    with self.assertRaises(ApiError) as ctx:
                        client.whoami()
                self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_AUTH)
                self.assertEqual(
                    ctx.exception.code,
                    example["expect"]["body_subset"]["error"]["code"],
                )

        example = self.examples["02"]
        keys = self.keys_for(example)
        with fake_http(
            meta_reply(), self.whoami_for(example, "assessment_key")
        ) as fake:
            who = self.client(key=keys["assessment_key"]).whoami()
        self.assertEqual(who["key"]["kind"], "assessment")
        self.assertEqual(fake.requests[1].url, HOST + example["request"]["path"])

        with fake_http(reply(200, meta_body())) as fake:
            self.client().meta()
        self.assertEqual(
            fake.requests[0].url, HOST + self.examples["01"]["request"]["path"]
        )
        self.assertEqual(component_errors("Meta", meta_body()), [])

    def test_the_poll_examples_decide_the_end_of_a_202(self):
        accepted = reply(202, sync_body(status="accepted"), **{"Retry-After": "1"})
        for number, outcome in (("42", "ok"), ("43", "error")):
            with self.subTest(number=number):
                example = self.examples[number]
                answer = self.response_for(example, self.keys_for(example))
                with fake_http(meta_reply(), whoami_reply(), accepted, answer) as fake:
                    client = self.client(cache_path=self.tmp / number)
                    if outcome == "ok":
                        result = client.push(self.document(), name="n", exit_strategy=1)
                        self.assertEqual(result.status, "completed")
                    else:
                        with self.assertRaises(ApiError) as ctx:
                            client.push(self.document(), name="n", exit_strategy=1)
                        self.assertEqual(ctx.exception.exit_code, codes.UPLOAD_TARGET)
                self.assertEqual(fake.requests[3].method, "GET")


class FixtureTests(unittest.TestCase):
    def test_the_fake_answers_are_valid_against_the_definition(self):
        self.assertEqual(component_errors("Meta", meta_body()), [])
        self.assertEqual(component_errors("Whoami", whoami_body()), [])
        self.assertEqual(component_errors("Whoami", whoami_body(kind="account")), [])
        self.assertEqual(component_errors("Sync", sync_body()), [])
        self.assertEqual(
            component_errors("ErrorResponse", error_body("validation_failed")), []
        )

    def test_a_minimal_document_is_not_the_real_thing(self):
        self.assertNotEqual(schema_errors(minimal_document(SYNC_ID)), [])


if __name__ == "__main__":
    unittest.main()
