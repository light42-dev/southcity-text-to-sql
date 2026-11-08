"""Pytest wiring for the black-box API tests.

Configure with environment variables (see tests/README.md for the full list):
  API_BASE_URL        default http://localhost:8000 (e.g. https://your-app.vercel.app)
  API_CHAT_PATH       default /api/chat
  API_HEALTH_PATH     default /api/health
  API_TIMEOUT         client timeout per request in seconds, default 120
  REQUEST_INTERVAL_S  minimum gap between requests (rate limits), default 0
  CONSISTENCY_RUNS    run every LLM test N times and compare answers, default 1
  CONSISTENCY_CHECK   "facts" (status + amounts/customers/units) or "status", default facts
  CHECK_BUDGETS       set to 0 to skip latency/token/cost checks, default 1
"""
from __future__ import annotations

import os
import re
import statistics
import threading
import time
from typing import Any

import httpx
import pytest

from support import (BUDGET, Reply, budget_problems, invariant_problems, signature,
                     signature_diff)

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
CHAT_PATH = os.getenv("API_CHAT_PATH", "/api/chat")
HEALTH_PATH = os.getenv("API_HEALTH_PATH", "/api/health")
TIMEOUT = float(os.getenv("API_TIMEOUT", "120"))
REQUEST_INTERVAL_S = float(os.getenv("REQUEST_INTERVAL_S", "0"))
CONSISTENCY_CHECK = os.getenv("CONSISTENCY_CHECK", "facts")

# Markers whose tests are not repeated in consistency mode
NO_REPEAT = ("failure", "budget", "contract")

METRICS: list[dict] = []  # one entry per chat request made through `ask`
_pace_lock = threading.Lock()
_last_request = [0.0]


def pytest_addoption(parser):
    parser.addoption("--consistency-runs", type=int,
                     default=int(os.getenv("CONSISTENCY_RUNS", "1")),
                     help="Run every LLM test N times and check the answers match.")
    parser.addoption("--no-budgets", action="store_true",
                     default=os.getenv("CHECK_BUDGETS", "1") == "0",
                     help="Skip latency/token/cost budget checks.")


def pytest_collection_modifyitems(config, items):
    """API tests are skipped (not failed) when no server is reachable."""
    if not any("api" in item.keywords for item in items):
        return
    try:
        httpx.get(BASE_URL + HEALTH_PATH, timeout=10)
    except httpx.HTTPError as e:
        skip = pytest.mark.skip(reason=f"API not reachable at {BASE_URL}: {e}")
        for item in items:
            if "api" in item.keywords:
                item.add_marker(skip)


# ------------------------------------------------------------ consistency runs
def pytest_generate_tests(metafunc):
    runs = metafunc.config.getoption("--consistency-runs")
    if runs > 1 and "ask" in metafunc.fixturenames and not any(
            metafunc.definition.get_closest_marker(m) for m in NO_REPEAT):
        metafunc.parametrize("consistency_run", list(range(1, runs + 1)),
                             ids=[f"run{i}" for i in range(1, runs + 1)], indirect=True)


@pytest.fixture
def consistency_run(request):
    return getattr(request, "param", 1)


def base_test_id(nodeid: str) -> str:
    """'f.py::t[a-run2]' -> 'f.py::t[a]';  'f.py::t[run2]' -> 'f.py::t'."""
    out = re.sub(r"-run\d+\]$", "]", nodeid)
    return re.sub(r"\[run\d+\]$", "", out)


# ------------------------------------------------------------ transport
def _pace():
    if REQUEST_INTERVAL_S <= 0:
        return
    with _pace_lock:
        wait = _last_request[0] + REQUEST_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()


@pytest.fixture(scope="session")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        yield client


@pytest.fixture
def send(http):
    """Low-level request: returns a Reply with latency and headers."""

    def _send(method: str = "POST", path: str = CHAT_PATH, *, json: Any = None,
              content: bytes | str | None = None, headers: dict | None = None,
              timeout: float | None = None) -> Reply:
        _pace()
        kwargs: dict[str, Any] = {"headers": headers or {}}
        if json is not None:
            kwargs["json"] = json
        if content is not None:
            kwargs["content"] = content
        if timeout is not None:
            kwargs["timeout"] = timeout
        t0 = time.perf_counter()
        r = http.request(method, path, **kwargs)
        latency = time.perf_counter() - t0
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return Reply.from_response(r.status_code, body, latency, dict(r.headers))

    return _send


