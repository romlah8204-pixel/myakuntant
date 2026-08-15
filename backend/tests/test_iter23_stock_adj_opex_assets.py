"""Iter23: Stock adjustment/opname, Kartu Stock XLSX export, OpEx breakdown, Asset useful_life_months=0."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
SKU = "LIN-OVR-001"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def initial_stock(admin_session):
    r = admin_session.get(f"{API}/inventory", timeout=15)
    assert r.status_code == 200
    items = r.json()
    it = next((x for x in items if x.get("sku") == SKU), None)
    assert it, f"SKU {SKU} not in inventory"
    return int(it.get("stock", 0))


# ---------- Stock Adjustment ----------
class TestStockAdjust:
    def test_adjust_delta_plus_5(self, admin_session, initial_stock):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "adjust", "delta_qty": 5, "reason": "koreksi", "note": "iter23 +5"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert d["delta"] == 5
        assert d["new_stock"] == initial_stock + 5
        assert d["adjustment"]["reason"] == "koreksi"
        # Verify persistence via inventory
        inv = admin_session.get(f"{API}/inventory").json()
        cur = next(x for x in inv if x["sku"] == SKU)
        assert int(cur["stock"]) == initial_stock + 5

    def test_adjust_delta_zero_422(self, admin_session):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "adjust", "delta_qty": 0, "reason": "koreksi"})
        assert r.status_code == 422

    def test_adjust_negative_overflow_409(self, admin_session):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "adjust", "delta_qty": -99999, "reason": "koreksi"})
        assert r.status_code == 409

    def test_opname_set_10(self, admin_session):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "opname", "physical_qty": 10, "reason": "opname", "note": "iter23 opname"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["new_stock"] == 10
        assert d["adjustment"]["kind"] == "opname"
        assert d["adjustment"]["physical_qty"] == 10

    def test_opname_negative_422(self, admin_session):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "opname", "physical_qty": -1, "reason": "opname"})
        assert r.status_code == 422

    def test_invalid_kind_422(self, admin_session):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "invalid", "delta_qty": 1})
        assert r.status_code == 422

    def test_unknown_sku_404(self, admin_session):
        r = admin_session.post(f"{API}/inventory/NONEXISTENT-SKU/adjust",
                               json={"kind": "adjust", "delta_qty": 1, "reason": "koreksi"})
        assert r.status_code == 404

    def test_unknown_reason_maps_to_lainnya(self, admin_session):
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "adjust", "delta_qty": 1, "reason": "reason_yg_aneh"})
        assert r.status_code == 200
        assert r.json()["adjustment"]["reason"] == "lainnya"

    def test_list_adjustments(self, admin_session):
        r = admin_session.get(f"{API}/inventory/{SKU}/adjustments")
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and d["count"] >= 4
        # Should not include _id
        for it in d["items"]:
            assert "_id" not in it
            assert it["sku"] == SKU

    def test_restore_stock(self, admin_session, initial_stock):
        # Restore SKU to initial stock via opname
        r = admin_session.post(f"{API}/inventory/{SKU}/adjust",
                               json={"kind": "opname", "physical_qty": initial_stock, "reason": "opname", "note": "iter23 restore"})
        assert r.status_code == 200
        assert r.json()["new_stock"] == initial_stock


# ---------- Excel Export ----------
class TestStockCardExcel:
    def _assert_xlsx(self, r):
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert "attachment" in r.headers.get("content-disposition", "").lower()
        # xlsx magic = PK\x03\x04
        assert r.content[:2] == b"PK"
        assert len(r.content) > 500

    def test_export_all_skus(self, admin_session):
        r = admin_session.get(f"{API}/inventory/stock-card.xlsx")
        self._assert_xlsx(r)

    def test_export_single_sku(self, admin_session):
        r = admin_session.get(f"{API}/inventory/stock-card.xlsx", params={"sku": SKU})
        self._assert_xlsx(r)

    def test_export_nonexistent_sku_404(self, admin_session):
        r = admin_session.get(f"{API}/inventory/stock-card.xlsx", params={"sku": "NONEXISTENT-SKU"})
        assert r.status_code == 404


# ---------- Reports: OpEx breakdown ----------
class TestOpexBreakdown:
    def test_reports_has_opex_breakdown(self, admin_session):
        r = admin_session.get(f"{API}/reports")
        assert r.status_code == 200
        rep = r.json()
        assert "income" in rep
        opex = rep["income"].get("opex_breakdown")
        assert isinstance(opex, list), f"opex_breakdown missing/not list: {opex}"
        # Structure check
        for row in opex:
            assert "category" in row and "amount" in row
            assert isinstance(row["amount"], (int, float))
        # Sorted descending by amount
        amounts = [row["amount"] for row in opex]
        assert amounts == sorted(amounts, reverse=True), f"Not sorted desc: {amounts}"

    def test_reports_balance_still_balanced(self, admin_session):
        r = admin_session.get(f"{API}/reports")
        assert r.status_code == 200
        bal = r.json()["balance"]
        # equity = assets - liabilities (identity)
        assert abs((bal["assets"] - bal["liabilities"]) - bal["equity"]) < 1.0


# ---------- Assets useful_life_months=0 ----------
class TestAssetsNoDepreciation:
    created_id = None

    def test_create_asset_life_0(self, admin_session):
        payload = {
            "name": "TEST_Deposit Sewa iter23",
            "category": "Deposit",
            "purchase_date": "2024-01-15",
            "purchase_cost": 5000000,
            "useful_life_months": 0,
            "salvage_value": 0,
        }
        r = admin_session.post(f"{API}/assets", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["useful_life_months"] == 0
        assert "derived" in d
        assert d["derived"]["book_value"] == payload["purchase_cost"]
        assert d["derived"].get("monthly_dep", 0) == 0
        TestAssetsNoDepreciation.created_id = d["id"]

    def test_create_asset_life_negative_422(self, admin_session):
        payload = {
            "name": "TEST_Invalid",
            "category": "Test",
            "purchase_date": "2024-01-15",
            "purchase_cost": 1000000,
            "useful_life_months": -1,
        }
        r = admin_session.post(f"{API}/assets", json=payload)
        assert r.status_code == 422

    def test_cleanup(self, admin_session):
        if TestAssetsNoDepreciation.created_id:
            r = admin_session.delete(f"{API}/assets/{TestAssetsNoDepreciation.created_id}")
            assert r.status_code in (200, 204)


# ---------- Regression ----------
class TestRegression:
    def test_receivables_payables(self, admin_session):
        r = admin_session.get(f"{API}/receivables-payables")
        assert r.status_code == 200

    def test_inventory_ok(self, admin_session):
        r = admin_session.get(f"{API}/inventory")
        assert r.status_code == 200
        assert len(r.json()) > 0

    def test_inventory_history(self, admin_session):
        r = admin_session.get(f"{API}/inventory/{SKU}/history")
        assert r.status_code == 200
