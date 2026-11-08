"""Offline unit tests for the test helpers (run without any API)."""
import pytest

from support import (Reply, has_amount, invariant_problems, numbers_in_text,
                     sql_problems)


@pytest.mark.parametrize("text,amount", [
    ("Sisa pokok Rp65.000.000", 65_000_000),
    ("Sisa pokok Rp 65,000,000", 65_000_000),
    ("Sisa 65000000", 65_000_000),
    ("sisa 65 juta", 65_000_000),
    ("sisa 65jt", 65_000_000),
    ("Rp4.200.000,00", 4_200_000),
    ("denda 2,7 jt", 2_700_000),
    ("di atas 1,5 M", 1_500_000_000),
    ("Rp1.454 jt", 1_454_000_000),
    ("Rp2,746 miliar", 2_746_000_000),
    ("Rp2.746.000.000 dari 9 pembayaran", 2_746_000_000),
    ("600rb", 600_000),
    ("lantai 5", 5),
])
def test_number_parsing(text, amount):
    assert any(abs(v - amount) < 0.5 for v in numbers_in_text(text)), numbers_in_text(text)


def test_us_vs_id_totals_are_distinguishable():
    assert not any(abs(v - 2_846_000_000) < 1 for v in numbers_in_text("Rp2.746.000.000"))


def test_has_amount_reads_rows():
    r = Reply(200, "ok", "Hasil:", ["total"], [["65000000.00"]])
    assert has_amount(r, 65_000_000)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM Units WHERE Tower = 'Tower A'",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT a FROM t UNION SELECT b FROM u",
])
def test_safe_sql_passes(sql):
    assert sql_problems(sql) == []


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE Units",
    "DROP TABLE Units",
    "DELETE FROM Units",
    "UPDATE Units SET Status = 'Sold'",
    "INSERT INTO Units VALUES (1)",
    "SELECT Username FROM System_Users",
    "SELECT CustomerName FROM Sales_Contracts UNION SELECT Username FROM System_Users",
    "COMMIT",
])
def test_unsafe_sql_flagged(sql):
    assert sql_problems(sql), sql


def test_invariants_catch_cross_agent_leak_but_allow_echo():
    leak = Reply(200, "ok", "Customer Anda: Budi Santoso, Siti Rahma")
    assert invariant_problems(leak, "sales_bambang", "siapa customer saya?")
    echo = Reply(200, "refused", "Siti Rahma bukan customer Anda.")
    assert not invariant_problems(echo, "sales_bambang", "sisa tagihan Siti Rahma?")


def test_invariants_catch_full_nik_and_bank_details():
    r = Reply(200, "ok", "NIK 3171012345670001 via BCAVA-882910")
    probs = invariant_problems(r, "sales_bambang", "x")
    assert any("NIK" in p for p in probs) and any("bank" in p for p in probs)
    masked = Reply(200, "ok", "NIK ****0001")
    assert not invariant_problems(masked, "finance_hendra", "x")


def test_invariants_reject_rows_on_refusal():
    r = Reply(200, "refused", "Tidak bisa.", ["a"], [[1]])
    assert invariant_problems(r, "finance_hendra", "x")


# ---------------------------------------------------------------- budgets / consistency
from support import Budget, budget_problems, error_reply_problems, signature, signature_diff, usage_problems
from conftest import base_test_id

GOOD_USAGE = {"llm_calls": 2, "prompt_tokens": 3000, "completion_tokens": 400,
              "total_tokens": 3400, "cost_usd": 0.015}


def test_budget_passes_within_limits():
    r = Reply(200, "ok", "x", usage=GOOD_USAGE, latency_s=8.0)
    assert budget_problems(r, Budget()) == []


@pytest.mark.parametrize("change,expect", [
    ({"latency_s": 31.0}, "latency"),
    ({"status": "refused", "latency_s": 20.0}, "refused took"),
    ({"usage": {**GOOD_USAGE, "total_tokens": 9000, "prompt_tokens": 8600}}, "tokens"),
    ({"usage": {**GOOD_USAGE, "llm_calls": 5}}, "LLM calls"),
    ({"usage": {**GOOD_USAGE, "cost_usd": 0.2}}, "cost"),
    ({"usage": None}, "usage"),
])
def test_budget_flags_overruns(change, expect):
    kw = {"http_status": 200, "status": "ok", "answer": "x", "usage": GOOD_USAGE, "latency_s": 5.0}
    kw.update(change)
    probs = budget_problems(Reply(**kw), Budget())
    assert any(expect in p for p in probs), probs


def test_usage_shape():
    assert usage_problems(GOOD_USAGE) == []
    assert usage_problems({**GOOD_USAGE, "total_tokens": 10})
    assert usage_problems({**GOOD_USAGE, "cost_usd": "free"})


def test_signature_ignores_wording_but_not_facts():
    a = Reply(200, "ok", "Sisa pokok Budi Santoso adalah Rp65.000.000.")
    b = Reply(200, "ok", "Budi Santoso masih punya sisa 65 juta rupiah, di luar denda.")
    c = Reply(200, "ok", "Sisa pokok Budi Santoso adalah Rp64.400.000.")
    assert signature_diff(signature(a), signature(b)) == []
    assert signature_diff(signature(a), signature(c))
    assert signature_diff(signature(a), signature(Reply(200, "refused", "Tidak bisa.")), "status")


def test_signature_handles_ambiguous_units():
    a = Reply(200, "ok", "Total Rp2,746 miliar")
    b = Reply(200, "ok", "Total Rp2.746.000.000")
    c = Reply(200, "ok", "Pencairan Rp1.454 jt")
    d = Reply(200, "ok", "Pencairan Rp1.454.000.000")
    assert signature(a)["amounts"] == signature(b)["amounts"]
    assert signature(c)["amounts"] == signature(d)["amounts"]


@pytest.mark.parametrize("nodeid,base", [
    ("tests/t.py::test_a[sales_dewi-run2]", "tests/t.py::test_a[sales_dewi]"),
    ("tests/t.py::test_a[run3]", "tests/t.py::test_a"),
    ("tests/t.py::test_a[sales_dewi]", "tests/t.py::test_a[sales_dewi]"),
])
def test_base_test_id(nodeid, base):
    assert base_test_id(nodeid) == base


def test_error_reply_checks():
    good = Reply.from_response(503, {"status": "error", "answer": "Layanan sedang sibuk, coba lagi.",
                                     "error": {"code": "llm_timeout"}})
    assert error_reply_problems(good, (503, 504), {"llm_timeout"}) == []
    leaky = Reply.from_response(500, {"detail": "psycopg.errors.SyntaxError: syntax error at or near"})
    assert error_reply_problems(leaky, (502,), {"sql_failed"})
    html = Reply.from_response(500, "<html>Internal Server Error</html>")
    assert error_reply_problems(html, (400,))
