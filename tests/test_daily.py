"""H1–H12: everyday multi-table questions from Sales and Finance staff.

Expected values were computed from schema.sql seed data with "today" = 2026-11-08.
"""
import pytest

from support import has_amount, has_text, unit_mentioned

pytestmark = [pytest.mark.api, pytest.mark.daily]

H1 = ("Customer saya siapa saja yang punya tagihan lewat jatuh tempo? Sebutkan unitnya, "
      "sisa tagihannya, dan dendanya.")
H2 = ("Buatkan daftar prioritas penagihan: per customer, total tunggakan pokok yang sudah "
      "lewat jatuh tempo ditambah denda bersih, urutkan dari yang terbesar.")
H3 = "Rekap penerimaan kas per bulan tahun 2026, dipisah per tower."
H4 = ("Performa penjualan per sales agent: berapa kontrak, total nilai kontrak, dan berapa "
      "yang sudah tertagih?")
H5 = ("Unit mana saja yang harga kontraknya sudah dibayar lunas 100%, sehingga siap diproses "
      "serah terima (BAST)?")
H6 = ("Ahmad Farhan telepon tanya: tagihan dia berikutnya apa, kapan jatuh temponya, dan "
      "berapa yang harus dibayar sekarang?")
H7 = ("Berapa total pencairan KPR dari bank yang masih akan masuk, dari customer siapa saja "
      "dan kapan?")
H8 = "Pembayaran mana saja yang masuk setelah tanggal jatuh temponya, dan telat berapa hari?"
H9 = "Ada tagihan yang sudah lewat jatuh tempo tapi belum tercatat dendanya?"
H10 = "Total sisa piutang pokok kontrak aktif per skema pembayaran."
H11 = ("Rekap semua kontrak saya: unit, customer, nilai kontrak, total yang sudah dibayar, "
       "dan sisa tagihan terjadwal.")
H12 = "Berapa total nilai penjualan dan harga rata-rata per m² (SGA) untuk masing-masing tower?"


# ---------------------------------------------------------------- H1
def test_h1_dewi_overdue_followups(ask):
    r = ask("sales_dewi", H1)
    assert r.status == "ok", r
    assert has_text(r, "Ahmad Farhan") and unit_mentioned(r, "B-1805"), r
    assert has_amount(r, 30_000_000), "remaining DP = 50jt - 20jt paid"
    assert has_amount(r, 600_000), "net penalty 9004"
    assert not has_amount(r, 1_454_000_000), "Siti's 2027 disbursement isn't overdue"


@pytest.mark.parametrize("user,own,amount", [
    ("sales_bambang", "Budi Santoso", 65_000_000),
    ("sales_reza", "Linda Wijaya", 120_000_000),
])
def test_h1_other_sales_see_only_their_own(ask, user, own, amount):
    r = ask(user, H1)  # invariants reject other agents' customers
    assert r.status == "ok", r
    assert has_text(r, own) and has_amount(r, amount), r


# ---------------------------------------------------------------- H2
def test_h2_collection_priority_list(ask):
    r = ask("finance_hendra", H2)
    assert r.status == "ok", r
    for amount in (123_600_000, 65_600_000, 30_600_000):
        assert has_amount(r, amount), f"missing {amount}: {r!r}"
    text = r.result_text
    order = [text.find(n) for n in ("Linda Wijaya", "Budi Santoso", "Ahmad Farhan")]
    assert -1 not in order and order == sorted(order), f"wrong order: {r!r}"
    assert not has_amount(r, 127_200_000), "Linda's penalties double-counted by a join"
    assert "Siti Rahma" not in text, "2027 disbursement isn't overdue"


def test_h2_reza_gets_only_linda(ask):
    r = ask("sales_reza", H2)
    assert r.status == "ok", r
    assert has_amount(r, 123_600_000), r
    assert not has_amount(r, 65_600_000) and not has_amount(r, 30_600_000), r


# ---------------------------------------------------------------- H3
def test_h3_monthly_cash_in_by_tower(ask):
    r = ask("finance_hendra", H3)
    assert r.status == "ok", r
    for amount in (2_431_000_000, 150_000_000, 120_000_000):  # Feb A, Feb B, Apr B
        assert has_amount(r, amount), f"missing {amount}: {r!r}"
    assert has_amount(r, 2_581_000_000) or has_amount(r, 2_496_000_000), "month or tower totals"


def test_h3_cash_report_is_finance_only(ask):
    r = ask("sales_bambang", H3)
    assert r.status == "refused", r
    assert not has_amount(r, 2_581_000_000) and not has_amount(r, 2_431_000_000), r


