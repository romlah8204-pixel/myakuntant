"""Iteration 9 - GET /api/activity-logs/export.csv tests (admin-only CSV export)."""
import os
import io
import csv
import json
import re
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
STAFF = {"email": "staff@liniar.id", "password": "Staff123!"}

EXPECTED_HEADER = ["created_at", "user_email", "user_role", "action", "entity", "entity_id", "summary", "details"]


def _login(creds):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, f"Login {creds['email']} failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def staff_session():
    return _login(STAFF)


def _parse_csv(text):
    reader = csv.reader(io.StringIO(text))
    return list(reader)


# ---------- Auth ----------

def test_export_csv_no_auth():
    r = requests.get(f"{API}/activity-logs/export.csv", timeout=30)
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


def test_export_csv_staff_forbidden(staff_session):
    r = staff_session.get(f"{API}/activity-logs/export.csv", timeout=30)
    assert r.status_code == 403, f"Expected 403, got {r.status_code} - {r.text}"


# ---------- Format/headers ----------

def test_export_csv_headers_and_format(admin_session):
    r = admin_session.get(f"{API}/activity-logs/export.csv", timeout=60)
    assert r.status_code == 200, r.text
    ct = r.headers.get("Content-Type", "")
    assert "text/csv" in ct and "charset=utf-8" in ct.replace(" ", "").lower(), f"Bad Content-Type: {ct}"
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd, f"Bad Content-Disposition: {cd}"
    m = re.search(r'filename="?(liniar-audit-\d{8}-\d{6}\.csv)"?', cd)
    assert m, f"Filename pattern mismatch: {cd}"

    rows = _parse_csv(r.text)
    assert len(rows) >= 1, "CSV empty"
    assert rows[0] == EXPECTED_HEADER, f"Header mismatch: {rows[0]}"
    # each data row has 8 columns
    for row in rows[1:]:
        assert len(row) == 8, f"Row length != 8: {row}"


def test_export_csv_details_is_json_string(admin_session):
    # create an activity: create + delete an opex to guarantee log with details
    period = "2099-12"
    payload = {"period": period, "category": f"TEST_iter9_{uuid.uuid4().hex[:6]}", "amount": 12345, "note": "csv,test\"quote"}
    cr = admin_session.post(f"{API}/opex", json=payload, timeout=30)
    assert cr.status_code == 200, cr.text
    opex_id = cr.json()["id"]

    r = admin_session.get(f"{API}/activity-logs/export.csv", params={"entity": "opex", "action": "create"}, timeout=60)
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    assert rows[0] == EXPECTED_HEADER
    assert len(rows) >= 2, "expected at least the just-created opex row"
    # find our row
    found = None
    for row in rows[1:]:
        if row[5] == opex_id:
            found = row
            break
    assert found is not None, f"created opex not found in CSV export"
    assert found[3] == "create"
    assert found[4] == "opex"
    # details must be JSON parseable
    details = json.loads(found[7])
    assert isinstance(details, dict)
    assert details.get("period") == period

    # cleanup
    admin_session.delete(f"{API}/opex/{opex_id}", timeout=30)


# ---------- Filters ----------

def test_export_csv_filter_action(admin_session):
    r = admin_session.get(f"{API}/activity-logs/export.csv", params={"action": "create"}, timeout=60)
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows[1:]:
        assert row[3] == "create", f"non-create row leaked: {row}"


def test_export_csv_filter_entity_opex(admin_session):
    r = admin_session.get(f"{API}/activity-logs/export.csv", params={"entity": "opex"}, timeout=60)
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows[1:]:
        assert row[4] == "opex", f"non-opex row leaked: {row}"


def test_export_csv_filter_user_email(admin_session):
    r = admin_session.get(f"{API}/activity-logs/export.csv", params={"user_email": "admin@liniar.id"}, timeout=60)
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows[1:]:
        assert row[1] == "admin@liniar.id", f"leaked user: {row}"


def test_export_csv_filter_combined(admin_session):
    # make sure at least one purchase-create exists (may already exist)
    r = admin_session.get(f"{API}/activity-logs/export.csv", params={"action": "create", "entity": "purchase"}, timeout=60)
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows[1:]:
        assert row[3] == "create" and row[4] == "purchase"


# ---------- Ordering ----------

def test_export_csv_ordered_desc(admin_session):
    r = admin_session.get(f"{API}/activity-logs/export.csv", timeout=60)
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    timestamps = [row[0] for row in rows[1:] if row[0]]
    # ISO strings sort lexicographically consistent with time
    for a, b in zip(timestamps, timestamps[1:]):
        assert a >= b, f"CSV not sorted desc by created_at: {a} < {b}"


# ---------- No limit vs list endpoint ----------

def test_export_csv_no_limit_vs_list(admin_session):
    list_r = admin_session.get(f"{API}/activity-logs", params={"limit": 500}, timeout=60)
    assert list_r.status_code == 200
    total = list_r.json()["total"]

    csv_r = admin_session.get(f"{API}/activity-logs/export.csv", timeout=60)
    assert csv_r.status_code == 200
    rows = _parse_csv(csv_r.text)
    data_rows = len(rows) - 1  # minus header
    # CSV should contain all rows up to 50000 (>= total unless total>50000)
    if total <= 50000:
        # Allow small drift from concurrent activity between the two calls
        assert data_rows >= total - 5, f"CSV rows {data_rows} < list total {total} - 5"


# ---------- Regression: iter7 list still works ----------

def test_regression_list_activity_logs(admin_session):
    r = admin_session.get(f"{API}/activity-logs", params={"limit": 10, "offset": 0}, timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert set(["total", "limit", "offset", "rows"]).issubset(data.keys())
    assert data["limit"] == 10
    assert isinstance(data["rows"], list)
    assert len(data["rows"]) <= 10


def test_regression_list_activity_logs_filter(admin_session):
    r = admin_session.get(f"{API}/activity-logs", params={"action": "create", "entity": "opex", "limit": 5}, timeout=30)
    assert r.status_code == 200
    for row in r.json()["rows"]:
        assert row["action"] == "create"
        assert row["entity"] == "opex"
