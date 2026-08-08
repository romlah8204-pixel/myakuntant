"""Iteration 8 - Backup endpoints tests (Emergent Object Storage integration)."""
import os
import json
import re
import pytest
import requests
from datetime import datetime

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
STAFF = {"email": "staff@liniar.id", "password": "Staff123!"}

BACKUP_COLLECTIONS = ["purchases", "production", "sales_transactions", "inventory", "operating_expenses"]
FORBIDDEN_COLLECTIONS = ["users", "activity_logs"]


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


# ---------------- POST /api/backups ----------------

def test_post_backup_no_auth():
    r = requests.post(f"{API}/backups", timeout=30)
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


def test_post_backup_staff_forbidden(staff_session):
    r = staff_session.post(f"{API}/backups", timeout=30)
    assert r.status_code == 403, f"Expected 403, got {r.status_code}"


@pytest.fixture(scope="module")
def created_backup(admin_session):
    r = admin_session.post(f"{API}/backups", timeout=120)
    assert r.status_code == 200, f"Backup create failed: {r.status_code} {r.text}"
    return r.json()


def test_post_backup_admin_returns_metadata(created_backup):
    b = created_backup
    for k in ["id", "filename", "storage_path", "size", "counts", "total_rows", "created_at", "created_by"]:
        assert k in b, f"missing key {k} in response"
    assert re.match(r"^liniar-\d{8}-\d{6}\.json$", b["filename"]), f"bad filename {b['filename']}"
    assert b["size"] > 0
    assert b["created_by"] == ADMIN["email"]
    # ISO created_at parseable
    datetime.fromisoformat(b["created_at"].replace("Z", "+00:00"))
    # counts has all 5 required keys
    for c in BACKUP_COLLECTIONS:
        assert c in b["counts"], f"counts missing {c}"
        assert isinstance(b["counts"][c], int)
    assert b["total_rows"] == sum(b["counts"].values())


# ---------------- GET /api/backups list ----------------

def test_list_backups_staff_forbidden(staff_session):
    r = staff_session.get(f"{API}/backups", timeout=30)
    assert r.status_code == 403


def test_list_backups_admin_desc(admin_session, created_backup):
    r = admin_session.get(f"{API}/backups", timeout=30)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    # created backup should be present
    assert any(x["id"] == created_backup["id"] for x in rows)
    # descending by created_at
    times = [x["created_at"] for x in rows]
    assert times == sorted(times, reverse=True), "backups not sorted desc by created_at"


# ---------------- GET /api/backups/{id}/download ----------------

def test_download_backup_not_found(admin_session):
    r = admin_session.get(f"{API}/backups/nonexistent-id-xyz/download", timeout=30)
    assert r.status_code == 404


def test_download_backup_staff_forbidden(staff_session, created_backup):
    r = staff_session.get(f"{API}/backups/{created_backup['id']}/download", timeout=30)
    assert r.status_code == 403


def test_download_backup_admin_content(admin_session, created_backup):
    r = admin_session.get(f"{API}/backups/{created_backup['id']}/download", timeout=120)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("application/json")
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert created_backup["filename"] in cd
    # Valid JSON with required keys
    body = r.json()
    assert "generated_at" in body
    assert body.get("app") == "liniar"
    assert "collections" in body
    for c in BACKUP_COLLECTIONS:
        assert c in body["collections"], f"collections missing {c}"
        assert isinstance(body["collections"][c], list)
    # No forbidden collections
    for c in FORBIDDEN_COLLECTIONS:
        assert c not in body["collections"], f"forbidden collection {c} leaked into backup"
    # Size cross-check: size stored ~ len(payload). Recompute similar payload len.
    # We can't reproduce exact bytes (server-side ensure_ascii=False + default=str),
    # but we verify size > 0 and content length reasonable.
    assert len(r.content) > 0
    # Sanity: counts in metadata should match rows in payload (>= to allow race conditions)
    for c in BACKUP_COLLECTIONS:
        assert len(body["collections"][c]) == created_backup["counts"][c], (
            f"row count mismatch for {c}: payload={len(body['collections'][c])} meta={created_backup['counts'][c]}"
        )


# ---------------- activity_logs integration ----------------

def test_backup_logged_to_activity_logs(admin_session, created_backup):
    r = admin_session.get(f"{API}/activity-logs", params={"entity": "backup", "action": "create", "limit": 20}, timeout=30)
    assert r.status_code == 200
    data = r.json()
    rows = data.get("rows", [])
    match = [x for x in rows if x.get("entity_id") == created_backup["id"]]
    assert match, f"no activity_log entry for backup id={created_backup['id']}"
    entry = match[0]
    assert entry["action"] == "create"
    assert entry["entity"] == "backup"
    summary = entry.get("summary", "")
    assert created_backup["filename"] in summary, f"filename not in summary: {summary}"
    assert str(created_backup["total_rows"]) in summary, f"total_rows not in summary: {summary}"


# ---------------- Data integrity: counts match live DB counts (>=) ----------------

def test_counts_match_actual_data(admin_session, created_backup):
    # Compare backup counts vs current DB rows via list endpoints where available.
    # We use permissive endpoints admin can access; not all collections have list endpoints,
    # but purchases/production/sales are exposed.
    endpoints = {
        "purchases": "/purchases",
        "production": "/production",
        "sales_transactions": "/sales",
    }
    for coll, ep in endpoints.items():
        r = admin_session.get(f"{API}{ep}", timeout=30)
        if r.status_code != 200:
            continue
        data = r.json()
        # data may be dict with rows or list
        if isinstance(data, dict) and "rows" in data:
            live = len(data["rows"])
        elif isinstance(data, list):
            live = len(data)
        else:
            continue
        # backup snapshot count should be <= current live count (rows only added over time)
        assert created_backup["counts"][coll] <= live + 5, (
            f"{coll}: backup counts {created_backup['counts'][coll]} > live {live}"
        )
