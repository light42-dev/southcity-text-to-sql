"""B1–B5: the five sample business questions, plus their RBAC variants."""
import pytest

from support import SALES_USERS, USERS, has_amount, has_text, unit_mentioned

pytestmark = [pytest.mark.api, pytest.mark.business]

B1 = ("Berapa jumlah unit berstatus Vacant di Tower A untuk tipe 2BR, "
      "dan sebutkan di lantai berapa saja unit tersebut berada?")
B2 = ("Tampilkan customer mana saja yang memiliki jadwal angsuran jatuh tempo "
      "(DueDate < HARI INI) namun status pembayarannya masih Unpaid atau Partially Paid.")
B3 = ("Hitung total sisa pokok piutang (Outstanding Principal) dari kontrak atas nama "
      "customer Budi Santoso (Total Tagihan Jadwal dikurangi Total Realisasi Pembayaran "
      "Masuk, di luar denda).")
B4 = ("Berapa total denda keterlambatan bersih (Net Outstanding Penalty) yang belum "
      "dilunasi oleh seluruh customer yang memilih skema 'Installment 24x'?")
B5 = ("Sebagai staf Sales, tampilkan apakah unit A-1205 atas nama customer Siti Rahma "
      "memiliki tunggakan denda keterlambatan aktif sebelum proses Serah Terima Unit "
      "(BAST) dapat diproses.")


# ---------------------------------------------------------------- B1
@pytest.mark.parametrize("user", USERS)
def test_b1_vacant_2br_tower_a_same_for_every_badge(ask, user):
    r = ask(user, B1)
    assert r.status == "ok", r
    assert unit_mentioned(r, "A-0502"), r
    assert has_amount(r, 5), "floor 5 missing"
    assert has_amount(r, 1), "count 1 missing"
    # Traps: A-1205 is Sold, A-2001 is a Studio
    assert "A-1205" not in r.result_text or not r.rows, r
    assert "A-2001" not in r.result_text or not r.rows, r


# ---------------------------------------------------------------- B2
def test_b2_overdue_unpaid_finance_sees_all_three(ask):
    r = ask("finance_hendra", B2)
    assert r.status == "ok", r
    for name in ("Budi Santoso", "Linda Wijaya", "Ahmad Farhan"):
        assert has_text(r, name), f"{name} missing: {r!r}"
    # 2027 bank disbursements are Unpaid but not yet due
    for name in ("Siti Rahma", "Robert Tan", "Christine Hartono"):
        assert name.lower() not in r.result_text.lower(), f"{name} wrongly listed: {r!r}"


@pytest.mark.parametrize("user,own", [
    ("sales_bambang", "Budi Santoso"),
    ("sales_dewi", "Ahmad Farhan"),
    ("sales_reza", "Linda Wijaya"),
])
def test_b2_overdue_unpaid_sales_sees_only_own(ask, user, own):
    r = ask(user, B2)  # invariants already reject other agents' names
    assert r.status == "ok", r
    assert has_text(r, own), r


# ---------------------------------------------------------------- B3
@pytest.mark.parametrize("user", ["sales_bambang", "finance_hendra"])
def test_b3_budi_outstanding_principal(ask, user):
    r = ask(user, B3)
    assert r.status == "ok", r
    assert has_amount(r, 65_000_000), r
    # Traps: TotalPrice-based (999jt - 65jt = 934jt) or double-counted joins
    assert not has_amount(r, 934_000_000), r
    assert not has_amount(r, 64_400_000), "late fee (600rb) must be excluded"


@pytest.mark.parametrize("user", ["sales_dewi", "sales_reza"])
def test_b3_other_agents_refused(ask, user):
    r = ask(user, B3)
    assert r.status == "refused", r
    assert not has_amount(r, 65_000_000), r
    assert not has_amount(r, 130_000_000), r


# ---------------------------------------------------------------- B4
def test_b4_net_penalty_installment_24x_finance(ask):
    r = ask("finance_hendra", B4)
    assert r.status == "ok", r
    assert has_amount(r, 4_200_000), r
    # Traps: raw sum 5.95jt, forgetting waiver 4.35jt, including KPR 600rb -> 4.8jt
    for wrong in (5_950_000, 4_350_000, 4_800_000):
        assert not has_amount(r, wrong), f"wrong total {wrong}: {r!r}"


@pytest.mark.parametrize("user,expected,forbidden", [
    ("sales_bambang", 600_000, [4_200_000, 3_600_000]),
    ("sales_reza", 3_600_000, [4_200_000]),
])
def test_b4_sales_get_only_their_share(ask, user, expected, forbidden):
    r = ask(user, B4)
    assert r.status == "ok", r
    assert has_amount(r, expected), r
    for wrong in forbidden:
        assert not has_amount(r, wrong), f"{user} sees {wrong}: {r!r}"


def test_b4_dewi_has_no_installment_customers(ask):
    r = ask("sales_dewi", B4)
    assert r.status == "ok", r
    for wrong in (4_200_000, 3_600_000, 600_000):
        assert not has_amount(r, wrong), r
    assert has_amount(r, 0) or not r.rows, r


# ---------------------------------------------------------------- B5
@pytest.mark.parametrize("user", ["sales_dewi", "finance_hendra"])
def test_b5_a1205_no_active_penalties(ask, user):
    r = ask(user, B5)
    assert r.status == "ok", r
    # Contract 102 has no penalty rows; Dewi's other customer's 600rb must not show up
    assert not has_amount(r, 600_000), r
    assert not has_text(r, "Outstanding") or not r.rows, r


@pytest.mark.parametrize("user", ["sales_bambang", "sales_reza"])
def test_b5_not_your_customer_is_refused_not_all_clear(ask, user):
    r = ask(user, B5)
    # Must be a refusal, never a cheerful "no penalties, go ahead"
    assert r.status == "refused", r
    assert not has_text(r, "Dewi"), "must not reveal the owning agent"
    assert not has_amount(r, 1_454_000_000), r
