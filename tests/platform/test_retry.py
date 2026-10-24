import random
import unittest
from email.utils import formatdate

import requests

from core.api_v2 import (
    BACKOFF_CAP,
    MAX_ATTEMPTS,
    ApiClient,
    ApiError,
    parse_retry_after,
)
from tests.support.api import (
    HOST,
    KEY,
    created_reply,
    error_body,
    meta_reply,
    reply,
    whoami_reply,
)
from tests.support.http import fake_http, make_response
from tests.platform.test_client import ClientTestCase
from utils import codes


class Ceiling:
    # A random source that always answers the top of the range
    def uniform(self, low, high):
        return high


def unavailable(status=503, code="service_unavailable", **headers):
    return reply(status, error_body(code, "Try later.", True), **headers)


class RetryTestCase(ClientTestCase):
    def retrying(self, **options):
        options.setdefault("budget", 300.0)
        options.setdefault("rng", random.Random(7))
        return self.client(**options)

    def posts(self, fake):
        return [r for r in fake.requests if r.method == "POST"]


class BackoffTests(RetryTestCase):
    def test_a_transient_failure_is_retried_until_it_goes_through(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(),
            unavailable(),
            created_reply(),
        ]
        result, fake = self.push(replies, client=self.retrying())

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.posts(fake)), 3)
        self.assertEqual(len(self.slept), 2)

    def test_the_delays_are_jittered_within_one_two_four_seconds(self):
        client = self.retrying(rng=random.Random(11))
        replies = [meta_reply(), whoami_reply(), *[unavailable()] * 3, created_reply()]
        self.push(replies, client=client)

        expected = random.Random(11)
        ceilings = [1, 2, 4]
        self.assertEqual(
            self.slept, [expected.uniform(0, ceiling) for ceiling in ceilings]
        )
        for delay, ceiling in zip(self.slept, ceilings):
            self.assertGreaterEqual(delay, 0)
            self.assertLessEqual(delay, ceiling)

    def test_the_ceiling_doubles_from_one_second_and_stops_at_sixty(self):
        client = self.retrying(rng=Ceiling())
        error = ApiError("x", "x", codes.UPLOAD_UNAVAILABLE, retryable=True)

        ceilings = [client._delay(error, attempt) for attempt in range(1, 10)]

        self.assertEqual(ceilings, [1, 2, 4, 8, 16, 32, 60, 60, 60])
        self.assertEqual(BACKOFF_CAP, 60)

    def test_full_jitter_can_be_anywhere_from_zero_to_the_ceiling(self):
        client = self.retrying(rng=random.Random(3))
        error = ApiError("x", "x", codes.UPLOAD_UNAVAILABLE, retryable=True)

        delays = [client._delay(error, 5) for _ in range(200)]

        self.assertTrue(all(0 <= delay <= 16 for delay in delays))
        self.assertLess(min(delays), 2)
        self.assertGreater(max(delays), 14)

    def test_at_most_six_attempts_are_made(self):
        replies = [meta_reply(), whoami_reply(), *[unavailable()] * MAX_ATTEMPTS]
        error, fake = self.failure(replies, client=self.retrying())

        self.assertEqual(len(self.posts(fake)), 6)
        self.assertEqual((error.attempts, error.exhausted), (6, True))
        self.assertEqual(error.exit_code, codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(len(self.slept), 5)

    def test_the_attempt_limit_can_be_lowered(self):
        replies = [meta_reply(), whoami_reply(), unavailable(), unavailable()]
        error, fake = self.failure(replies, client=self.retrying(max_attempts=2))

        self.assertEqual(len(self.posts(fake)), 2)
        self.assertEqual(error.attempts, 2)


class RetryableFailureTests(RetryTestCase):
    def test_these_statuses_are_retried_even_without_an_error_body(self):
        for status in (408, 425, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.slept.clear()
                result, fake = self.push(
                    [
                        meta_reply(),
                        whoami_reply(),
                        make_response(status),
                        created_reply(),
                    ],
                    client=self.retrying(),
                )
                self.assertEqual(result.status, "completed")
                self.assertEqual(len(self.posts(fake)), 2)

    def test_connection_errors_and_timeouts_are_retried(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            requests.ConnectionError("refused"),
            requests.ConnectTimeout("slow"),
            requests.ReadTimeout("slow"),
            created_reply(),
        ]
        result, fake = self.push(replies, client=self.retrying())

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.posts(fake)), 4)

    def test_an_error_the_platform_marks_retryable_is_retried(self):
        for code, status in (
            ("request_in_progress", 409),
            ("rate_limited", 429),
            ("maintenance", 503),
            ("internal_error", 500),
        ):
            with self.subTest(code=code):
                result, _ = self.push(
                    [
                        meta_reply(),
                        whoami_reply(),
                        reply(status, error_body(code, "Wait.", True)),
                        created_reply(),
                    ],
                    client=self.retrying(),
                )
                self.assertEqual(result.status, "completed")

    def test_a_retry_that_reaches_a_final_error_stops(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(),
            reply(422, error_body("validation_failed", "No.", False)),
        ]
        error, fake = self.failure(replies, client=self.retrying())

        self.assertEqual((error.code, error.exit_code), ("validation_failed", 10))
        self.assertEqual(len(self.posts(fake)), 2)
        self.assertFalse(error.exhausted)


