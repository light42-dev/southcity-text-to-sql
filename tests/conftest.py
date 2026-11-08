"""Pytest wiring for the black-box API tests.

Configure with environment variables:
  API_BASE_URL   default http://localhost:8000 (e.g. https://your-app.vercel.app)
  API_CHAT_PATH  default /api/chat
  API_TIMEOUT    seconds per request, default 120 (free OpenRouter models are slow)
  SQL_DIALECT    sqlglot dialect used to parse returned SQL (postgres, sqlite, tsql...)
  MAX_ROWS       row cap the API must respect, default 200
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from support import Reply, invariant_problems

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
CHAT_PATH = os.getenv("API_CHAT_PATH", "/api/chat")
TIMEOUT = float(os.getenv("API_TIMEOUT", "120"))


def pytest_collection_modifyitems(config, items):
    """API tests are skipped (not failed) when no server is reachable."""
    if not any("api" in item.keywords for item in items):
        return
    try:
        httpx.get(BASE_URL, timeout=10)
    except httpx.HTTPError as e:
        skip = pytest.mark.skip(reason=f"API not reachable at {BASE_URL}: {e}")
        for item in items:
            if "api" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        yield client


@pytest.fixture
def post(http):
    """Raw POST helper: returns (status_code, json_or_text)."""

    def _post(payload: Any):
        r = http.post(CHAT_PATH, json=payload)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, body

    return _post


@pytest.fixture
def ask(post):
    """Ask a question as `username`; asserts HTTP 200 + global invariants."""

    def _ask(username: str, message: str, history: list[dict] | None = None,
             expect_http: int = 200) -> Reply:
        payload: dict[str, Any] = {"username": username, "message": message}
        if history is not None:
            payload["history"] = history
        code, body = post(payload)
        reply = Reply.from_response(code, body)
        assert code == expect_http, f"HTTP {code} for {username}: {body!r}"
        problems = invariant_problems(reply, username, message)
        assert not problems, f"Invariant violations for {username}: {problems}\n{reply!r}"
        return reply

    return _ask
