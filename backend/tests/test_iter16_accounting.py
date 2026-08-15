"""Iter16: cash_movements, fixed_assets CRUD + balance detail in /api/reports."""
import os
import pytest
import requests
from datetime import date, datetime, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
STAFF = {"email": "staff@liniar.id", "password": "Staff123!"}


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def staff_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=STAFF, timeout=15)
    assert r.status_code == 200, f"Staff login failed: {r.status_code} {r.text}"
    return s


# ---------------- Cash Movements ----------------

class TestCashMovements:
    def test_create_valid(self, admin_session):
        payload = {"date": "2026-01-05", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 12345, "note": "TEST_cm"}
        r = admin_session.post(f"{API}/cash-movements", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["id"] and d["account"] == "kas" and d["direction"] == "in"
        assert d["category"] == "lain_lain" and d["amount"] == 12345
        assert "created_at" in d
        # cleanup
        admin_session.delete(f"{API}/cash-movements/{d['id']}")

    @pytest.mark.parametrize("bad,field", [
        ({"date": "2026-01-05", "account": "kas", "direction": "in", "category": "invalid", "amount": 100}, "category"),
        ({"date": "2026-01-05", "account": "other", "direction": "in", "category": "lain_lain", "amount": 100}, "account"),
        ({"date": "2026-01-05", "account": "kas", "direction": "out2", "category": "lain_lain", "amount": 100}, "direction"),
        ({"date": "2026-01-05", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 0}, "amount"),
        ({"date": "bad-date", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 100}, "date"),
    ])
    def test_create_invalid(self, admin_session, bad, field):
        r = admin_session.post(f"{API}/cash-movements", json=bad)
        assert r.status_code == 422, f"[{field}] expected 422, got {r.status_code}: {r.text}"

    def test_staff_forbidden(self, staff_session):
        r = staff_session.post(f"{API}/cash-movements", json={"date": "2026-01-05", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 100})
        assert r.status_code == 403, r.status_code

    def test_no_auth_401(self):
        r = requests.post(f"{API}/cash-movements", json={"date": "2026-01-05", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 100})
        assert r.status_code == 401

    def test_list_sorted_desc(self, admin_session):
        # create 2 with different dates
        a = admin_session.post(f"{API}/cash-movements", json={"date": "2025-01-01", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 111, "note": "TEST_old"}).json()
        b = admin_session.post(f"{API}/cash-movements", json={"date": "2026-06-01", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 222, "note": "TEST_new"}).json()
        r = admin_session.get(f"{API}/cash-movements")
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 2
        dates = [x["date"] for x in rows]
        assert dates == sorted(dates, reverse=True), "cash_movements not sorted date desc"
        admin_session.delete(f"{API}/cash-movements/{a['id']}")
        admin_session.delete(f"{API}/cash-movements/{b['id']}")

    def test_delete_flow(self, admin_session, staff_session):
        cm = admin_session.post(f"{API}/cash-movements", json={"date": "2026-01-10", "account": "bank", "direction": "in", "category": "lain_lain", "amount": 500, "note": "TEST_del"}).json()
        # staff forbidden
        r_staff = staff_session.delete(f"{API}/cash-movements/{cm['id']}")
        assert r_staff.status_code == 403
        # admin success
        r_ok = admin_session.delete(f"{API}/cash-movements/{cm['id']}")
        assert r_ok.status_code == 200 and r_ok.json().get("deleted") == cm["id"]
        # not found
        r_nf = admin_session.delete(f"{API}/cash-movements/does-not-exist-{cm['id']}")
        assert r_nf.status_code == 404


# ---------------- Fixed Assets ----------------

class TestAssets:
    def test_create_and_derived(self, admin_session):
        payload = {"name": "TEST_Mesin Bordir", "category": "Mesin", "purchase_date": "2025-06-01", "purchase_cost": 15000000, "useful_life_months": 60, "salvage_value": 0}
        r = admin_session.post(f"{API}/assets", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["id"] and "derived" in d
        # verify monthly_dep = 250000
        assert d["derived"]["monthly_dep"] == 250000, f"monthly_dep={d['derived']['monthly_dep']}"
        # elapsed check for today (test env in 2026)
        today = datetime.now(timezone.utc).date()
        purchase = date(2025, 6, 1)
        expected_elapsed = min(60, (today.year - purchase.year) * 12 + (today.month - purchase.month))
        assert d["derived"]["elapsed_months"] == expected_elapsed, f"elapsed={d['derived']['elapsed_months']} exp={expected_elapsed}"
        assert d["derived"]["accumulated_dep"] == 250000 * expected_elapsed
        assert d["derived"]["book_value"] == 15000000 - (250000 * expected_elapsed)
        # cleanup
        admin_session.delete(f"{API}/assets/{d['id']}")

    @pytest.mark.parametrize("bad,field", [
        ({"name": "x", "category": "c", "purchase_date": "01-06-2025", "purchase_cost": 100, "useful_life_months": 12}, "date"),
        ({"name": "x", "category": "c", "purchase_date": "2025-06-01", "purchase_cost": 0, "useful_life_months": 12}, "cost"),
        ({"name": "x", "category": "c", "purchase_date": "2025-06-01", "purchase_cost": 100, "useful_life_months": 0}, "life"),
        ({"name": "x", "category": "c", "purchase_date": "2025-06-01", "purchase_cost": 100, "useful_life_months": 12, "salvage_value": 100}, "salvage>=cost"),
    ])
    def test_create_invalid(self, admin_session, bad, field):
        r = admin_session.post(f"{API}/assets", json=bad)
        assert r.status_code == 422, f"[{field}] expected 422 got {r.status_code}: {r.text}"

    def test_list_has_derived(self, admin_session):
        r = admin_session.get(f"{API}/assets")
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        for row in rows:
            assert "derived" in row
            for k in ("monthly_dep", "elapsed_months", "accumulated_dep", "book_value"):
                assert k in row["derived"]

    def test_delete_flow(self, admin_session, staff_session):
        a = admin_session.post(f"{API}/assets", json={"name": "TEST_ToDel", "category": "Alat", "purchase_date": "2025-01-01", "purchase_cost": 1000000, "useful_life_months": 24, "salvage_value": 0}).json()
        r_staff = staff_session.delete(f"{API}/assets/{a['id']}")
        assert r_staff.status_code == 403
        r_ok = admin_session.delete(f"{API}/assets/{a['id']}")
        assert r_ok.status_code == 200 and r_ok.json().get("deleted") == a["id"]
        r_nf = admin_session.delete(f"{API}/assets/nope-{a['id']}")
        assert r_nf.status_code == 404


# ---------------- Reports balance.detail ----------------

class TestReportsBalance:
    def test_structure_and_consistency(self, admin_session):
        r = admin_session.get(f"{API}/reports")
        assert r.status_code == 200, r.text
        rep = r.json()
        assert "balance" in rep and "detail" in rep["balance"]
        d = rep["balance"]["detail"]
        # structure
        assert set(d.keys()) >= {"assets", "liabilities", "equity"}
        assert set(d["assets"].keys()) >= {"lancar", "tetap", "total"}
        assert set(d["assets"]["lancar"].keys()) >= {"kas", "bank", "persediaan_bahan", "persediaan_barang_jadi", "total"}
        assert set(d["assets"]["tetap"].keys()) >= {"items", "total_perolehan", "total_akumulasi_penyusutan", "total_nilai_buku"}
        assert isinstance(d["assets"]["tetap"]["items"], list)
        assert set(d["liabilities"].keys()) >= {"utang_pinjaman", "total"}
        assert set(d["equity"].keys()) >= {"modal_disetor", "laba_ditahan", "total"}
        # Balance sheet identity: Assets == Liabilities + Equity
        lhs = d["assets"]["total"]
        rhs = d["liabilities"]["total"] + d["equity"]["total"]
        assert abs(lhs - rhs) < 0.01, f"Neraca tidak seimbang: A={lhs} vs L+E={rhs}"

    def test_modal_utang_from_cash_movements(self, admin_session):
        cm_list = admin_session.get(f"{API}/cash-movements").json()
        modal_masuk = sum(m["amount"] for m in cm_list if m.get("category") == "modal_masuk")
        tarik_pribadi = sum(m["amount"] for m in cm_list if m.get("category") == "tarik_pribadi")
        expected_modal = modal_masuk - tarik_pribadi
        pinjaman_in = sum(m["amount"] for m in cm_list if m.get("category") == "pinjaman_diterima")
        pinjaman_out = sum(m["amount"] for m in cm_list if m.get("category") == "bayar_cicilan_pinjaman")
        expected_utang = max(0, pinjaman_in - pinjaman_out)
        rep = admin_session.get(f"{API}/reports").json()
        d = rep["balance"]["detail"]
        assert abs(d["equity"]["modal_disetor"] - expected_modal) < 0.01, f"modal_disetor={d['equity']['modal_disetor']} exp={expected_modal}"
        assert abs(d["liabilities"]["utang_pinjaman"] - expected_utang) < 0.01, f"utang={d['liabilities']['utang_pinjaman']} exp={expected_utang}"

    def test_bank_from_cash_movements(self, admin_session):
        cm_list = admin_session.get(f"{API}/cash-movements").json()
        bank_in = sum(m["amount"] for m in cm_list if m.get("account") == "bank" and m.get("direction") == "in")
        bank_out = sum(m["amount"] for m in cm_list if m.get("account") == "bank" and m.get("direction") == "out")
        expected_bank = bank_in - bank_out
        rep = admin_session.get(f"{API}/reports").json()
        assert abs(rep["balance"]["detail"]["assets"]["lancar"]["bank"] - expected_bank) < 0.01


# ---------------- Activity log entries ----------------

class TestAuditLogs:
    def test_cash_movement_and_asset_logged(self, admin_session):
        cm = admin_session.post(f"{API}/cash-movements", json={"date": "2026-01-15", "account": "kas", "direction": "in", "category": "lain_lain", "amount": 777, "note": "TEST_log"}).json()
        a = admin_session.post(f"{API}/assets", json={"name": "TEST_LogAsset", "category": "Alat", "purchase_date": "2025-12-01", "purchase_cost": 500000, "useful_life_months": 12, "salvage_value": 0}).json()
        try:
            logs_cm = admin_session.get(f"{API}/activity-logs", params={"entity": "cash_movement", "action": "create"}).json()
            assert any(r.get("entity_id") == cm["id"] for r in logs_cm.get("rows", []))
            logs_a = admin_session.get(f"{API}/activity-logs", params={"entity": "asset", "action": "create"}).json()
            assert any(r.get("entity_id") == a["id"] for r in logs_a.get("rows", []))
        finally:
            admin_session.delete(f"{API}/cash-movements/{cm['id']}")
            admin_session.delete(f"{API}/assets/{a['id']}")


# ---------------- Regression smoke ----------------

class TestRegression:
    @pytest.mark.parametrize("endpoint", [
        "/reports", "/reports/detail?kind=revenue", "/sales-by-channel", "/purchases",
        "/sales", "/opex", "/inventory", "/activity-logs", "/backups", "/ledger",
    ])
    def test_endpoint_ok(self, admin_session, endpoint):
        r = admin_session.get(f"{API}{endpoint}")
        assert r.status_code == 200, f"{endpoint} -> {r.status_code}: {r.text[:200]}"