class FinalFailureTests(RetryTestCase):
    def test_these_failures_are_final_and_cost_one_request(self):
        cases = (
            (400, "invalid_json", 10),
            (401, "invalid_api_key", 11),
            (402, "insufficient_credits", 13),
            (409, "target_mismatch", 14),
            (409, "assessment_archived", 14),
            (422, "validation_failed", 10),
            (422, "idempotency_key_reused", 10),
            (403, "insufficient_scope", 11),
            (404, "assessment_not_found", 14),
            (429, "quota_exceeded", 13),
        )
        for status, code, exit_code in cases:
            with self.subTest(code=code):
                self.slept.clear()
                error, fake = self.failure(
                    [
                        meta_reply(),
                        whoami_reply(),
                        reply(status, error_body(code, "No.", False)),
                    ],
                    client=self.retrying(),
                )
                self.assertEqual((error.code, error.exit_code), (code, exit_code))
                self.assertEqual(len(self.posts(fake)), 1)
                self.assertEqual(self.slept, [])
                self.assertFalse(error.retryable)

    def test_a_status_without_an_error_body_that_is_not_retryable_is_final(self):
        for status in (400, 401, 402, 404, 409, 422):
            with self.subTest(status=status):
                error, fake = self.failure(
                    [meta_reply(), whoami_reply(), make_response(status)],
                    client=self.retrying(),
                )
                self.assertEqual(len(self.posts(fake)), 1)
                self.assertFalse(error.retryable)

    def test_a_tls_failure_is_not_retried(self):
        error, fake = self.failure(
            [meta_reply(), whoami_reply(), requests.exceptions.SSLError("bad")],
            client=self.retrying(),
        )
        self.assertEqual(len(self.posts(fake)), 1)
        self.assertEqual(error.code, "tls_error")


class IdempotencyTests(RetryTestCase):
    def test_every_attempt_carries_the_same_key_and_the_same_body(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(),
            unavailable(),
            created_reply(),
        ]
        _, fake = self.push(replies, client=self.retrying())

        posts = self.posts(fake)
        self.assertEqual(len(posts), 3)
        keys = {p.kwargs["headers"]["Idempotency-Key"] for p in posts}
        bodies = {p.kwargs["data"] for p in posts}
        self.assertEqual(len(keys), 1)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(keys.pop(), self.document()["sync"]["id"])

    def test_every_attempt_is_its_own_request_for_the_platform_logs(self):
        replies = [meta_reply(), whoami_reply(), unavailable(), created_reply()]
        _, fake = self.push(replies, client=self.retrying())

        ids = [p.kwargs["headers"]["X-Request-ID"] for p in self.posts(fake)]
        self.assertEqual(len(set(ids)), 2)

    def test_only_idempotent_requests_are_retried(self):
        # Every POST carries the key, and the reads are safe to repeat
        replies = [meta_reply(), whoami_reply(), unavailable(), created_reply()]
        _, fake = self.push(replies, client=self.retrying())
        for request in fake.requests:
            if request.method == "POST":
                self.assertIn("Idempotency-Key", request.kwargs["headers"])
            else:
                self.assertEqual(request.method, "GET")

    def test_a_read_before_the_push_is_retried_within_the_same_budget(self):
        replies = [meta_reply(), unavailable(), whoami_reply(), created_reply()]
        result, fake = self.push(replies, client=self.retrying())

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [r.method for r in fake.requests], ["GET", "GET", "GET", "POST"]
        )


