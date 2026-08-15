"""
Iter17 PSAK Balance Sheet + Drill-down (/api/balance-detail) tests.

Validates:
 - Auth (401 unauth, 403 staff, admin allowed)
 - kind validation (422 for invalid kind, valid list keys)
 - Row semantics for each kind (kas, bank, utang_pinjaman, modal_disetor,
   laba_ditahan, persediaan_bahan, persediaan_barang_jadi, aset_tetap)
 - Saldo alignment with /api/reports balance.detail
 - New PSAK keys in reports balance.detail
 - Regression: balance.assets == balance.liabilities + balance.equity
 - /api/reports/detail still works
"""
import os
import requests
import pytest
from pathlib import Path

def _load_frontend_url():
    if "REACT_APP_BACKEND_URL" in os.environ:
        return os.environ["REACT_APP_BACKEND_URL"]
    env_path = Path("/app/frontend/.env")
    for line in env_path.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("REACT_APP_BACKEND_URL not configured")

BASE = _load_frontend_url().rstrip("/") + "/api"
ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
STAFF = {"email": "staff@liniar.id", "password": "Staff123!"}

VALID_KINDS = [
    "kas", "bank", "persediaan_bahan", "persediaan_barang_jadi",
    "aset_tetap", "utang_pinjaman", "modal_disetor", "laba_ditahan",
]


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE}/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def staff_session():
    return _login(STAFF)


@pytest.fixture(scope="module")
def reports_payload(admin_session):
    r = admin_session.get(f"{BASE}/reports", timeout=20)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Auth ----------
def test_balance_detail_requires_auth():
    r = requests.get(f"{BASE}/balance-detail?kind=kas", timeout=10)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


def test_balance_detail_staff_forbidden(staff_session):
    r = staff_session.get(f"{BASE}/balance-detail?kind=kas", timeout=10)
    assert r.status_code == 403, f"expected 403 got {r.status_code} {r.text}"


def test_balance_detail_invalid_kind(admin_session):
    r = admin_session.get(f"{BASE}/balance-detail?kind=INVALID", timeout=10)
    assert r.status_code == 422
    body = r.json()
    detail = str(body.get("detail", ""))
    # Should list some valid kinds
    for k in ["kas", "bank", "utang_pinjaman"]:
        assert k in detail, f"expected {k} in error detail: {detail}"