@pytest.fixture
def post(send):
    """Raw POST helper: returns (status_code, json_or_text)."""

    def _post(payload: Any, headers: dict | None = None):
        reply = send(json=payload, headers=headers)
        return reply.http_status, reply.raw

    return _post


# ------------------------------------------------------------ ask
@pytest.fixture
def ask(send, request, consistency_run):
    """Ask a question as `username`.

    Checks HTTP status, global invariants and (unless disabled) budgets, records
    metrics, and in consistency mode compares this answer with run 1.
    """
    config = request.config
    budgets_on = not config.getoption("--no-budgets")
    store = config.__dict__.setdefault("_sc_signatures", {})
    base = base_test_id(request.node.nodeid)
    counter = [0]

    def _ask(username: str, message: str, history: list[dict] | None = None,
             expect_http: int = 200, headers: dict | None = None) -> Reply:
        payload: dict[str, Any] = {"username": username, "message": message}
        if history is not None:
            payload["history"] = history
        reply = send(json=payload, headers=headers)
        counter[0] += 1
        METRICS.append({"test": base, "run": consistency_run, "user": username,
                        "http": reply.http_status, "status": reply.status,
                        "latency_s": reply.latency_s, "usage": reply.usage})

        assert reply.http_status == expect_http, f"HTTP {reply.http_status} for {username}: {reply.raw!r}"
        problems = invariant_problems(reply, username, message)
        assert not problems, f"Invariant violations for {username}: {problems}\n{reply!r}"
        if budgets_on:
            over = budget_problems(reply)
            assert not over, f"[budget] {username}: {over}\n{reply!r}"

        key = (base, counter[0])
        sig = signature(reply)
        if key not in store:
            store[key] = (consistency_run, sig)
        else:
            first_run, first_sig = store[key]
            diff = signature_diff(first_sig, sig, CONSISTENCY_CHECK)
            assert not diff, (f"[consistency] answer changed between run{first_run} and "
                              f"run{consistency_run}: {diff}\n{reply!r}")
        return reply

    return _ask


# ------------------------------------------------------------ run-level report
def _pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Aggregate budgets: median latency and total cost across the whole run."""
    config = session.config
    msgs: list[str] = []
    lat = [m["latency_s"] for m in METRICS if m["http"] == 200 and m["latency_s"] is not None]
    if lat and not config.getoption("--no-budgets"):
        med = statistics.median(lat)
        if med > BUDGET.latency_median_s:
            msgs.append(f"median latency {med:.1f}s > {BUDGET.latency_median_s:g}s")
        cost = sum((m["usage"] or {}).get("cost_usd", 0) or 0 for m in METRICS)
        if BUDGET.suite_max_cost_usd is not None and cost > BUDGET.suite_max_cost_usd:
            msgs.append(f"run cost ${cost:.4f} > ${BUDGET.suite_max_cost_usd:g}")
    config.__dict__["_sc_budget_msgs"] = msgs
    if msgs and session.exitstatus == 0:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not METRICS:
        return
    tr = terminalreporter
    tr.section("API latency / usage")
    lat = [m["latency_s"] for m in METRICS if m["latency_s"] is not None]
    usages = [m["usage"] for m in METRICS if isinstance(m["usage"], dict)]
    tr.write_line(f"requests: {len(METRICS)}   latency p50 {statistics.median(lat):.1f}s  "
                  f"p95 {_pct(lat, 95):.1f}s  max {max(lat):.1f}s")
    if usages:
        tok = [u.get("total_tokens", 0) or 0 for u in usages]
        calls = [u.get("llm_calls", 0) or 0 for u in usages]
        cost = sum(u.get("cost_usd", 0) or 0 for u in usages)
        tr.write_line(f"tokens/request avg {statistics.mean(tok):.0f}  max {max(tok)}   "
                      f"LLM calls max {max(calls)}   total cost ${cost:.4f}")
    slow = sorted((m for m in METRICS if m["latency_s"]), key=lambda m: -m["latency_s"])[:5]
    for m in slow:
        tr.write_line(f"  slowest: {m['latency_s']:.1f}s  {m['test']} (run{m['run']}, {m['user']})")
    for msg in config.__dict__.get("_sc_budget_msgs", []):
        tr.write_line(f"[budget] FAILED: {msg}", red=True)