class RetryAfterTests(RetryTestCase):
    def test_retry_after_in_seconds_is_the_wait(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(**{"Retry-After": "7"}),
            created_reply(),
        ]
        self.push(replies, client=self.retrying())
        self.assertEqual(self.slept, [7.0])

    def test_retry_after_as_an_http_date_is_the_wait(self):
        date = formatdate(self.now + 12, usegmt=True)
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(**{"Retry-After": date}),
            created_reply(),
        ]
        self.push(replies, client=self.retrying())
        self.assertEqual(self.slept, [12.0])

    def test_a_date_in_the_past_means_no_wait(self):
        date = formatdate(self.now - 30, usegmt=True)
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(**{"Retry-After": date}),
            created_reply(),
        ]
        self.push(replies, client=self.retrying())
        self.assertEqual(self.slept, [0.0])

    def test_retry_after_replaces_the_backoff_even_when_it_is_long(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(**{"Retry-After": "90"}),
            created_reply(),
        ]
        self.push(replies, client=self.retrying(rng=Ceiling()))
        self.assertEqual(self.slept, [90.0])

    def test_a_retry_after_beyond_the_budget_gives_up_at_once(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(**{"Retry-After": "120"}),
        ]
        error, fake = self.failure(replies, client=self.retrying(budget=60.0))

        self.assertEqual(self.slept, [])
        self.assertEqual(len(self.posts(fake)), 1)
        self.assertTrue(error.exhausted)
        self.assertEqual(error.retry_after, 120.0)
        self.assertEqual(error.exit_code, codes.UPLOAD_UNAVAILABLE)

    def test_the_budget_runs_out_across_several_waits(self):
        client = self.retrying(budget=5.0, rng=Ceiling())
        replies = [meta_reply(), whoami_reply(), *[unavailable()] * 3]
        error, fake = self.failure(replies, client=client)

        # Waits of 1 and 2 seconds fit; the 4 second wait would not
        self.assertEqual(self.slept, [1.0, 2.0])
        self.assertEqual(len(self.posts(fake)), 3)
        self.assertTrue(error.exhausted)

    def test_an_unusable_retry_after_falls_back_to_the_backoff(self):
        replies = [
            meta_reply(),
            whoami_reply(),
            unavailable(**{"Retry-After": "soon"}),
            created_reply(),
        ]
        self.push(replies, client=self.retrying(rng=Ceiling()))
        self.assertEqual(self.slept, [1.0])

    def test_parse_retry_after(self):
        now = 1_800_000_000.0
        self.assertEqual(parse_retry_after("5", now), 5.0)
        self.assertEqual(parse_retry_after("0", now), 0.0)
        self.assertEqual(parse_retry_after("2.5", now), 2.5)
        self.assertEqual(
            parse_retry_after(formatdate(now + 40, usegmt=True), now), 40.0
        )
        self.assertEqual(parse_retry_after(formatdate(now - 40, usegmt=True), now), 0.0)
        for value in (None, "", "  ", "soon", "-", "Thu, nonsense"):
            self.assertIsNone(parse_retry_after(value, now))
        self.assertEqual(parse_retry_after("-5", now), 0.0)


class BudgetTests(RetryTestCase):
    def test_without_a_budget_there_is_one_attempt(self):
        error, fake = self.failure(
            [meta_reply(), whoami_reply(), unavailable()],
            client=self.client(budget=0.0),
        )
        self.assertEqual(len(self.posts(fake)), 1)
        self.assertTrue(error.exhausted)

    def test_the_client_takes_the_budget_from_the_settings(self):
        from utils.settings import Setting, Settings

        settings = Settings(
            host=Setting(HOST, "test"),
            api_key=Setting(KEY, "test"),
            upload_budget=Setting(42.0, "test"),
        )
        self.assertEqual(ApiClient.from_settings(settings, environ={}).budget, 42.0)

    def test_the_default_budget_is_five_minutes(self):
        from utils.settings import Setting, Settings

        settings = Settings(host=Setting(HOST, "test"), api_key=Setting(KEY, "test"))
        self.assertEqual(ApiClient.from_settings(settings, environ={}).budget, 300.0)

    def test_two_pushes_do_not_share_one_budget(self):
        client = self.retrying(budget=3.0, rng=Ceiling())
        for _ in range(2):
            replies = [unavailable(), created_reply()]
            with fake_http(
                *([meta_reply(), whoami_reply()] if _ == 0 else [whoami_reply()]),
                *replies,
            ):
                client.push(self.document(), name="n", exit_strategy=1)

        self.assertEqual(self.slept, [1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
