"""Iteration 7 backend tests: audit log activity trail + GET /api/activity-logs."""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@liniar.id"
ADMIN_PASSWORD = "Liniar123!"
STAFF_EMAIL = "staff@liniar.id"
STAFF_PASSWORD = "Staff123!"


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def staff():
    return _login(STAFF_EMAIL, STAFF_PASSWORD)


def _get_logs(session, **params):
    r = session.get(f"{API}/activity-logs", params=params, timeout=20)
    return r


# ---------- Auth / access control ----------
class TestActivityLogAccess:
    def test_admin_can_list(self, admin):
        r = _get_logs(admin)
        assert r.status_code == 200
        data = r.json()
        for key in ("total", "limit", "offset", "rows"):
            assert key in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["total"], int)

    def test_staff_forbidden(self, staff):
        r = _get_logs(staff)
        assert r.status_code == 403

    def test_no_auth_unauthorized(self):
        r = requests.get(f"{API}/activity-logs", timeout=20)
        assert r.status_code == 401


# ---------- Login instrumentation ----------
class TestLoginAudit:
    def test_login_success_creates_log(self, admin):
        # fresh login to guarantee a new entry
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
        assert r.status_code == 200
        time.sleep(0.5)
        r2 = _get_logs(admin, action="login", user_email=ADMIN_EMAIL, limit=5)
        assert r2.status_code == 200
        rows = r2.json()["rows"]
        assert rows, "expected at least one login log"
        top = rows[0]
        assert top["action"] == "login"
        assert top["entity"] == "auth"
        assert ADMIN_EMAIL in top["summary"]

    def test_failed_login_not_logged(self, admin):
        before = _get_logs(admin, action="login").json()["total"]
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json={"email": "nobody@liniar.id", "password": "wrong"}, timeout=20)
        assert r.status_code == 401
        after = _get_logs(admin, action="login").json()["total"]
        assert after == before, "failed login should not add audit entry"


