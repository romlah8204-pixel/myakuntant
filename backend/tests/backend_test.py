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
        assert d["status"] == "Menunggu"


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

        assert avail_before >= 5, f"Not enough stock ({avail_before}) to test 5 channels for {sku_with_stock['sku']}"

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
