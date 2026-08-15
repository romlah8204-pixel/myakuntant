"""Tests for Iter12: Buku Besar (ledger), drill-down reports, inventory SKU history."""
import os
import pytest
import requests
from datetime import datetime, timezone

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


@pytest.fixture(scope="module")
def anon():
    return requests.Session()


# ---------------- /api/ledger ----------------

class TestLedger:
    def test_ledger_admin_shape_and_sort(self, admin):
        r = admin.get(f"{API}/ledger")
        assert r.status_code == 200
        j = r.json()
        for k in ("entries", "count", "total_in", "total_out", "net", "start", "end", "kind"):
            assert k in j, f"missing key {k}"
        assert j["count"] == len(j["entries"])
        # ASC by date
        dates = [e["date"] for e in j["entries"]]
        assert dates == sorted(dates), "entries not sorted ASC by date"
        # running balance cumulative
        bal = 0
        for e in j["entries"]:
            bal += e["in"] - e["out"]
            assert e["balance"] == bal, f"balance mismatch at {e['ref']}"
        # net = total_in - total_out
        assert j["net"] == j["total_in"] - j["total_out"]
        if j["entries"]:
            assert j["entries"][-1]["balance"] == j["net"]

    def test_ledger_kind_sale(self, admin):
        r = admin.get(f"{API}/ledger", params={"kind": "sale"})
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["type"] == "sale"

    def test_ledger_kind_purchase(self, admin):
        r = admin.get(f"{API}/ledger", params={"kind": "purchase"})
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["type"] == "purchase"

    def test_ledger_kind_production(self, admin):
        r = admin.get(f"{API}/ledger", params={"kind": "production"})
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["type"] == "production"

    def test_ledger_kind_opex(self, admin):
        r = admin.get(f"{API}/ledger", params={"kind": "opex"})
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["type"] == "opex"

    def test_ledger_kind_invalid_returns_empty(self, admin):
        r = admin.get(f"{API}/ledger", params={"kind": "bogus"})
        assert r.status_code == 200
        assert r.json()["entries"] == []
        assert r.json()["count"] == 0

    def test_ledger_date_range(self, admin):
        r = admin.get(f"{API}/ledger", params={"start": "2026-08-01", "end": "2026-08-31"})
        assert r.status_code == 200
        j = r.json()
        for e in j["entries"]:
            d = e["date"]
            assert "2026-08-01" <= d[:10] <= "2026-08-31", f"out of range: {d}"
        assert j["start"] == "2026-08-01"
        assert j["end"] == "2026-08-31"

    def test_ledger_no_auth(self, anon):
        r = anon.get(f"{API}/ledger")
        assert r.status_code == 401

    def test_ledger_staff_forbidden(self, staff):
        r = staff.get(f"{API}/ledger")
        assert r.status_code == 403


# ---------------- /api/ledger/export.csv ----------------

class TestLedgerCSV:
    def test_csv_admin_headers_and_content(self, admin):
        r = admin.get(f"{API}/ledger/export.csv")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/csv" in ct
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "liniar-bukubesar-" in cd
        assert ".csv" in cd
        body = r.text
        first_line = body.splitlines()[0]
        assert first_line == "date,type,ref,description,kas_masuk,kas_keluar,saldo"
        # TOTAL row
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert lines[-1].startswith(",,,TOTAL") or "TOTAL" in lines[-1]

    def test_csv_respects_kind_filter(self, admin):
        r_all = admin.get(f"{API}/ledger", params={"kind": "sale"}).json()
        r = admin.get(f"{API}/ledger/export.csv", params={"kind": "sale"})
        assert r.status_code == 200
        # data rows should equal count of sale entries
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        # header + N data rows + TOTAL
        assert len(lines) == 1 + r_all["count"] + 1

    def test_csv_staff_forbidden(self, staff):
        r = staff.get(f"{API}/ledger/export.csv")
        assert r.status_code == 403


# ---------------- /api/inventory/{sku}/history ----------------

