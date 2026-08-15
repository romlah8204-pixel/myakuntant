"""Iter22 tests: POS settings, allocate with min, low-stock-alerts, regressions."""
import os
import pytest
import requests


def _load_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("REACT_APP_BACKEND_URL", "")


BASE_URL = _load_env().rstrip("/")
assert BASE_URL
API = f"{BASE_URL}/api"

CREDS = {
    "admin": ("admin@liniar.id", "Liniar123!"),
    "kasir": ("kasir@liniar.id", "Kasir123!"),
    "keuangan": ("keuangan@liniar.id", "Keuangan123!"),
    "gudang": ("gudang@liniar.id", "Gudang123!"),
}


def _login(role):
    email, pw = CREDS[role]
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=15)
    assert r.status_code == 200, f"login {role} failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def sess():
    return {role: _login(role) for role in CREDS}


SKU = "LIN-OVR-001"


# ---------- POS Settings ----------
class TestPOSSettings:
    def test_get_returns_shape(self, sess):
        r = sess["admin"].get(f"{API}/settings/pos")
        assert r.status_code == 200
        d = r.json()
        assert "default_margin_pct" in d and "round_to" in d and "margin_by_type" in d
        assert d["round_to"] in {1, 100, 500, 1000, 5000, 10000}

    def test_kasir_can_get(self, sess):
        r = sess["kasir"].get(f"{API}/settings/pos")
        assert r.status_code == 200

    def test_put_admin_success_and_persisted(self, sess):
        r = sess["admin"].put(f"{API}/settings/pos",
                              json={"default_margin_pct": 75, "round_to": 5000, "margin_by_type": {}})
        assert r.status_code == 200, r.text
        g = sess["admin"].get(f"{API}/settings/pos").json()
        assert g["default_margin_pct"] == 75
        assert g["round_to"] == 5000

    def test_put_margin_over_500(self, sess):
        r = sess["admin"].put(f"{API}/settings/pos", json={"default_margin_pct": 999, "round_to": 1000})
        assert r.status_code == 422

    def test_put_margin_negative(self, sess):
        r = sess["admin"].put(f"{API}/settings/pos", json={"default_margin_pct": -10, "round_to": 1000})
        assert r.status_code == 422

    def test_put_invalid_round_to(self, sess):
        r = sess["admin"].put(f"{API}/settings/pos", json={"default_margin_pct": 60, "round_to": 2500})
        assert r.status_code == 422

    def test_put_forbidden_kasir(self, sess):
        r = sess["kasir"].put(f"{API}/settings/pos", json={"default_margin_pct": 60, "round_to": 1000})
        assert r.status_code == 403

    def test_put_forbidden_keuangan(self, sess):
        r = sess["keuangan"].put(f"{API}/settings/pos", json={"default_margin_pct": 60, "round_to": 1000})
        assert r.status_code == 403

    def test_put_forbidden_gudang(self, sess):
        r = sess["gudang"].put(f"{API}/settings/pos", json={"default_margin_pct": 60, "round_to": 1000})
        assert r.status_code == 403

    def test_zzz_restore_default(self, sess):
        r = sess["admin"].put(f"{API}/settings/pos",
                              json={"default_margin_pct": 60, "round_to": 1000, "margin_by_type": {}})
        assert r.status_code == 200


# ---------- Allocate with Min ----------
class TestAllocateWithMin:
    def test_dict_qty_and_min(self, sess):
        body = {"Shopee": {"qty": 5, "min": 2}, "Tokopedia": {"qty": 3, "min": 1}}
        r = sess["admin"].post(f"{API}/inventory/{SKU}/allocate", json=body)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["channel_stock"].get("Shopee") == 5
        assert d["channel_stock"].get("Tokopedia") == 3
        assert d["channel_min_stock"].get("Shopee") == 2
        assert d["channel_min_stock"].get("Tokopedia") == 1

    def test_backward_compat_integer(self, sess):
        # ensure a prior min exists
        sess["admin"].post(f"{API}/inventory/{SKU}/allocate",
                           json={"Shopee": {"qty": 5, "min": 2}})
        r = sess["admin"].post(f"{API}/inventory/{SKU}/allocate", json={"Shopee": 7})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["channel_stock"].get("Shopee") == 7
        # min preserved (still 2 from prior state)
        assert d["channel_min_stock"].get("Shopee") == 2

    def test_min_negative_rejected(self, sess):
        r = sess["admin"].post(f"{API}/inventory/{SKU}/allocate",
                               json={"Shopee": {"qty": 5, "min": -1}})
        assert r.status_code == 422

    def test_bahan_baku_rejected(self, sess):
        items = sess["admin"].get(f"{API}/inventory").json()
        bb = next((i for i in items if i.get("type") == "Bahan Baku"), None)
        if not bb:
            pytest.skip("no Bahan Baku SKU")
        r = sess["admin"].post(f"{API}/inventory/{bb['sku']}/allocate",
                               json={"Shopee": {"qty": 1, "min": 0}})
        assert r.status_code == 422


# ---------- Low Stock Alerts ----------
class TestLowStockAlerts:
    def test_shape(self, sess):
        r = sess["kasir"].get(f"{API}/inventory/low-stock-alerts")
        assert r.status_code == 200
        d = r.json()
        assert "alerts" in d and "count" in d and "critical" in d
        assert isinstance(d["alerts"], list)

    def test_warning_when_below_min(self, sess):
        r = sess["admin"].post(f"{API}/inventory/{SKU}/allocate",
                               json={"Shopee": {"qty": 1, "min": 3}})
        assert r.status_code == 200
        alerts = sess["admin"].get(f"{API}/inventory/low-stock-alerts").json()["alerts"]
        m = [a for a in alerts if a["sku"] == SKU and a["channel"] == "Shopee"]
        assert m, f"expected {SKU}/Shopee alert. Got: {alerts}"
        assert m[0]["current"] == 1
        assert m[0]["min"] == 3
        assert m[0]["severity"] == "warning"

    def test_critical_when_zero(self, sess):
        sess["admin"].post(f"{API}/inventory/{SKU}/allocate",
                           json={"Shopee": {"qty": 0, "min": 3}})
        alerts = sess["admin"].get(f"{API}/inventory/low-stock-alerts").json()["alerts"]
        m = [a for a in alerts if a["sku"] == SKU and a["channel"] == "Shopee"]
        assert m and m[0]["severity"] == "critical"

    def test_watch_when_equal(self, sess):
        sess["admin"].post(f"{API}/inventory/{SKU}/allocate",
                           json={"Shopee": {"qty": 3, "min": 3}})
        alerts = sess["admin"].get(f"{API}/inventory/low-stock-alerts").json()["alerts"]
        m = [a for a in alerts if a["sku"] == SKU and a["channel"] == "Shopee"]
        assert m and m[0]["severity"] == "watch"


# ---------- Regression ----------
class TestRegression:
    def test_reports_balance_ok(self, sess):
        r = sess["admin"].get(f"{API}/reports")
        assert r.status_code == 200
        bal = r.json().get("balance", {})
        assets = bal.get("assets", 0)
        le = bal.get("liabilities", 0) + bal.get("equity", 0)
        assert abs(assets - le) < 1, f"assets={assets} le={le}"

    def test_receivables_payables(self, sess):
        r = sess["admin"].get(f"{API}/receivables-payables")
        assert r.status_code == 200

    def test_channel_stock_visible(self, sess):
        items = sess["admin"].get(f"{API}/inventory").json()
        t = next((i for i in items if i["sku"] == SKU), None)
        assert t is not None
        assert "channel_stock" in t