# ---------- Row schema for each kind ----------
@pytest.mark.parametrize("kind", VALID_KINDS)
def test_balance_detail_schema(admin_session, kind):
    r = admin_session.get(f"{BASE}/balance-detail?kind={kind}", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ["kind", "label", "rows", "count", "total_in", "total_out", "saldo"]:
        assert key in body, f"missing {key}"
    assert body["kind"] == kind
    assert isinstance(body["rows"], list)
    assert body["count"] == len(body["rows"])
    for row in body["rows"]:
        assert set(["date", "ref", "description", "direction", "amount"]).issubset(row.keys())
        assert row["direction"] in ("in", "out")
    # saldo == total_in - total_out
    assert round(body["saldo"], 2) == round(body["total_in"] - body["total_out"], 2)


# ---------- Cross-check saldo against /api/reports balance.detail ----------
def test_kas_saldo_matches_reports(admin_session, reports_payload):
    r = admin_session.get(f"{BASE}/balance-detail?kind=kas", timeout=15).json()
    kas_expected = reports_payload["balance"]["detail"]["assets"]["lancar"]["kas"]
    assert round(r["saldo"], 2) == round(kas_expected, 2)


def test_bank_saldo_matches_reports(admin_session, reports_payload):
    r = admin_session.get(f"{BASE}/balance-detail?kind=bank", timeout=15).json()
    bank_expected = reports_payload["balance"]["detail"]["assets"]["lancar"]["bank"]
    assert round(r["saldo"], 2) == round(bank_expected, 2)


def test_utang_pinjaman_saldo(admin_session, reports_payload):
    r = admin_session.get(f"{BASE}/balance-detail?kind=utang_pinjaman", timeout=15).json()
    exp = reports_payload["balance"]["detail"]["liabilities"]["jangka_panjang"]["utang_pinjaman"]
    # Saldo can be negative if bayar > terima, but reports clamps to >= 0.
    # Compare unclamped saldo when it's positive; otherwise reports must be 0.
    if r["saldo"] >= 0:
        assert round(r["saldo"], 2) == round(exp, 2)
    else:
        assert exp == 0
    # direction rules
    for row in r["rows"]:
        assert row["direction"] in ("in", "out")


def test_modal_disetor_saldo(admin_session, reports_payload):
    r = admin_session.get(f"{BASE}/balance-detail?kind=modal_disetor", timeout=15).json()
    exp = reports_payload["balance"]["detail"]["equity"]["modal_disetor"]
    assert round(r["saldo"], 2) == round(exp, 2)


def test_aset_tetap_saldo(admin_session, reports_payload):
    r = admin_session.get(f"{BASE}/balance-detail?kind=aset_tetap", timeout=15).json()
    exp = reports_payload["balance"]["detail"]["assets"]["tetap"]["total_nilai_buku"]
    assert round(r["saldo"], 2) == round(exp, 2)


def test_persediaan_bahan_rows(admin_session):
    r = admin_session.get(f"{BASE}/balance-detail?kind=persediaan_bahan", timeout=15).json()
    # purchases -> in, production material_breakdown -> out
    dirs = {row["direction"] for row in r["rows"]}
    # not strict on presence (dataset may vary) but validate consistency
    assert dirs.issubset({"in", "out"})


def test_persediaan_barang_jadi_rows(admin_session):
    r = admin_session.get(f"{BASE}/balance-detail?kind=persediaan_barang_jadi", timeout=15).json()
    dirs = {row["direction"] for row in r["rows"]}
    assert dirs.issubset({"in", "out"})


def test_laba_ditahan_rows(admin_session):
    r = admin_session.get(f"{BASE}/balance-detail?kind=laba_ditahan", timeout=15).json()
    # Must have entries only if there's data; validate structure only
    for row in r["rows"]:
        assert row["direction"] in ("in", "out")


# ---------- Reports PSAK structure ----------
def test_reports_psak_new_keys(reports_payload):
    bd = reports_payload["balance"]["detail"]
    assert "lancar" in bd["assets"]
    assert "kas_setara_total" in bd["assets"]["lancar"]
    assert "persediaan_total" in bd["assets"]["lancar"]
    assert "tidak_lancar_total" in bd["assets"]
    assert "jangka_pendek" in bd["liabilities"]
    assert bd["liabilities"]["jangka_pendek"].get("total") == 0
    assert "jangka_panjang" in bd["liabilities"]
    jp = bd["liabilities"]["jangka_panjang"]
    assert "utang_pinjaman" in jp and "total" in jp


def test_reports_balance_seimbang(reports_payload):
    b = reports_payload["balance"]
    assert round(b["assets"], 2) == round(b["liabilities"] + b["equity"], 2)


# ---------- Regression: /api/reports/detail still works ----------
@pytest.mark.parametrize("kind", ["revenue", "cogs", "opex", "purchases", "production", "cash_out"])
def test_reports_detail_regression(admin_session, kind):
    r = admin_session.get(f"{BASE}/reports/detail?kind={kind}", timeout=15)
    assert r.status_code == 200, f"{kind}: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("kind") == kind
    assert isinstance(body.get("rows"), list)


# ---------- Regression: core endpoints still respond ----------
def test_ledger_regression(admin_session):
    r = admin_session.get(f"{BASE}/ledger", timeout=15)
    assert r.status_code == 200


def test_cash_movements_list(admin_session):
    r = admin_session.get(f"{BASE}/cash-movements", timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_assets_list(admin_session):
    r = admin_session.get(f"{BASE}/assets", timeout=15)
    assert r.status_code == 200


def test_auth_me(admin_session):
    r = admin_session.get(f"{BASE}/auth/me", timeout=10)
    assert r.status_code == 200
    assert r.json().get("email") == ADMIN["email"]
