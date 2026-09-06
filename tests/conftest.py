"""Offline test harness.

The fixtures in ``tests/fixtures`` are recorded from the live service on
2026-09-06. A few are not recordings but constructions, because the live
account cannot produce the shape in question: ``movie_detail_no_prediction``
(the account is past cold start), ``ratings_export_awkward`` (no title on the
account contains a comma, a quote or a newline) and ``ratings_export_html``
(an expired session answering HTML with a 200). Each of those says so in a
``_note`` key. Some tests also mutate a loaded fixture in-place to construct a
shape the live account cannot produce — each says why.

A fixture whose ``json`` is null and whose ``text`` is set records a non-JSON
body: ``FakeResponse.json()`` raises ``ValueError`` for it, exactly as
``requests`` does. That is how the CSV export is served offline.

Nothing here touches the network: the client accepts an injected
``requests.Session``-alike, and ``FakeHTTP`` is that alike.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Callable
from urllib.parse import urlparse

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    """Return the recorded envelope: ``{"status_code", "json", "text"}``."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeResponse:
    def __init__(self, status_code: int, payload: Any, text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON object could be decoded")
        return self._payload


def response_from_fixture(name: str) -> FakeResponse:
    rec = fixture(name)
    return FakeResponse(rec["status_code"], rec["json"], rec.get("text"))


class FakeHTTP:
    """Minimal stand-in for ``requests.Session``.

    ``routes`` maps ``(METHOD, path)`` to either a FakeResponse or a callable
    taking ``(params, payload)`` and returning one. Every call is recorded in
    ``.calls`` so tests can assert on request count and ordering — that is what
    proves the ratings/prediction iterators are lazy rather than eager.
    """

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None):
        self.routes = routes or {}
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.closed = False

    def request(
        self, method, url, *, params=None, json=None, timeout=None, headers=None, **kw
    ):
        path = urlparse(url).path
        self.calls.append(
            {
                "method": method.upper(),
                "path": path,
                "params": params,
                "json": json,
                "headers": headers or {},
            }
        )
        handler = self.routes.get((method.upper(), path))
        if handler is None:
            raise AssertionError(f"unrouted request: {method.upper()} {path}")
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            return handler(params, json)
        return handler

    def close(self):
        self.closed = True


def paged_route(prefix: str) -> Callable[[Any, Any], FakeResponse]:
    """Serve ``<prefix>page<N>.json`` keyed off the request's ``page`` param."""

    def handler(params, _payload):
        page = int((params or {}).get("page", 1))
        return response_from_fixture(f"{prefix}page{page}")

    return handler


@pytest.fixture
def logged_in_http() -> FakeHTTP:
    return FakeHTTP({("POST", "/api/sessions"): response_from_fixture("login_success")})


def load_dotenv(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@pytest.fixture(scope="session")
def live_credentials() -> tuple[str, str]:
    """Credentials for the live smoke test, from the environment or .env.

    Never asserted on, never printed — a failing assertion prints its operands.
    """
    env = dict(os.environ)
    for key, value in load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env").items():
        env.setdefault(key, value)
    user = env.get("MOVIELENS_TEST_USER")
    password = env.get("MOVIELENS_TEST_PASSWORD")
    if not user or not password:
        pytest.skip("MOVIELENS_TEST_USER / MOVIELENS_TEST_PASSWORD not set")
    return user, password
