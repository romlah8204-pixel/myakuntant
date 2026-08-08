"""Backend regression tests for Liniar manufaktur fashion app.
Covers: auth, purchases, production, inventory, ready-to-sell, sales (5 channels),
reports (dynamic channel filter + channel_summary), authz.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@liniar.id"
ADMIN_PASSWORD = "Liniar123!"


@pytest.fixture(scope="session")
def auth_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("email") == ADMIN_EMAIL
    assert data.get("role") == "admin"
    # cookie set
    assert "access_token" in s.cookies.get_dict(), f"access_token cookie not set. Cookies: {s.cookies.get_dict()}"
    return s


# -------- Auth --------
class TestAuth:
    def test_login_success_sets_cookie(self, auth_session):
        r = auth_session.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_protected_without_cookie_401(self):
        r = requests.get(f"{API}/inventory", timeout=15)
        assert r.status_code == 401

    def test_reports_without_cookie_401(self):
        r = requests.get(f"{API}/reports", timeout=15)
        assert r.status_code == 401


# -------- Purchases --------
class TestPurchases:
    def test_create_purchase_computes_total(self, auth_session):
        payload = {"supplier": "TEST_Supp", "material": "TEST Fabric", "quantity": 10, "unit": "meter", "unit_cost": 50000}
        r = auth_session.post(f"{API}/purchases", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total"] == 10 * 50000
        assert d["po"].startswith("PO-")
        assert d["status"] == "Diterima"


# -------- Production --------
class TestProduction:
    def test_create_production_computes_hpp(self, auth_session):
        payload = {"sku": "TEST-SKU-01", "product": "TEST Product", "output_qty": 10,
                   "material_cost": 400000, "labor_cost": 200000, "overhead_cost": 100000}
        r = auth_session.post(f"{API}/production", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total_cost"] == 700000
        assert d["hpp"] == 70000.0
        assert d["batch"].startswith("BTH-")


# -------- Inventory & Ready-to-sell --------
class TestInventory:
    def test_inventory_list(self, auth_session):
        r = auth_session.get(f"{API}/inventory", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) >= 1
        assert all("_id" not in it for it in data)

    def test_ready_to_sell_fields(self, auth_session):
        r = auth_session.get(f"{API}/ready-to-sell", timeout=15)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list) and len(items) >= 1
        for it in items:
            assert it["type"] == "Barang Jadi"
            assert it["available"] > 0
            assert "ready_qty" in it and it["ready_qty"] == it["available"]
            assert it["sell_status"] in {"Siap dijual", "Stok terbatas"}


# -------- Sales (5 channels) --------
VALID_CHANNELS = ["Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"]


class TestSales:
    @pytest.fixture(scope="class")
    def sku_with_stock(self, auth_session):
        r = auth_session.get(f"{API}/ready-to-sell", timeout=15)
        assert r.status_code == 200
        items = r.json()
        # Pick item with the most available
        item = max(items, key=lambda x: x.get("available", 0))
        return item

    def test_sale_all_valid_channels(self, auth_session, sku_with_stock):
        # Before qty
        r0 = auth_session.get(f"{API}/inventory", timeout=15)
        before = next(x for x in r0.json() if x["sku"] == sku_with_stock["sku"])
        avail_before = before["available"]
        stock_before = before["stock"]

        if avail_before < 5:
            pytest.skip(f"Not enough stock ({avail_before}) to test 5 channels for {sku_with_stock['sku']} — inventory depleted across test runs")

        for ch in VALID_CHANNELS:
            payload = {"channel": ch, "sku": sku_with_stock["sku"], "quantity": 1, "unit_price": 250000, "customer": "TEST_cust", "order_ref": f"TEST-{ch}"}
            r = auth_session.post(f"{API}/sales", json=payload, timeout=15)
            assert r.status_code == 200, f"channel {ch} failed: {r.status_code} {r.text}"
            d = r.json()
            assert d["channel"] == ch
            assert d["revenue"] == 250000
            assert d["gross_profit"] == round(d["revenue"] - d["cogs"], 2)
            assert d["invoice"].startswith("INV-")

        # Verify stock decreased by 5
        r1 = auth_session.get(f"{API}/inventory", timeout=15)
        after = next(x for x in r1.json() if x["sku"] == sku_with_stock["sku"])
        assert after["available"] == avail_before - 5
        assert after["stock"] == stock_before - 5

    def test_sale_invalid_channel_marketplace_422(self, auth_session, sku_with_stock):
        payload = {"channel": "Marketplace", "sku": sku_with_stock["sku"], "quantity": 1, "unit_price": 100000}
        r = auth_session.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"

    def test_sale_over_available_409(self, auth_session, sku_with_stock):
        r0 = auth_session.get(f"{API}/inventory", timeout=15)
        cur = next(x for x in r0.json() if x["sku"] == sku_with_stock["sku"])
        payload = {"channel": "Shopee", "sku": sku_with_stock["sku"], "quantity": cur["available"] + 100, "unit_price": 100000}
        r = auth_session.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text}"


# -------- Reports --------
class TestReports:
    def test_reports_semua_has_channel_summary(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"channel": "Semua"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["channel"] == "Semua"
        assert set(d["channel_summary"].keys()) == set(VALID_CHANNELS)
        # After running TestSales, each should have >=1 count
        for ch in VALID_CHANNELS:
            assert d["channel_summary"][ch]["count"] >= 1, f"{ch} count 0 in summary: {d['channel_summary']}"
        # revenue = sum of channel revenues (only from these 5 channels part of query, but sum includes legacy too)
        assert d["income"]["revenue"] >= sum(v["revenue"] for v in d["channel_summary"].values())

    def test_reports_shopee_filter(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"channel": "Shopee"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["channel"] == "Shopee"
        assert d["channel_summary"] == {}
        assert d["transaction_count"] >= 1
        assert d["income"]["operating_expense"] == 0

    def test_reports_invalid_channel_422(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"channel": "Marketplace"}, timeout=15)
        assert r.status_code == 422


# -------- Reports: period filter + previous period comparison --------
class TestReportsPeriodFilter:
    def test_granularity_all_no_previous(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "all"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["period"] == "Semua Periode"
        assert d["previous_period"] is None
        assert d["previous"] is None
        assert d["deltas"] is None
        assert d["granularity"] == "all"

    def test_granularity_monthly_2026_08(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "monthly", "period": "2026-08"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["period"] == "Agu 2026"
        assert d["previous_period"] == "Jul 2026"
        assert d["previous"] is not None
        prev_keys = {"revenue", "cogs", "gross_profit", "operating_expense", "net_profit",
                     "cash_in", "cash_out", "cash_net", "transaction_count"}
        assert prev_keys.issubset(set(d["previous"].keys())), f"missing keys: {prev_keys - set(d['previous'].keys())}"
        assert d["deltas"] is not None
        assert set(d["deltas"].keys()) == {"revenue_pct", "net_profit_pct", "cash_net_pct"}

    def test_granularity_quarterly_2026_q3(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "quarterly", "period": "2026-Q3"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["period"] == "Q3 2026"
        assert d["previous_period"] == "Q2 2026"
        assert d["previous"] is not None
        assert d["deltas"] is not None
        assert set(d["deltas"].keys()) == {"revenue_pct", "net_profit_pct", "cash_net_pct"}

    def test_quarterly_q1_rollback_year(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "quarterly", "period": "2026-Q1"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["period"] == "Q1 2026"
        assert d["previous_period"] == "Q4 2025"

    def test_monthly_january_rollback_year(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "monthly", "period": "2026-01"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["period"] == "Jan 2026"
        assert d["previous_period"] == "Des 2025"

    def test_monthly_december_rollforward_year(self, auth_session):
        # December should not raise, end range should roll into next year Jan 1
        r = auth_session.get(f"{API}/reports", params={"granularity": "monthly", "period": "2026-12"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["period"] == "Des 2026"
        assert d["previous_period"] == "Nov 2026"

    def test_invalid_granularity_422(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "weekly"}, timeout=15)
        assert r.status_code == 422
        assert "Granularity tidak valid" in r.text

    def test_monthly_without_period_422(self, auth_session):
        r = auth_session.get(f"{API}/reports", params={"granularity": "monthly"}, timeout=15)
        assert r.status_code == 422
        assert "Period wajib diisi" in r.text

    def test_channel_shopee_plus_period_filter(self, auth_session):
        # Verify report filtered by channel=Shopee + monthly period returns correct label,
        # channel echo, empty channel_summary, and operating_expense==0.
        # (TestSales creates Shopee sales in current month, so transaction_count>=1.)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        period_str = f"{now.year:04d}-{now.month:02d}"
        id_month = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
        expected_label = f"{id_month[now.month-1]} {now.year}"

        r = auth_session.get(f"{API}/reports",
                             params={"channel": "Shopee", "granularity": "monthly", "period": period_str},
                             timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["period"] == expected_label
        assert d["channel"] == "Shopee"
        assert d["granularity"] == "monthly"
        # operating_expense=0 when channel != Semua
        assert d["income"]["operating_expense"] == 0
        # Channel summary empty when not Semua
        assert d["channel_summary"] == {}
        # Should include previous-period comparison payload
        assert d["previous"] is not None
        assert d["deltas"] is not None

    def test_purchase_and_production_created_at_included_in_period(self, auth_session):
        # A purchase created now should be counted in cash_out of current-month report (channel=Semua).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        period_str = f"{now.year:04d}-{now.month:02d}"

        r_before = auth_session.get(f"{API}/reports",
                                    params={"channel": "Semua", "granularity": "monthly", "period": period_str},
                                    timeout=15)
        assert r_before.status_code == 200
        base_out = r_before.json()["cash"]["out"]

        purchase = {"supplier": "TEST_Supp_Period", "material": "TEST", "quantity": 2, "unit": "meter", "unit_cost": 111111}
        rp = auth_session.post(f"{API}/purchases", json=purchase, timeout=15)
        assert rp.status_code == 200
        purchase_total = rp.json()["total"]
        assert purchase_total == 222222

        production = {"sku": "TEST-PROD-PERIOD", "product": "TEST", "output_qty": 5,
                      "material_cost": 100000, "labor_cost": 50000, "overhead_cost": 25000}
        rpr = auth_session.post(f"{API}/production", json=production, timeout=15)
        assert rpr.status_code == 200
        production_total = rpr.json()["total_cost"]
        assert production_total == 175000

        r_after = auth_session.get(f"{API}/reports",
                                   params={"channel": "Semua", "granularity": "monthly", "period": period_str},
                                   timeout=15)
        assert r_after.status_code == 200
        after_out = r_after.json()["cash"]["out"]
        # Use >= to be robust against parallel tests (pytest-xdist) also creating purchases/production.
        assert after_out >= base_out + purchase_total + production_total, (
            f"cash.out did not include newly-created purchase+production (created_at not filtered in): "
            f"before={base_out} after={after_out} min_expected_delta={purchase_total + production_total}"
        )
