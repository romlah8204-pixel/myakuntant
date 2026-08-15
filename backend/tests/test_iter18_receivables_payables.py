"""Iteration 18: Receivables & Payables (Piutang & Utang) tests.
Run with: pytest tests/test_iter18_receivables_payables.py -o addopts=""
(xdist splits classes across workers; class-level shared IDs require serial run OR use --dist loadfile)
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Read frontend .env directly if not exported
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login("admin@liniar.id", "Liniar123!")


@pytest.fixture(scope="module")
def staff():
    return _login("staff@liniar.id", "Staff123!")


@pytest.fixture(scope="module")
def anon():
    return requests.Session()


# --- Auth guard ---
class TestAuthGuard:
    def test_list_requires_auth(self, anon):
        r = anon.get(f"{API}/receivables-payables")
        assert r.status_code == 401

    def test_list_forbidden_for_staff(self, staff):
        r = staff.get(f"{API}/receivables-payables")
        assert r.status_code == 403

    def test_create_forbidden_for_staff(self, staff):
        r = staff.post(f"{API}/receivables-payables", json={"type": "piutang_usaha", "date": "2026-01-05", "counterparty": "X", "amount": 1000})
        assert r.status_code == 403


# --- CRUD & Validation ---
class TestRPCrud:
    created_ids = {}

    @pytest.mark.parametrize("rp_type,label", [
        ("piutang_usaha", "Piutang Usaha"),
        ("piutang_lainnya", "Piutang Lainnya"),
        ("utang_usaha", "Utang Usaha"),
        ("utang_lainnya", "Utang Lainnya"),
    ])
    def test_create_valid(self, admin, rp_type, label):
        payload = {"type": rp_type, "date": "2026-01-10", "counterparty": f"TEST_{rp_type}", "amount": 1000000, "due_date": "2026-02-10", "reference": f"REF-{rp_type}", "note": "test"}
        r = admin.post(f"{API}/receivables-payables", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["type"] == rp_type
        assert d["label"] == label
        assert d["status"] == "outstanding"
        assert d["paid_total"] == 0
        assert d["remaining"] == 1000000
        TestRPCrud.created_ids[rp_type] = d["id"]

    def test_create_invalid_type(self, admin):
        r = admin.post(f"{API}/receivables-payables", json={"type": "hutang", "date": "2026-01-10", "counterparty": "X", "amount": 1000})
        assert r.status_code == 422

    def test_create_negative_amount(self, admin):
        r = admin.post(f"{API}/receivables-payables", json={"type": "piutang_usaha", "date": "2026-01-10", "counterparty": "X", "amount": -100})
        assert r.status_code == 422

    def test_create_zero_amount(self, admin):
        r = admin.post(f"{API}/receivables-payables", json={"type": "piutang_usaha", "date": "2026-01-10", "counterparty": "X", "amount": 0})
        assert r.status_code == 422

    def test_create_empty_counterparty(self, admin):
        r = admin.post(f"{API}/receivables-payables", json={"type": "piutang_usaha", "date": "2026-01-10", "counterparty": "   ", "amount": 100})
        assert r.status_code == 422

    def test_create_bad_date(self, admin):
        r = admin.post(f"{API}/receivables-payables", json={"type": "piutang_usaha", "date": "10-01-2026", "counterparty": "X", "amount": 100})
        assert r.status_code == 422

    def test_create_bad_due_date(self, admin):
        r = admin.post(f"{API}/receivables-payables", json={"type": "piutang_usaha", "date": "2026-01-10", "counterparty": "X", "amount": 100, "due_date": "next month"})
        assert r.status_code == 422

    def test_filter_by_type(self, admin):
        r = admin.get(f"{API}/receivables-payables?type=piutang_usaha")
        assert r.status_code == 200
        data = r.json()
        for item in data["items"]:
            assert item["type"] == "piutang_usaha"

    def test_filter_by_status_outstanding(self, admin):
        r = admin.get(f"{API}/receivables-payables?status=outstanding")
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["status"] == "outstanding"

    def test_filter_invalid_type(self, admin):
        r = admin.get(f"{API}/receivables-payables?type=bogus")
        assert r.status_code == 422


# --- Payments ---
class TestRPPayments:
    def test_payment_not_found(self, admin):
        r = admin.post(f"{API}/receivables-payables/nonexistent/payments", json={"date": "2026-01-15", "amount": 100, "account": "bank"})
        assert r.status_code == 404

    def test_partial_payment_updates_status_and_cash(self, admin):
        rp_id = TestRPCrud.created_ids["piutang_usaha"]
        # cash movements before
        cm_before = admin.get(f"{API}/cash-movements").json()
        n_before = len(cm_before)
        r = admin.post(f"{API}/receivables-payables/{rp_id}/payments", json={"date": "2026-01-15", "amount": 400000, "account": "bank", "note": "cicilan1"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["paid_total"] == 400000
        assert d["remaining"] == 600000
        assert d["status"] == "partial"
        # Verify cash_movement mirrored
        cm_after = admin.get(f"{API}/cash-movements").json()
        assert len(cm_after) == n_before + 1
        # Find the new cash movement
        new_cm = [c for c in cm_after if c.get("linked_rp_id") == rp_id]
        assert len(new_cm) >= 1
        assert new_cm[0]["direction"] == "in"
        assert new_cm[0]["category"] == "pelunasan_piutang"
        assert new_cm[0]["account"] == "bank"

    def test_payment_exceeds_remaining(self, admin):
        rp_id = TestRPCrud.created_ids["piutang_usaha"]
        r = admin.post(f"{API}/receivables-payables/{rp_id}/payments", json={"date": "2026-01-16", "amount": 999999999, "account": "bank"})
        assert r.status_code == 409

    def test_full_payment_status_lunas(self, admin):
        rp_id = TestRPCrud.created_ids["piutang_lainnya"]
        r = admin.post(f"{API}/receivables-payables/{rp_id}/payments", json={"date": "2026-01-16", "amount": 1000000, "account": "kas"})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "lunas"
        assert d["remaining"] == 0

    def test_utang_payment_direction_out(self, admin):
        rp_id = TestRPCrud.created_ids["utang_usaha"]
        r = admin.post(f"{API}/receivables-payables/{rp_id}/payments", json={"date": "2026-01-16", "amount": 500000, "account": "bank"})
        assert r.status_code == 200
        cms = admin.get(f"{API}/cash-movements").json()
        matching = [c for c in cms if c.get("linked_rp_id") == rp_id]
        assert matching[0]["direction"] == "out"
        assert matching[0]["category"] == "pelunasan_utang"


# --- Delete ---
class TestRPDelete:
    def test_delete_with_payment_conflicts(self, admin):
        rp_id = TestRPCrud.created_ids["piutang_usaha"]  # has a payment
        r = admin.delete(f"{API}/receivables-payables/{rp_id}")
        assert r.status_code == 409

    def test_delete_no_payment_succeeds(self, admin):
        # utang_lainnya has no payment
        rp_id = TestRPCrud.created_ids["utang_lainnya"]
        r = admin.delete(f"{API}/receivables-payables/{rp_id}")
        assert r.status_code == 200


# --- Balance detail + Reports integration ---
class TestBalanceIntegration:
    def test_balance_detail_kinds(self, admin):
        for k in ("piutang_usaha", "piutang_lainnya", "utang_usaha", "utang_lainnya"):
            r = admin.get(f"{API}/balance-detail?kind={k}")
            assert r.status_code == 200, f"{k} → {r.status_code}"
            data = r.json()
            assert data["kind"] == k
            assert "rows" in data
            assert "saldo" in data

    def test_reports_balance_extended(self, admin):
        r = admin.get(f"{API}/reports")
        assert r.status_code == 200
        rep = r.json()
        detail = rep["balance"]["detail"]
        lancar = detail["assets"]["lancar"]
        for f in ("piutang_usaha", "piutang_lainnya", "piutang_total"):
            assert f in lancar
        jp = detail["liabilities"]["jangka_pendek"]
        for f in ("utang_usaha", "utang_lainnya", "total"):
            assert f in jp
        # Neraca seimbang: A = L + E
        assets = rep["balance"]["assets"]
        liab = rep["balance"]["liabilities"]
        eq = rep["balance"]["equity"]
        assert abs(assets - (liab + eq)) < 1

    def test_saldo_matches_remaining(self, admin):
        # For piutang_usaha: sum of saldo across all rows in listing should equal balance-detail saldo
        rp_list = admin.get(f"{API}/receivables-payables?type=piutang_usaha").json()
        total_remaining = rp_list["total_remaining"]
        bd = admin.get(f"{API}/balance-detail?kind=piutang_usaha").json()
        assert abs(bd["saldo"] - total_remaining) < 1, f"balance-detail saldo {bd['saldo']} vs total_remaining {total_remaining}"


# --- Cleanup ---
def test_zzz_cleanup(admin=None):
    # Clean TEST_ records without payments; leave those with payments (deletion blocked by design)
    if admin is None:
        admin = _login("admin@liniar.id", "Liniar123!")
    all_rp = admin.get(f"{API}/receivables-payables").json().get("items", [])
    for it in all_rp:
        if str(it.get("counterparty", "")).startswith("TEST_") and it.get("paid_total", 0) == 0:
            admin.delete(f"{API}/receivables-payables/{it['id']}")
