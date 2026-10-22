import json
from collections import namedtuple
from contextlib import contextmanager
from unittest import mock

import requests

RecordedRequest = namedtuple("RecordedRequest", "method url kwargs")


def make_response(status=200, json_body=None, text="", headers=None, content=None):
    response = requests.Response()
    response.status_code = status
    response.encoding = "utf-8"
    response.headers.update(headers or {})
    if content is not None:
        response._content = content
    elif json_body is not None:
        response._content = json.dumps(json_body).encode("utf-8")
        response.headers.setdefault("Content-Type", "application/json")
    else:
        response._content = text.encode("utf-8")
    return response


class FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, session, method, url, **kwargs):
        self.requests.append(RecordedRequest(method.upper(), url, kwargs))
        if not self.replies:
            raise AssertionError(f"unexpected {method.upper()} request to {url}")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@contextmanager
def fake_http(*replies):
    # Every requests.get/post/Session call goes through Session.request, so
    # patching it there covers all of them. Each reply is returned in order;
    # an exception instance in the list is raised instead (e.g.
    # requests.ConnectionError). A request beyond the queued replies fails the
    # test, and fake.requests records what was sent.
    fake = FakeHTTP(replies)
    with mock.patch.object(
        requests.Session, "request", autospec=True, side_effect=fake
    ):
        yield fake
    if fake.replies:
        raise AssertionError(f"{len(fake.replies)} queued replies were never used")