# ---------- Purchase / production / sale / opex instrumentation ----------
class TestBusinessInstrumentation:
    def test_purchase_logged(self, admin):
        payload = {"supplier": "TEST_SupplierAudit", "material": "Kain Test", "quantity": 20, "unit": "meter", "unit_cost": 50000}
        r = admin.post(f"{API}/purchases", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        pdoc = r.json()
        time.sleep(0.3)
        r2 = _get_logs(admin, entity="purchase", limit=5)
        rows = r2.json()["rows"]
        # find the log for this purchase.id
        match = next((x for x in rows if x["entity_id"] == pdoc["id"]), None)
        assert match is not None, f"no purchase log for {pdoc['id']}"
        assert match["action"] == "create"
        assert pdoc["po"] in match["summary"]
        assert "TEST_SupplierAudit" in match["summary"]
        assert "Kain Test" in match["summary"]
        # total = 20 * 50000 = 1,000,000 -> 'Rp 1.000.000'
        assert "Rp 1.000.000" in match["summary"]
        # linked purchase id available for later production test
        pytest.purchase_for_prod = pdoc

    def test_production_logged(self, admin):
        po = getattr(pytest, "purchase_for_prod", None)
        if not po:
            pytest.skip("purchase fixture missing")
        payload = {
            "sku": "LIN-OVR-001",
            "product": "Overshirt Test",
            "output_qty": 10,
            "material_cost": 0,
            "labor_cost": 100000,
            "overhead_cost": 50000,
            "material_lines": [{"purchase_id": po["id"], "qty_used": 5}],
        }
        r = admin.post(f"{API}/production", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        prod = r.json()
        time.sleep(0.3)
        r2 = _get_logs(admin, entity="production", limit=5)
        rows = r2.json()["rows"]
        match = next((x for x in rows if x["entity_id"] == prod["id"]), None)
        assert match is not None, "no production log"
        assert match["action"] == "create"
        d = match["details"]
        assert d["sku"] == "LIN-OVR-001"
        assert d["output_qty"] == 10
        assert d["linked_lines"] == 1
        assert d["hpp"] == prod["hpp"]

    def test_sale_logged(self, admin):
        payload = {"channel": "Shopee", "sku": "LIN-OVR-001", "quantity": 1, "unit_price": 250000, "customer": "TEST_AuditCust"}
        r = admin.post(f"{API}/sales", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        sale = r.json()
        time.sleep(0.3)
        r2 = _get_logs(admin, entity="sale", limit=5)
        rows = r2.json()["rows"]
        match = next((x for x in rows if x["entity_id"] == sale["id"]), None)
        assert match is not None
        s = match["summary"]
        assert "Shopee" in s and "LIN-OVR-001" in s
        d = match["details"]
        assert d["channel"] == "Shopee"
        assert d["quantity"] == 1
        assert d["revenue"] == 250000

    def test_opex_create_and_delete_logged(self, admin):
        payload = {"period": "2026-01", "category": "TEST_AuditOpex", "amount": 12345, "note": "audit"}
        r = admin.post(f"{API}/opex", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        opex = r.json()
        time.sleep(0.3)
        logs = _get_logs(admin, entity="opex", action="create", limit=10).json()["rows"]
        create_log = next((x for x in logs if x["entity_id"] == opex["id"]), None)
        assert create_log is not None
        assert "TEST_AuditOpex" in create_log["summary"]

        # DELETE
        rd = admin.delete(f"{API}/opex/{opex['id']}", timeout=20)
        assert rd.status_code == 200
        time.sleep(0.3)
        del_logs = _get_logs(admin, entity="opex", action="delete", limit=10).json()["rows"]
        del_log = next((x for x in del_logs if x["entity_id"] == opex["id"]), None)
        assert del_log is not None
        assert "dihapus" in del_log["summary"].lower()

    def test_change_password_logged(self, admin):
        # change to same password (should still log)
        r = admin.post(f"{API}/auth/change-password", json={"current_password": ADMIN_PASSWORD, "new_password": ADMIN_PASSWORD}, timeout=20)
        assert r.status_code == 200, r.text
        time.sleep(0.3)
        rows = _get_logs(admin, action="change_password", limit=5).json()["rows"]
        assert rows, "no change_password log"
        top = rows[0]
        assert top["entity"] == "user"
        assert ADMIN_EMAIL in top["summary"]


# ---------- Filters & pagination ----------
class TestFiltersPagination:
    def test_filter_action_create(self, admin):
        rows = _get_logs(admin, action="create", limit=50).json()["rows"]
        assert rows, "expected some create logs"
        assert all(x["action"] == "create" for x in rows)

    def test_filter_entity_opex(self, admin):
        rows = _get_logs(admin, entity="opex", limit=50).json()["rows"]
        assert all(x["entity"] == "opex" for x in rows)

    def test_filter_user_email(self, admin):
        rows = _get_logs(admin, user_email=ADMIN_EMAIL, limit=50).json()["rows"]
        assert rows
        assert all(x["user_email"] == ADMIN_EMAIL for x in rows)

    def test_pagination_and_ordering(self, admin):
        page1 = _get_logs(admin, limit=5, offset=0).json()
        page2 = _get_logs(admin, limit=5, offset=5).json()
        assert len(page1["rows"]) == 5
        # ordering descending on created_at
        created = [r["created_at"] for r in page1["rows"]]
        assert created == sorted(created, reverse=True)
        # no overlap between pages
        ids1 = {r["id"] for r in page1["rows"]}
        ids2 = {r["id"] for r in page2["rows"]}
        assert ids1.isdisjoint(ids2)


# ---------- Regression: iter 5 & 6 endpoints still work ----------
class TestRegression:
    def test_reports_monthly_ok(self, admin):
        r = admin.get(f"{API}/reports", params={"granularity": "monthly", "period": "2026-01", "channel": "Semua"}, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d["granularity"] == "monthly"
        assert "income" in d and "deltas" in d

    def test_reports_all(self, admin):
        r = admin.get(f"{API}/reports", params={"granularity": "all", "channel": "Semua"}, timeout=20)
        assert r.status_code == 200

    def test_opex_list(self, admin):
        r = admin.get(f"{API}/opex", timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_staff_cannot_reports(self, staff):
        r = staff.get(f"{API}/reports", timeout=20)
        assert r.status_code == 403
