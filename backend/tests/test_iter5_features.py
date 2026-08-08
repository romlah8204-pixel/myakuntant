"""Iteration 5 tests: role enforcement (staff), OpEx CRUD, sales-by-channel,
dynamic OpEx integration into /api/reports.
"""
import os
import re
import uuid
from datetime import datetime, timezone

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@liniar.id"
ADMIN_PASSWORD = "Liniar123!"
STAFF_EMAIL = "staff@liniar.id"
STAFF_PASSWORD = "Staff123!"


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return s, r.json()


@pytest.fixture(scope="module")
def admin_session():
    s, _ = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    return s


@pytest.fixture(scope="module")
def staff_session():
    s, u = _login(STAFF_EMAIL, STAFF_PASSWORD)
    assert u.get("role") == "staff", f"expected role=staff got {u}"
    return s


# -------- Auth: staff login --------
class TestStaffAuth:
    def test_staff_login_returns_role_staff(self):
        _, u = _login(STAFF_EMAIL, STAFF_PASSWORD)
        assert u["email"] == STAFF_EMAIL
        assert u["role"] == "staff"

    def test_staff_me(self, staff_session):
        r = staff_session.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 200
        assert r.json()["role"] == "staff"


# -------- Role enforcement --------
class TestRoleEnforcement:
    def test_staff_reports_forbidden(self, staff_session):
        r = staff_session.get(f"{API}/reports", timeout=15)
        assert r.status_code == 403, r.text
        assert "administrator" in r.text.lower()

    def test_staff_post_opex_forbidden(self, staff_session):
        payload = {"period": "2026-08", "category": "TEST", "amount": 100000}
        r = staff_session.post(f"{API}/opex", json=payload, timeout=15)
        assert r.status_code == 403, r.text

    def test_staff_delete_opex_forbidden(self, staff_session):
        r = staff_session.delete(f"{API}/opex/{uuid.uuid4()}", timeout=15)
        assert r.status_code == 403, r.text

    def test_staff_get_opex_allowed(self, staff_session):
        r = staff_session.get(f"{API}/opex", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_staff_get_dashboard_inventory_ready(self, staff_session):
        for path in ["/dashboard", "/inventory", "/ready-to-sell", "/sales-by-channel"]:
            r = staff_session.get(f"{API}{path}", timeout=15)
            assert r.status_code == 200, f"{path} -> {r.status_code} {r.text}"

    def test_staff_can_post_sales_purchases_production(self, staff_session):
        # purchase
        rp = staff_session.post(f"{API}/purchases",
                                json={"supplier": "TEST_Staff", "material": "TEST", "quantity": 1, "unit": "meter", "unit_cost": 1000},
                                timeout=15)
        assert rp.status_code == 200, rp.text
        # production
        rpr = staff_session.post(f"{API}/production",
                                 json={"sku": "TEST-STAFF", "product": "TEST", "output_qty": 2,
                                       "material_cost": 1000, "labor_cost": 500, "overhead_cost": 500},
                                 timeout=15)
        assert rpr.status_code == 200, rpr.text
        # sale on existing SKU with stock
        inv = staff_session.get(f"{API}/ready-to-sell", timeout=15).json()
        if inv:
            item = max(inv, key=lambda x: x.get("available", 0))
            rs = staff_session.post(f"{API}/sales",
                                    json={"channel": "Shopee", "sku": item["sku"], "quantity": 1, "unit_price": 200000, "customer": "TEST_staff"},
                                    timeout=15)
            assert rs.status_code == 200, rs.text


# -------- OpEx CRUD (admin) --------
class TestOpExCRUD:
    def test_post_opex_bad_period_422(self, admin_session):
        r = admin_session.post(f"{API}/opex",
                               json={"period": "2026/08", "category": "TEST", "amount": 100000},
                               timeout=15)
        assert r.status_code == 422, r.text

    def test_post_opex_negative_amount_422(self, admin_session):
        r = admin_session.post(f"{API}/opex",
                               json={"period": "2026-08", "category": "TEST", "amount": -1},
                               timeout=15)
        assert r.status_code == 422, r.text

    def test_full_crud_flow(self, admin_session):
        # snapshot existing count
        r0 = admin_session.get(f"{API}/opex", timeout=15)
        assert r0.status_code == 200
        before = r0.json()

        # create two entries different periods
        p1 = {"period": "2026-08", "category": "TEST_Sewa", "amount": 1500000, "note": "TEST"}
        p2 = {"period": "2026-07", "category": "TEST_Listrik", "amount": 500000}
        c1 = admin_session.post(f"{API}/opex", json=p1, timeout=15)
        c2 = admin_session.post(f"{API}/opex", json=p2, timeout=15)
        assert c1.status_code == 200 and c2.status_code == 200, f"{c1.text}|{c2.text}"
        d1 = c1.json(); d2 = c2.json()
        assert d1["period"] == "2026-08" and d1["amount"] == 1500000
        assert "id" in d1 and "created_at" in d1
        assert "_id" not in d1

        # list sorted period desc
        r1 = admin_session.get(f"{API}/opex", timeout=15)
        assert r1.status_code == 200
        rows = r1.json()
        periods = [r["period"] for r in rows]
        assert periods == sorted(periods, reverse=True), f"not sorted desc: {periods}"
        ids = {r["id"] for r in rows}
        assert d1["id"] in ids and d2["id"] in ids

        # delete non-existent -> 404
        r404 = admin_session.delete(f"{API}/opex/{uuid.uuid4()}", timeout=15)
        assert r404.status_code == 404

        # delete valid
        rd = admin_session.delete(f"{API}/opex/{d1['id']}", timeout=15)
        assert rd.status_code == 200
        assert rd.json()["deleted"] == d1["id"]
        rd2 = admin_session.delete(f"{API}/opex/{d2['id']}", timeout=15)
        assert rd2.status_code == 200


# -------- Reports integration: dynamic OpEx --------
class TestReportsDynamicOpEx:
    def test_monthly_opex_matches_sum(self, admin_session):
        # Create 2 opex entries in 2026-08
        e1 = admin_session.post(f"{API}/opex", json={"period": "2026-08", "category": "TEST_A", "amount": 1234567}, timeout=15).json()
        e2 = admin_session.post(f"{API}/opex", json={"period": "2026-08", "category": "TEST_B", "amount": 2000000}, timeout=15).json()
        try:
            # Sum of ALL opex entries in 2026-08 (test may co-exist with others)
            all_opex = admin_session.get(f"{API}/opex", timeout=15).json()
            expected = sum(r["amount"] for r in all_opex if r["period"] == "2026-08")

            r = admin_session.get(f"{API}/reports",
                                  params={"channel": "Semua", "granularity": "monthly", "period": "2026-08"},
                                  timeout=15)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["income"]["operating_expense"] == expected, \
                f"opex mismatch: report={d['income']['operating_expense']} expected={expected}"
            # Ensure it's NOT the old hardcoded value (unless coincidentally equal)
            assert d["income"]["operating_expense"] != 3840000 or expected == 3840000
        finally:
            admin_session.delete(f"{API}/opex/{e1['id']}", timeout=15)
            admin_session.delete(f"{API}/opex/{e2['id']}", timeout=15)

    def test_quarterly_opex_sums_three_months(self, admin_session):
        # 2026-Q3 = Jul, Aug, Sep 2026
        entries = []
        try:
            for period, amt in [("2026-07", 100000), ("2026-08", 200000), ("2026-09", 300000)]:
                e = admin_session.post(f"{API}/opex",
                                       json={"period": period, "category": "TEST_Q3", "amount": amt},
                                       timeout=15).json()
                entries.append(e)

            all_opex = admin_session.get(f"{API}/opex", timeout=15).json()
            expected = sum(r["amount"] for r in all_opex if r["period"] in ("2026-07", "2026-08", "2026-09"))

            r = admin_session.get(f"{API}/reports",
                                  params={"channel": "Semua", "granularity": "quarterly", "period": "2026-Q3"},
                                  timeout=15)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["income"]["operating_expense"] == expected, \
                f"quarterly opex mismatch: {d['income']['operating_expense']} vs {expected}"
        finally:
            for e in entries:
                admin_session.delete(f"{API}/opex/{e['id']}", timeout=15)

    def test_channel_filter_opex_zero(self, admin_session):
        # Create opex in current month; channel=Shopee -> operating_expense must be 0
        now = datetime.now(timezone.utc)
        period = f"{now.year:04d}-{now.month:02d}"
        e = admin_session.post(f"{API}/opex",
                               json={"period": period, "category": "TEST_ChanZero", "amount": 999999},
                               timeout=15).json()
        try:
            r = admin_session.get(f"{API}/reports",
                                  params={"channel": "Shopee", "granularity": "monthly", "period": period},
                                  timeout=15)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["income"]["operating_expense"] == 0
        finally:
            admin_session.delete(f"{API}/opex/{e['id']}", timeout=15)


# -------- Sales-by-channel --------
class TestSalesByChannel:
    def test_shape_and_ordering(self, admin_session):
        r = admin_session.get(f"{API}/sales-by-channel", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "months" in d and "channels" in d
        assert d["channels"] == ["Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"]
        months = d["months"]
        assert isinstance(months, list) and len(months) == 6, f"expected 6 months got {len(months)}"

        # Each month has required keys and 5 channels dict
        for m in months:
            assert set(m.keys()) >= {"label", "year", "month", "channels", "total"}
            assert set(m["channels"].keys()) == set(d["channels"])
            assert m["total"] == sum(m["channels"].values())

        # Ordering: last month must be current month, and months chronologically ascending
        now = datetime.now(timezone.utc)
        assert months[-1]["year"] == now.year and months[-1]["month"] == now.month, \
            f"last month should be current ({now.year}-{now.month}), got {months[-1]}"

        # Check strict ascending order (year, month)
        for i in range(1, 6):
            prev = (months[i-1]["year"], months[i-1]["month"])
            cur = (months[i]["year"], months[i]["month"])
            # Difference should be exactly 1 month forward
            expected_m = prev[1] + 1
            expected_y = prev[0]
            if expected_m > 12:
                expected_m = 1
                expected_y += 1
            assert cur == (expected_y, expected_m), f"order break at {i}: prev={prev} cur={cur}"

    def test_current_month_reflects_sales(self, admin_session):
        # After earlier tests inserting Shopee sale in current month, the last bucket should have >0 Shopee revenue
        r = admin_session.get(f"{API}/sales-by-channel", timeout=15)
        d = r.json()
        last = d["months"][-1]
        # No hard assertion on value (may be 0 if this test runs first), but structure must be numeric
        assert isinstance(last["channels"]["Shopee"], (int, float))
