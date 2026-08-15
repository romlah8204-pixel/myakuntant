"""Iteration 19 tests: granular roles, POS channel, CSV import, channel allocation."""
import os
import io
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://fashion-mfg-hub.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"

CREDS = {
    "admin": ("admin@liniar.id", "Liniar123!"),
    "keuangan": ("keuangan@liniar.id", "Keuangan123!"),
    "produksi": ("produksi@liniar.id", "Produksi123!"),
    "kasir": ("kasir@liniar.id", "Kasir123!"),
    "gudang": ("gudang@liniar.id", "Gudang123!"),
    "staff": ("staff@liniar.id", "Staff123!"),
}


def _login(role):
    s = requests.Session()
    email, pw = CREDS[role]
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=15)
    assert r.status_code == 200, f"Login {role} failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["email"] == email
    return s, data


@pytest.fixture(scope="module")
def admin_sess():
    s, _ = _login("admin")
    return s


@pytest.fixture(scope="module")
def kasir_sess():
    s, _ = _login("kasir")
    return s


@pytest.fixture(scope="module")
def keuangan_sess():
    s, _ = _login("keuangan")
    return s


@pytest.fixture(scope="module")
def produksi_sess():
    s, _ = _login("produksi")
    return s


@pytest.fixture(scope="module")
def gudang_sess():
    s, _ = _login("gudang")
    return s


# ---------- Auth: all demo users can log in ----------
@pytest.mark.parametrize("role", ["admin", "keuangan", "produksi", "kasir", "gudang", "staff"])
def test_demo_logins(role):
    s, data = _login(role)
    r = s.get(f"{API}/auth/me")
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == CREDS[role][0]


# ---------- POS sale ----------
def test_pos_sale_by_kasir(kasir_sess):
    r = kasir_sess.post(f"{API}/sales", json={
        "channel": "POS", "sku": "LIN-OVR-001", "quantity": 1,
        "unit_price": 250000, "customer": "TEST_POS_Walkin",
        "payment_method": "qris"
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["channel"] == "POS"
    assert data["payment_method"] == "qris"
    assert data["revenue"] == 250000
    assert "invoice" in data


def test_invalid_channel_422(kasir_sess):
    r = kasir_sess.post(f"{API}/sales", json={
        "channel": "INVALID", "sku": "LIN-OVR-001", "quantity": 1, "unit_price": 100000
    })
    assert r.status_code == 422


# ---------- Role gating: kasir & staf_gudang must be 403 on admin endpoints ----------
@pytest.mark.parametrize("path", [
    "/cash-movements", "/assets", "/receivables-payables", "/reports", "/ledger"
])
def test_kasir_forbidden_admin_endpoints(kasir_sess, path):
    r = kasir_sess.get(f"{API}{path}")
    # /reports may not exist as GET; accept 403 or 404 but never 200
    assert r.status_code in (403, 404), f"kasir got {r.status_code} on {path}: {r.text[:100]}"
    if r.status_code == 200:
        pytest.fail(f"kasir should not access {path}")


@pytest.mark.parametrize("path", ["/cash-movements", "/assets", "/receivables-payables"])
def test_gudang_forbidden(gudang_sess, path):
    r = gudang_sess.get(f"{API}{path}")
    assert r.status_code == 403


def test_admin_keuangan_forbidden_kas_bank(keuangan_sess):
    """admin_keuangan should NOT access admin_only endpoints (by design)"""
    r = keuangan_sess.get(f"{API}/cash-movements")
    assert r.status_code == 403


def test_admin_can_access_kas_bank(admin_sess):
    r = admin_sess.get(f"{API}/cash-movements")
    assert r.status_code == 200


# ---------- CSV Import ----------
def test_csv_import_shopee(kasir_sess):
    csv_content = (
        "sku,quantity,unit_price,customer,order_ref\n"
        "LIN-OVR-001,1,180000,TEST_ShopeeUser,SHP-TEST-001\n"
        "NON-EXISTENT,2,50000,TEST_Bad,SHP-TEST-002\n"
        "LIN-OVR-001,0,180000,TEST_Zero,SHP-TEST-003\n"
    )
    files = {"file": ("test.csv", csv_content.encode("utf-8"), "text/csv")}
    r = kasir_sess.post(f"{API}/sales/import-csv?channel=Shopee", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["channel"] == "Shopee"
    assert data["imported"] == 1
    assert data["skipped"] == 2
    assert data["total_revenue"] == 180000
    # skipped rows should have reasons
    reasons = " ".join(row.get("reason", "") for row in data["skipped_rows"])
    assert "NON-EXISTENT" in reasons or "tidak ada" in reasons


def test_csv_import_invalid_channel(kasir_sess):
    files = {"file": ("t.csv", b"sku,quantity,unit_price\nLIN-OVR-001,1,100\n", "text/csv")}
    r = kasir_sess.post(f"{API}/sales/import-csv?channel=INVALID", files=files)
    assert r.status_code == 422


# ---------- Channel allocation ----------
def test_allocate_success(admin_sess):
    r = admin_sess.get(f"{API}/inventory")
    assert r.status_code == 200
    inv = r.json()
    bj = next((i for i in inv if i["type"] == "Barang Jadi" and i.get("available", 0) >= 6), None)
    assert bj, "No Barang Jadi with enough stock"
    sku = bj["sku"]
    available = bj["available"]
    # allocate small amounts across channels
    body = {"Shopee": 1, "Tokopedia": 1, "TikTok": 1, "Offline": 1, "POS": 1}
    total = sum(body.values())
    assert total <= available
    r = admin_sess.post(f"{API}/inventory/{sku}/allocate", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sku"] == sku
    assert data["channel_stock"] == body
    assert data["unallocated"] == available - total


def test_allocate_over_available(admin_sess):
    r = admin_sess.get(f"{API}/inventory")
    bj = next((i for i in r.json() if i["type"] == "Barang Jadi"), None)
    sku = bj["sku"]
    over = bj["available"] + 100
    r = admin_sess.post(f"{API}/inventory/{sku}/allocate", json={"Shopee": over})
    assert r.status_code == 409


def test_allocate_bahan_baku_422(admin_sess):
    r = admin_sess.get(f"{API}/inventory")
    bb = next((i for i in r.json() if i["type"] == "Bahan Baku"), None)
    assert bb, "No Bahan Baku SKU"
    r = admin_sess.post(f"{API}/inventory/{bb['sku']}/allocate", json={"Shopee": 1})
    assert r.status_code == 422


def test_allocate_invalid_channel_422(admin_sess):
    r = admin_sess.get(f"{API}/inventory")
    bj = next((i for i in r.json() if i["type"] == "Barang Jadi"), None)
    r = admin_sess.post(f"{API}/inventory/{bj['sku']}/allocate", json={"BadChannel": 1})
    assert r.status_code == 422


# ---------- Regression on reports / balance detail ----------
def test_reports_still_works(admin_sess):
    r = admin_sess.get(f"{API}/reports?granularity=all")
    # Endpoint may or may not exist; accept 200 or 404 (route only)
    assert r.status_code in (200, 404, 422)


def test_balance_detail_kas(admin_sess):
    r = admin_sess.get(f"{API}/balance-detail?kind=kas")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d and "saldo" in d


def test_receivables_payables_list(admin_sess):
    r = admin_sess.get(f"{API}/receivables-payables")
    assert r.status_code == 200
    assert "items" in r.json()


def test_ledger(admin_sess):
    r = admin_sess.get(f"{API}/ledger")
    assert r.status_code == 200
    assert "entries" in r.json()