class TestInventoryHistory:
    def test_history_admin_shape(self, admin):
        # find any Barang Jadi SKU
        inv = admin.get(f"{API}/inventory").json()
        bj = [i for i in inv if i.get("type") == "Barang Jadi"]
        assert bj, "no Barang Jadi in inventory"
        sku = bj[0]["sku"]
        r = admin.get(f"{API}/inventory/{sku}/history")
        assert r.status_code == 200
        j = r.json()
        for k in ("sku", "name", "variant", "unit", "current_available", "events"):
            assert k in j
        assert j["sku"] == sku
        # ASC by date and running balance
        dates = [e["date"] for e in j["events"]]
        assert dates == sorted(dates)
        bal = 0
        for e in j["events"]:
            bal += e["in"] - e["out"]
            assert e["balance"] == bal

    def test_history_staff_ok(self, staff):
        inv = staff.get(f"{API}/inventory").json()
        assert inv
        sku = inv[0]["sku"]
        r = staff.get(f"{API}/inventory/{sku}/history")
        assert r.status_code == 200

    def test_history_not_found(self, admin):
        r = admin.get(f"{API}/inventory/DOES-NOT-EXIST/history")
        assert r.status_code == 404
        assert "tidak ditemukan" in r.json().get("detail", "").lower()

    def test_history_no_auth(self, anon):
        r = anon.get(f"{API}/inventory/LIN-OVR-001/history")
        assert r.status_code == 401

    def test_history_produksi_after_new_batch(self, admin):
        # Create a production batch and verify a 'produksi' event appears
        inv = admin.get(f"{API}/inventory").json()
        bj = [i for i in inv if i.get("type") == "Barang Jadi"]
        assert bj
        sku = bj[0]["sku"]
        payload = {
            "product": bj[0].get("name", "Test Product"),
            "sku": sku,
            "output_qty": 3,
            "material_cost": 50000,
            "labor_cost": 30000,
            "overhead_cost": 15000,
        }
        r = admin.post(f"{API}/production", json=payload)
        # production endpoint may be POST /api/production; if not present, skip
        if r.status_code == 404:
            pytest.skip("POST /api/production not available in this build")
        assert r.status_code in (200, 201), f"prod create failed: {r.status_code} {r.text}"
        hist = admin.get(f"{API}/inventory/{sku}/history").json()
        prod_events = [e for e in hist["events"] if e["type"] == "produksi"]
        assert len(prod_events) >= 1, "no produksi event after creating a batch"
        assert prod_events[-1]["in"] >= 3


# ---------------- /api/reports/detail ----------------

class TestReportsDetail:
    def test_detail_revenue_shape(self, admin):
        r = admin.get(f"{API}/reports/detail", params={"kind": "revenue"})
        assert r.status_code == 200
        j = r.json()
        for k in ("kind", "period", "channel", "rows", "count", "total"):
            assert k in j
        assert j["kind"] == "revenue"
        assert j["count"] == len(j["rows"])
        assert j["total"] == sum(row["amount"] for row in j["rows"])
        for row in j["rows"][:5]:
            for k in ("date", "ref", "description", "amount"):
                assert k in row

    @pytest.mark.parametrize("kind", ["revenue", "cogs", "purchases", "production", "opex", "cash_out"])
    def test_detail_valid_kinds(self, admin, kind):
        r = admin.get(f"{API}/reports/detail", params={"kind": kind})
        assert r.status_code == 200
        assert r.json()["kind"] == kind

    def test_detail_invalid_kind(self, admin):
        r = admin.get(f"{API}/reports/detail", params={"kind": "bogus"})
        assert r.status_code == 422

    def test_detail_channel_filter_shopee(self, admin):
        r = admin.get(f"{API}/reports/detail", params={"kind": "revenue", "channel": "Shopee"})
        assert r.status_code == 200
        for row in r.json()["rows"]:
            assert "Shopee" in row["description"]

    def test_detail_cash_out_monthly_period(self, admin):
        r = admin.get(f"{API}/reports/detail", params={"kind": "cash_out", "granularity": "monthly", "period": "2026-08"})
        assert r.status_code == 200
        j = r.json()
        # rows should be from Aug 2026 (either created_at in month, or opex period == 2026-08)
        for row in j["rows"]:
            d = row.get("date", "")
            ref = row.get("ref", "")
            in_month = d[:7] == "2026-08" or ref == "2026-08"
            assert in_month, f"row not in 2026-08: {row}"

    def test_detail_opex_channel_shopee_empty(self, admin):
        r = admin.get(f"{API}/reports/detail", params={"kind": "opex", "channel": "Shopee"})
        assert r.status_code == 200
        assert r.json()["rows"] == []

    def test_detail_staff_forbidden(self, staff):
        r = staff.get(f"{API}/reports/detail", params={"kind": "revenue"})
        assert r.status_code == 403

    def test_detail_no_auth(self, anon):
        r = anon.get(f"{API}/reports/detail", params={"kind": "revenue"})
        assert r.status_code == 401

    def test_detail_revenue_consistency_with_reports(self, admin):
        params = {"granularity": "monthly", "period": "2026-08"}
        detail = admin.get(f"{API}/reports/detail", params={"kind": "revenue", **params}).json()
        rep = admin.get(f"{API}/reports", params=params).json()
        # income.revenue in reports should equal total in detail
        rep_rev = rep.get("income", {}).get("revenue", None)
        if rep_rev is None:
            pytest.skip("reports shape differs; skip cross-check")
        assert detail["total"] == rep_rev, f"detail total {detail['total']} != reports revenue {rep_rev}"


# ---------------- Regression: prior iterations still work ----------------

class TestRegression:
    def test_auth_me(self, admin):
        r = admin.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json().get("role") == "admin"

    def test_inventory_list(self, admin):
        r = admin.get(f"{API}/inventory")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_ready_to_sell(self, admin):
        r = admin.get(f"{API}/ready-to-sell")
        assert r.status_code == 200

    def test_reports_all(self, admin):
        r = admin.get(f"{API}/reports")
        assert r.status_code == 200

    def test_sales_by_channel(self, admin):
        r = admin.get(f"{API}/sales-by-channel")
        assert r.status_code == 200

    def test_activity_logs_admin(self, admin):
        r = admin.get(f"{API}/activity-logs")
        assert r.status_code == 200

    def test_public_catalog(self, anon):
        r = anon.get(f"{API}/public/catalog")
        assert r.status_code == 200
