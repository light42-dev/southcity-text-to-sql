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