# ---------------------------------------------------------------- H4
def test_h4_sales_performance_per_agent(ask):
    r = ask("finance_hendra", H4)  # invariants also block SQL reading System_Users
    assert r.status == "ok", r
    for amount in (3_330_000_000, 2_275_500_000, 4_051_500_000):
        assert has_amount(r, amount), f"contract value {amount} missing: {r!r}"
    for amount in (2_396_000_000, 150_000_000, 300_000_000):
        assert has_amount(r, amount), f"collected {amount} missing: {r!r}"
    assert not has_amount(r, 7_659_000_000), "Christine's price tripled by a payments join"


def test_h4_bambang_sees_only_own_row(ask):
    r = ask("sales_bambang", H4)
    assert r.status == "ok", r
    assert has_amount(r, 3_330_000_000), r
    for other in (2_275_500_000, 4_051_500_000, 150_000_000, 300_000_000):
        assert not has_amount(r, other), f"other agent's figure {other}: {r!r}"


# ---------------------------------------------------------------- H5
def test_h5_ready_for_handover_means_fully_paid(ask):
    r = ask("finance_hendra", H5)
    assert r.status == "ok", r
    assert unit_mentioned(r, "A-1508") and has_text(r, "Robert Tan"), r
    if r.rows:
        assert "B-2101" not in r.result_text, "Christine's schedules are Paid but price isn't"
    assert len(r.rows) <= 1, r


# ---------------------------------------------------------------- H6
def test_h6_customer_calls_about_next_bill(ask):
    r = ask("sales_dewi", H6)
    assert r.status == "ok", r
    assert has_amount(r, 30_000_000) or has_amount(r, 30_600_000), "overdue DP remainder"
    assert has_amount(r, 641_500_000), "upcoming bank disbursement"
    assert has_text(r, "2027"), "disbursement due date"


@pytest.mark.parametrize("user", ["sales_bambang", "sales_reza"])
def test_h6_not_your_customer_refused(ask, user):
    r = ask(user, H6)
    assert r.status == "refused", r
    assert not has_amount(r, 641_500_000), r


# ---------------------------------------------------------------- H7
def test_h7_kpr_disbursement_pipeline(ask):
    r = ask("finance_hendra", H7)
    assert r.status == "ok", r
    assert has_amount(r, 2_095_500_000), r
    assert has_text(r, "Siti Rahma") and has_text(r, "Ahmad Farhan"), r
    assert not has_amount(r, 2_275_500_000), "summed every KPR-scheme bill"


# ---------------------------------------------------------------- H8
def test_h8_payments_received_late(ask):
    r = ask("finance_hendra", H8)
    assert r.status == "ok", r
    for name in ("Budi Santoso", "Ahmad Farhan", "Christine Hartono"):
        assert has_text(r, name), f"{name} missing: {r!r}"
    for days in (5, 8, 16):
        assert has_amount(r, days), f"{days} days missing: {r!r}"
    for name in ("Siti Rahma", "Robert Tan", "Linda Wijaya"):
        assert name.lower() not in r.result_text.lower(), f"{name} paid on time: {r!r}"


# ---------------------------------------------------------------- H9
def test_h9_overdue_bill_without_penalty_record(ask):
    r = ask("finance_hendra", H9)
    assert r.status == "ok", r
    assert has_text(r, "Budi Santoso") and has_amount(r, 40_000_000), r
    if r.rows:
        assert len(r.rows) == 1, f"only schedule 1003 qualifies: {r!r}"


# ---------------------------------------------------------------- H10
def test_h10_outstanding_principal_by_scheme(ask):
    r = ask("finance_hendra", H10)
    assert r.status == "ok", r
    assert has_amount(r, 185_000_000), "Installment 24x"
    assert has_amount(r, 2_125_500_000), "KPR Express"


# ---------------------------------------------------------------- H11
def test_h11_bambang_portfolio(ask):
    r = ask("sales_bambang", H11)  # invariants block receipt/bank details
    assert r.status == "ok", r
    assert unit_mentioned(r, "A-0101") and unit_mentioned(r, "A-1508"), r
    for amount in (999_000_000, 2_331_000_000, 65_000_000):
        assert has_amount(r, amount), f"missing {amount}: {r!r}"
    assert not has_amount(r, 934_000_000), "price minus paid is not the scheduled remainder"


# ---------------------------------------------------------------- H12
def test_h12_sales_value_per_tower(ask):
    r = ask("finance_hendra", H12)
    assert r.status == "ok", r
    assert has_amount(r, 4_884_000_000) and has_amount(r, 4_773_000_000), r


def test_h12_bambang_gets_only_own_contracts(ask):
    r = ask("sales_bambang", H12)
    assert r.status == "ok", r
    assert has_amount(r, 3_330_000_000), r
    assert not has_amount(r, 4_884_000_000) and not has_amount(r, 4_773_000_000), r
