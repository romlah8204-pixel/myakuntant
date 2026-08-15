"""Tests for Iter14: Buku Besar channel filter."""
import os
import pytest
import requests


def _read_frontend_env():
    p = "/app/frontend/.env"
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("REACT_APP_BACKEND_URL", "")


BASE = _read_frontend_env().rstrip("/")
assert BASE, "REACT_APP_BACKEND_URL not set"
API = f"{BASE}/api"

ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
STAFF = {"email": "staff@liniar.id", "password": "Staff123!"}

CHANNELS = ["Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"]


def _login(creds):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def staff():
    return _login(STAFF)


# --- Ledger channel filter ---

def test_ledger_default_semua(admin):
    r = admin.get(f"{API}/ledger", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["channel"] == "Semua"
    assert "entries" in data
    # sales rows should include multiple channels
    sale_rows = [e for e in data["entries"] if e["type"] == "sale"]
    assert len(sale_rows) > 0
    channels_seen = set()
    for row in sale_rows:
        for ch in CHANNELS:
            if f"Penjualan {ch} " in row["description"]:
                channels_seen.add(ch)
    assert len(channels_seen) >= 2, f"expected multi-channel, got {channels_seen}"


def test_ledger_shopee_only_filters_sales(admin):
    r = admin.get(f"{API}/ledger", params={"channel": "Shopee"}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["channel"] == "Shopee"
    sale_rows = [e for e in data["entries"] if e["type"] == "sale"]
    assert len(sale_rows) > 0
    for row in sale_rows:
        assert "Penjualan Shopee " in row["description"], f"non-Shopee sale leaked: {row['description']}"
        for other in ["Tokopedia", "TikTok", "Offline", "Bazar"]:
            assert f"Penjualan {other} " not in row["description"]


def test_ledger_shopee_kind_sale_matches_db_sum(admin):
    # ledger filtered by Shopee + kind=sale
    r = admin.get(f"{API}/ledger", params={"channel": "Shopee", "kind": "sale"}, timeout=15)
    assert r.status_code == 200
    data = r.json()
    for e in data["entries"]:
        assert e["type"] == "sale"
    # cross-check with reports/detail kind=revenue channel=Shopee
    r2 = admin.get(f"{API}/reports/detail", params={"kind": "revenue", "channel": "Shopee"}, timeout=15)
    assert r2.status_code == 200
    detail = r2.json()
    # total_in should equal sum of Shopee revenues (== detail.total)
    assert data["total_in"] == detail["total"], f"ledger total_in {data['total_in']} vs detail total {detail['total']}"
    assert data["total_out"] == 0
    assert len(data["entries"]) == detail["count"]


def test_ledger_shopee_no_kind_keeps_non_sale_rows(admin):
    """When channel=Shopee and kind empty, non-sale rows (purchase/production/opex) still appear."""
    r = admin.get(f"{API}/ledger", params={"channel": "Shopee"}, timeout=15)
    assert r.status_code == 200
    data = r.json()
    types = {e["type"] for e in data["entries"]}
    # sale must exist; verify other kinds exist too (based on seed data likely has purchases/opex)
    assert "sale" in types
    # compare with Semua to ensure non-sale count unchanged
    r_all = admin.get(f"{API}/ledger", params={"channel": "Semua"}, timeout=15).json()
    non_sale_all = [e for e in r_all["entries"] if e["type"] != "sale"]
    non_sale_shopee = [e for e in data["entries"] if e["type"] != "sale"]
    assert len(non_sale_all) == len(non_sale_shopee), "non-sale rows should not be affected by channel filter"


def test_ledger_invalid_channel_422(admin):
    r = admin.get(f"{API}/ledger", params={"channel": "Blibli"}, timeout=15)
    assert r.status_code == 422
    body = r.json()
    detail = body.get("detail", "")
    assert "Kanal tidak valid" in (detail if isinstance(detail, str) else str(detail))


def test_ledger_tokopedia_only(admin):
    r = admin.get(f"{API}/ledger", params={"channel": "Tokopedia"}, timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data["channel"] == "Tokopedia"
    sale_rows = [e for e in data["entries"] if e["type"] == "sale"]
    assert len(sale_rows) > 0
    for row in sale_rows:
        assert "Penjualan Tokopedia " in row["description"]


def test_ledger_csv_tiktok_channel(admin):
    r = admin.get(f"{API}/ledger/export.csv", params={"channel": "TikTok"}, timeout=20)
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "").lower()
    text = r.text
    assert "tanggal" in text.lower() or "date" in text.lower() or "," in text  # valid CSV
    # any sale line must be TikTok
    lines = text.splitlines()
    for line in lines[1:]:
        if ",sale," in line or ";sale;" in line:
            assert "TikTok" in line, f"non-TikTok sale in CSV: {line}"


def test_ledger_csv_invalid_channel_422(admin):
    r = admin.get(f"{API}/ledger/export.csv", params={"channel": "invalid"}, timeout=15)
    assert r.status_code == 422


def test_ledger_no_auth_401():
    r = requests.get(f"{API}/ledger", params={"channel": "Shopee"}, timeout=15)
    assert r.status_code == 401


def test_ledger_staff_403(staff):
    r = staff.get(f"{API}/ledger", params={"channel": "Shopee"}, timeout=15)
    assert r.status_code == 403


def test_ledger_regression_default_matches_no_param(admin):
    """Default channel=Semua should behave identical to no channel param (iter12 behavior)."""
    r_none = admin.get(f"{API}/ledger", timeout=15).json()
    r_semua = admin.get(f"{API}/ledger", params={"channel": "Semua"}, timeout=15).json()
    assert r_none["count"] == r_semua["count"]
    assert r_none["total_in"] == r_semua["total_in"]
    assert r_none["total_out"] == r_semua["total_out"]
    assert r_none["net"] == r_semua["net"]


# --- Regression: iter5-iter13 endpoints ---

def test_reports_ok(admin):
    r = admin.get(f"{API}/reports", timeout=15)
    assert r.status_code == 200

def test_reports_detail_ok(admin):
    r = admin.get(f"{API}/reports/detail", params={"kind": "revenue"}, timeout=15)
    assert r.status_code == 200

def test_inventory_sku_history_ok(admin):
    r = admin.get(f"{API}/inventory/LIN-OVR-001/history", timeout=15)
    assert r.status_code == 200

def test_activity_logs_ok(admin):
    r = admin.get(f"{API}/activity-logs", timeout=15)
    assert r.status_code == 200

def test_backups_ok(admin):
    r = admin.get(f"{API}/backups", timeout=15)
    assert r.status_code == 200

def test_public_catalog_ok():
    r = requests.get(f"{API}/public/catalog", timeout=15)
    assert r.status_code == 200
