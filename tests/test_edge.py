"""E1–E11: gibberish, pasted SQL, typos, jargon, ambiguity, locale, ties, account switches."""
import pytest

from support import has_amount, has_text, unit_mentioned

pytestmark = [pytest.mark.api, pytest.mark.edge]


# ---------------------------------------------------------------- E1
def test_e1_gibberish_asks_back(ask):
    r = ask("sales_bambang", "  ?asdkjh!!!!!!!!!!")
    assert r.status == "clarify", r
    assert not r.sql and not r.rows, "don't guess a query from noise"


@pytest.mark.parametrize("message", ["", "   ", "\n\t"])
def test_e1_empty_message_rejected_at_the_door(post, message):
    code, body = post({"username": "sales_bambang", "message": message})
    assert code in (400, 422), (code, body)


def test_e1_huge_paste_rejected_or_refused(post):
    code, body = post({"username": "sales_bambang", "message": "unit " * 12_000})
    assert code in (400, 413, 422) or (code == 200 and body.get("status") in {"refused", "clarify"}), (code, body)


# ---------------------------------------------------------------- E2
def test_e2_broken_pasted_sql_rewritten_with_badge_filter(ask):
    r = ask("sales_bambang", "SELECT TOP 5 * form Sales_Contracts wher TotalPrice > 1000000000")
    assert r.status == "ok", r
    assert has_text(r, "Robert Tan") and has_amount(r, 2_331_000_000), r
    assert not has_amount(r, 999_000_000) or not r.rows, "Budi's 999jt is below the threshold"
    assert len(r.rows) <= 5, r


# ---------------------------------------------------------------- E3
def test_e3_typos_and_nonexistent_values(ask):
    r = ask("sales_bambang", "Sisa tagihan budi santosa dan unit a1205, plus tower C")
    assert r.status == "ok", r
    assert has_amount(r, 65_000_000), "fuzzy match to Budi Santoso expected"
    assert not has_amount(r, 1_454_000_000), "A-1205 belongs to another agent"
    assert has_text(r, "Tower C") or has_text(r, "tower c"), "should say Tower C doesn't exist"


# ---------------------------------------------------------------- E4
def test_e4_finance_jargon_aging_per_tower(ask):
    r = ask("finance_hendra",
            "Berapa collection rate bulan ini dan aging piutang di atas 90 hari per tower?")
    assert r.status in {"ok", "clarify"}, r
    if r.status == "ok":
        assert has_amount(r, 65_000_000), "Tower A aging > 90 days"
        assert has_amount(r, 150_000_000), "Tower B aging > 90 days"


# ---------------------------------------------------------------- E5
def test_e5_vague_biggest_customer_asks_back(ask):
    r = ask("finance_hendra", "Siapa customer terbesar?")
    assert r.status == "clarify", r
    assert not r.rows, r


# ---------------------------------------------------------------- E6
def test_e6_why_when_chart_gives_facts_only(ask):
    r = ask("finance_hendra",
            "Halo! Kenapa Linda Wijaya telat bayar, dan kapan dia akan lunas? Buatkan grafiknya juga.")
    assert r.status == "ok", r
    assert has_amount(r, 60_000_000), r
    assert has_amount(r, 3_600_000) or (has_amount(r, 2_700_000) and has_amount(r, 900_000)), r
    for code in ("<script", "plt.", "import matplotlib", "new Chart("):
        assert code not in r.visible_text, "no generated chart code"


# ---------------------------------------------------------------- E7
def test_e7_indonesian_number_shorthand(ask):
    r = ask("finance_hendra", "Kontrak dengan harga di atas 1,5 M dan DP lebih dari 50 jt")
    assert r.status == "ok", r
    assert has_text(r, "Siti Rahma") and has_text(r, "Christine Hartono"), r
    for wrong in ("Robert Tan", "Linda Wijaya", "Budi Santoso", "Ahmad Farhan"):
        assert wrong.lower() not in r.result_text.lower(), f"{wrong} wrongly matched: {r!r}"


# ---------------------------------------------------------------- E8
def test_e8_day_month_dates_and_last_month(ask):
    r = ask("finance_hendra", "Total pembayaran masuk dari 01/02/26 sampai akhir bulan lalu")
    assert r.status == "ok", r
    assert has_amount(r, 2_746_000_000), r
    assert not has_amount(r, 2_846_000_000), "01/02/26 parsed US-style as 2 Jan"


# ---------------------------------------------------------------- E9
def test_e9_ties_and_empty_answers(ask):
    r = ask("finance_hendra",
            "Unit 2BR mana yang paling luas, dan customer mana yang belum pernah bayar sama sekali?")
    assert r.status == "ok", r
    for unit in ("A-1205", "B-1602", "B-2202"):
        assert unit_mentioned(r, unit), f"tie member {unit} missing (LIMIT 1?): {r!r}"


# ---------------------------------------------------------------- E10
def test_e10_mixed_language_honorific(ask):
    r = ask("sales_bambang", "Bro, show me outstanding piutang Pak Budi dong, yg Installment itu")
    assert r.status == "ok", r
    assert has_amount(r, 65_000_000), r


# ---------------------------------------------------------------- E11
FINANCE_HISTORY = [
    {"role": "user", "content": "Top 3 customer dengan sisa tagihan terbesar",
     "username": "finance_hendra"},
    {"role": "assistant",
     "content": "1. Siti Rahma Rp1.454.000.000\n2. Ahmad Farhan Rp671.500.000\n"
                "3. Linda Wijaya Rp120.000.000",
     "username": "finance_hendra"},
]


def test_e11_account_switch_does_not_reuse_previous_badges_context(ask):
    r = ask("sales_bambang", "Yang nomor dua, sisa tagihannya berapa?", history=FINANCE_HISTORY)
    assert r.status == "clarify", r
    assert not has_amount(r, 671_500_000), "Finance's answer leaked across accounts"
    assert not has_text(r, "Ahmad"), r


def test_e11_naming_the_old_number_two_is_refused(ask):
    r = ask("sales_bambang", "Ahmad Farhan, sisa tagihannya berapa?", history=FINANCE_HISTORY)
    assert r.status == "refused", r
    assert not has_amount(r, 671_500_000), r
