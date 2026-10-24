import unittest

import requests

from tests.http_fixtures import fake_http, make_response


class FakeHTTPTests(unittest.TestCase):
    def test_replies_are_returned_in_order_and_requests_recorded(self):
        with fake_http(
            make_response(json_body={"n": 1}),
            make_response(status=503, text="down"),
        ) as fake:
            first = requests.get("https://api.test/one", timeout=5)
            second = requests.post("https://api.test/two", json={"a": 1})

        self.assertEqual(first.json(), {"n": 1})
        self.assertEqual(second.status_code, 503)
        self.assertEqual(
            [(r.method, r.url) for r in fake.requests],
            [("GET", "https://api.test/one"), ("POST", "https://api.test/two")],
        )
        self.assertEqual(fake.requests[1].kwargs["json"], {"a": 1})

    def test_exception_replies_are_raised(self):
        with fake_http(requests.ConnectionError("refused")):
            with self.assertRaises(requests.ConnectionError):
                requests.get("https://api.test/")

    def test_request_beyond_the_queued_replies_fails(self):
        with fake_http():
            with self.assertRaises(AssertionError):
                requests.get("https://api.test/")

    def test_unused_replies_fail_the_test(self):
        with self.assertRaises(AssertionError):
            with fake_http(make_response()):
                pass

    def test_raise_for_status_follows_the_status_code(self):
        with fake_http(make_response(status=500)):
            response = requests.get("https://api.test/")
        with self.assertRaises(requests.HTTPError):
            response.raise_for_status()


class FakeHTTPExampleTests(unittest.TestCase):
    def test_a_request_through_a_session_is_recorded_with_its_headers(self):
        with fake_http(make_response(json_body={"object": "meta"})) as fake:
            with requests.Session() as session:
                response = session.get(
                    "https://api.test/api/v2/meta", headers={"Accept": "text/plain"}
                )

        self.assertEqual(response.json(), {"object": "meta"})
        (request,) = fake.requests
        self.assertEqual(request.url, "https://api.test/api/v2/meta")
        self.assertEqual(request.kwargs["headers"], {"Accept": "text/plain"})

    def test_a_connection_error_reply_is_raised_from_a_session(self):
        with fake_http(requests.ConnectionError("refused")):
            with requests.Session() as session:
                with self.assertRaises(requests.ConnectionError):
                    session.get("https://api.test/")


if __name__ == "__main__":
    unittest.main()
