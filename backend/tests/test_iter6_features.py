"""Iteration 6 backend tests:
- GET/POST /api/purchases with remaining_qty, unit_cost, status
- POST /api/production with material_lines: linked PO HPP granular
- POST /api/auth/change-password (with restoration teardown)
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@liniar.id"
ADMIN_PASSWORD = "Liniar123!"
STAFF_EMAIL = "staff@liniar.id"
STAFF_PASSWORD = "Staff123!"


def _login(email, password, retries=8):
    s = requests.Session()
    last = None
    for _ in range(retries):
        r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
        if r.status_code == 200:
            return s, r.json()
        last = r
        # Could be transient window during password-change tests on another worker
        import time as _t
        _t.sleep(0.5)
    raise AssertionError(f"login {email} failed after retries: {last.status_code} {last.text}")


@pytest.fixture(scope="module")
def admin_session():
    s, _ = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    return s


# -------- Purchases: remaining_qty, unit_cost, status --------
class TestPurchasesStorage:
    def test_post_purchase_saves_remaining_and_status(self, admin_session):
        payload = {"supplier": "TEST_Iter6_S", "material": "TEST_Fabric_A", "quantity": 200, "unit": "meter", "unit_cost": 45000}
        r = admin_session.post(f"{API}/purchases", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["quantity"] == 200
        assert d["remaining_qty"] == 200
        assert d["unit_cost"] == 45000
        assert d["status"] == "Diterima"
        assert d["total"] == 200 * 45000
        assert "id" in d and d["po"].startswith("PO-")
        assert "_id" not in d

    def test_get_purchases_lists_new_fields(self, admin_session):
        # create then verify list contains it with fields
        p = admin_session.post(f"{API}/purchases",
                               json={"supplier": "TEST_Iter6_List", "material": "TEST_M", "quantity": 50, "unit": "meter", "unit_cost": 10000},
                               timeout=15).json()
        r = admin_session.get(f"{API}/purchases", timeout=15)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) > 0
        matched = [x for x in rows if x["id"] == p["id"]]
        assert matched, "created PO not in list"
        row = matched[0]
        assert row["remaining_qty"] == 50
        assert row["unit_cost"] == 10000
        assert row["status"] == "Diterima"


# -------- Production with material_lines --------
class TestProductionLinkedPO:
    @pytest.fixture(scope="class")
    def po_a(self, admin_session):
        return admin_session.post(f"{API}/purchases",
                                  json={"supplier": "TEST_PO_A", "material": "TEST_Cotton", "quantity": 500, "unit": "meter", "unit_cost": 20000},
                                  timeout=15).json()

    @pytest.fixture(scope="class")
    def po_b(self, admin_session):
        return admin_session.post(f"{API}/purchases",
                                  json={"supplier": "TEST_PO_B", "material": "TEST_Linen", "quantity": 300, "unit": "meter", "unit_cost": 35000},
                                  timeout=15).json()

    def test_production_with_material_lines_computes_cost_and_breakdown(self, admin_session, po_a, po_b):
        # Grab remaining before
        before = admin_session.get(f"{API}/purchases", timeout=15).json()
        rem_a_before = next(x["remaining_qty"] for x in before if x["id"] == po_a["id"])
        rem_b_before = next(x["remaining_qty"] for x in before if x["id"] == po_b["id"])

        payload = {
            "sku": "TEST-ITER6-A", "product": "TEST Overshirt", "output_qty": 10,
            "material_cost": 999999,  # should be OVERRIDDEN
            "labor_cost": 100000, "overhead_cost": 50000,
            "material_lines": [
                {"purchase_id": po_a["id"], "qty_used": 5},
                {"purchase_id": po_b["id"], "qty_used": 3},
            ],
        }
        r = admin_session.post(f"{API}/production", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        expected_mc = 5 * 20000 + 3 * 35000  # 100000 + 105000 = 205000
        assert d["material_cost"] == expected_mc, f"material_cost override failed: {d['material_cost']}"
        assert d["total_cost"] == expected_mc + 100000 + 50000
        assert d["hpp"] == round(d["total_cost"] / 10, 2)
        # breakdown structure
        assert isinstance(d["material_breakdown"], list) and len(d["material_breakdown"]) == 2
        b1 = d["material_breakdown"][0]
        for k in ("purchase_id", "po", "material", "qty_used", "unit", "unit_cost", "line_cost"):
            assert k in b1, f"missing key {k}"
        assert b1["purchase_id"] == po_a["id"]
        assert b1["line_cost"] == 5 * 20000
        assert d["material_breakdown"][1]["line_cost"] == 3 * 35000
        assert "_id" not in d

        # Verify remaining_qty deducted
        after = admin_session.get(f"{API}/purchases", timeout=15).json()
        rem_a_after = next(x["remaining_qty"] for x in after if x["id"] == po_a["id"])
        rem_b_after = next(x["remaining_qty"] for x in after if x["id"] == po_b["id"])
        assert rem_a_after == rem_a_before - 5
        assert rem_b_after == rem_b_before - 3

    def test_production_rejects_qty_exceeds_remaining_409(self, admin_session, po_a):
        current = next(x for x in admin_session.get(f"{API}/purchases", timeout=15).json() if x["id"] == po_a["id"])
        excess = current["remaining_qty"] + 100
        r = admin_session.post(f"{API}/production", json={
            "sku": "TEST-X", "product": "TEST", "output_qty": 1,
            "material_cost": 0, "labor_cost": 0, "overhead_cost": 0,
            "material_lines": [{"purchase_id": po_a["id"], "qty_used": excess}],
        }, timeout=15)
        assert r.status_code == 409, r.text
        assert "sisa" in r.text.lower()

    def test_production_rejects_missing_po_404(self, admin_session):
        fake_id = str(uuid.uuid4())
        r = admin_session.post(f"{API}/production", json={
            "sku": "TEST-X", "product": "TEST", "output_qty": 1,
            "material_cost": 0, "labor_cost": 0, "overhead_cost": 0,
            "material_lines": [{"purchase_id": fake_id, "qty_used": 1}],
        }, timeout=15)
        assert r.status_code == 404, r.text

    def test_production_rejects_invalid_line_422(self, admin_session, po_a):
        # qty_used = 0
        r = admin_session.post(f"{API}/production", json={
            "sku": "TEST-X", "product": "TEST", "output_qty": 1,
            "material_cost": 0, "labor_cost": 0, "overhead_cost": 0,
            "material_lines": [{"purchase_id": po_a["id"], "qty_used": 0}],
        }, timeout=15)
        assert r.status_code == 422, r.text

        # empty purchase_id
        r2 = admin_session.post(f"{API}/production", json={
            "sku": "TEST-X", "product": "TEST", "output_qty": 1,
            "material_cost": 0, "labor_cost": 0, "overhead_cost": 0,
            "material_lines": [{"purchase_id": "", "qty_used": 5}],
        }, timeout=15)
        assert r2.status_code == 422, r2.text

    def test_production_without_material_lines_uses_manual_cost(self, admin_session):
        r = admin_session.post(f"{API}/production", json={
            "sku": "TEST-MANUAL", "product": "TEST", "output_qty": 5,
            "material_cost": 300000, "labor_cost": 100000, "overhead_cost": 50000,
        }, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["material_cost"] == 300000
        assert d["total_cost"] == 450000
        assert d["hpp"] == 90000
        assert d["material_breakdown"] == []

    def test_production_output_qty_zero_422(self, admin_session):
        r = admin_session.post(f"{API}/production", json={
            "sku": "TEST-Z", "product": "TEST", "output_qty": 0,
            "material_cost": 100, "labor_cost": 0, "overhead_cost": 0,
        }, timeout=15)
        assert r.status_code == 422, r.text

    def test_production_hpp_computation(self, admin_session):
        r = admin_session.post(f"{API}/production", json={
            "sku": "TEST-HPP", "product": "TEST", "output_qty": 4,
            "material_cost": 100000, "labor_cost": 60000, "overhead_cost": 40000,
        }, timeout=15).json()
        assert r["total_cost"] == 200000
        assert r["hpp"] == 50000


# -------- Change password --------
class TestChangePassword:
    def test_no_auth_401(self):
        r = requests.post(f"{API}/auth/change-password",
                          json={"current_password": "x", "new_password": "12345678"}, timeout=15)
        assert r.status_code == 401, r.text

    def test_wrong_current_401(self, admin_session):
        r = admin_session.post(f"{API}/auth/change-password",
                               json={"current_password": "WRONG_PASS", "new_password": "NewPass123!"}, timeout=15)
        assert r.status_code == 401, r.text
        assert "lama" in r.text.lower() or "sesuai" in r.text.lower()

    def test_short_new_password_422(self, admin_session):
        r = admin_session.post(f"{API}/auth/change-password",
                               json={"current_password": ADMIN_PASSWORD, "new_password": "short1"}, timeout=15)
        assert r.status_code == 422, r.text
        assert "8" in r.text

    def test_change_password_flow_and_restore(self):
        """Change staff password, verify old fails, new works. Restore at end.
        Use staff (not admin) because admin gets force-reset by seed on next startup.
        """
        NEW_PWD = "TempStaff456!"
        s, _ = _login(STAFF_EMAIL, STAFF_PASSWORD)
        try:
            r = s.post(f"{API}/auth/change-password",
                       json={"current_password": STAFF_PASSWORD, "new_password": NEW_PWD}, timeout=15)
            assert r.status_code == 200, r.text
            assert "message" in r.json()

            # Login with old should fail
            fail = requests.post(f"{API}/auth/login",
                                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD}, timeout=15)
            assert fail.status_code == 401, f"old password should not work: {fail.status_code}"

            # Login with new should work
            s2, u = _login(STAFF_EMAIL, NEW_PWD)
            assert u["email"] == STAFF_EMAIL
        finally:
            # Restore. Try with new pwd session; if not available, try login with new.
            try:
                s_new, _ = _login(STAFF_EMAIL, NEW_PWD)
                rst = s_new.post(f"{API}/auth/change-password",
                                 json={"current_password": NEW_PWD, "new_password": STAFF_PASSWORD}, timeout=15)
                assert rst.status_code == 200, f"restore failed: {rst.text}"
            except Exception as e:
                pytest.fail(f"Could not restore staff password: {e}")

            # Verify restored
            verify = requests.post(f"{API}/auth/login",
                                   json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD}, timeout=15)
            assert verify.status_code == 200, "staff password NOT restored to Staff123!"

    def test_admin_change_and_restore(self, admin_session):
        """Admin change password; verify then restore back to Liniar123!.
        Uses filelock to serialize with other workers that may login as admin.
        """
        import fcntl, time
        NEW_PWD = "TempAdmin789!"
        lock_path = "/tmp/iter6_admin_pwd.lock"
        lockf = open(lock_path, "w")
        # Exclusive lock across processes for the whole change→verify→restore window
        for _ in range(60):
            try:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.5)
        else:
            pytest.skip("Could not acquire admin pwd lock")
        try:
            # Re-login fresh (admin_session cookie may have gone stale)
            s, _ = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
            try:
                r = s.post(f"{API}/auth/change-password",
                           json={"current_password": ADMIN_PASSWORD, "new_password": NEW_PWD}, timeout=15)
                assert r.status_code == 200, r.text

                # Old fails
                fail = requests.post(f"{API}/auth/login",
                                     json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
                assert fail.status_code == 401

                # New works
                s2, _ = _login(ADMIN_EMAIL, NEW_PWD)
            finally:
                # Restore
                s_new, _ = _login(ADMIN_EMAIL, NEW_PWD)
                rst = s_new.post(f"{API}/auth/change-password",
                                 json={"current_password": NEW_PWD, "new_password": ADMIN_PASSWORD}, timeout=15)
                assert rst.status_code == 200, f"admin restore failed: {rst.text}"

                verify = requests.post(f"{API}/auth/login",
                                       json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
                assert verify.status_code == 200, "admin password NOT restored to Liniar123!"
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
            lockf.close()
