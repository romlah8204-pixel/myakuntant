"""Iteration 20 tests: POS payment_method auto-linkage (cash_movement / piutang), 
balance sheet double-counting prevention, payment-methods-summary endpoint."""
import os
import pytest
import requests

def _load_env():
    try:
        with open('/app/frontend/.env') as f:
            for line in f:
                if line.startswith('REACT_APP_BACKEND_URL='):
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return os.environ.get('REACT_APP_BACKEND_URL', '')

BASE_URL = _load_env().rstrip('/')
assert BASE_URL, "REACT_APP_BACKEND_URL not configured"
API = f"{BASE_URL}/api"

ADMIN = ("admin@liniar.id", "Liniar123!")
KASIR = ("kasir@liniar.id", "Kasir123!")


def _login(email, pw):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_s():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def kasir_s():
    return _login(*KASIR)


def _pick_sku(sess):
    """Return sku of a Barang Jadi with the largest available stock (>=1)."""
    inv = sess.get(f"{API}/inventory", timeout=15).json()
    bj = [i for i in inv if i.get("type") == "Barang Jadi" and i.get("available", 0) >= 1]
    if not bj:
        pytest.skip("No barang jadi with available stock")
    bj.sort(key=lambda x: x.get("available", 0), reverse=True)
    it = bj[0]
    return it["sku"]


@pytest.fixture
def sku_barang_jadi(admin_s):
    sku = _pick_sku(admin_s)
    return sku, 0


# ---------- POS payment_method: cash_movement kas (tunai/qris) ----------
class TestPOSCashMovementKas:
    def test_pos_tunai_creates_kas_cash_movement(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        payload = {"channel": "POS", "sku": sku, "quantity": 1, "unit_price": 150000,
                   "customer": "TEST_pos_tunai", "payment_method": "tunai"}
        r = admin_s.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "linked_cash_movement_id" in data, "POS tunai must return linked_cash_movement_id"
        assert data.get("linked_receivable_id") is None or "linked_receivable_id" not in data
        assert data["status"] == "Lunas"
        cm_id = data["linked_cash_movement_id"]
        cms = admin_s.get(f"{API}/cash-movements", timeout=15).json()
        cm = next((m for m in cms if m.get("id") == cm_id), None)
        assert cm is not None, f"cash_movement {cm_id} not found"
        assert cm["account"] == "kas"
        assert cm["direction"] == "in"
        assert cm["category"] == "pos_penjualan"
        assert cm["amount"] == 150000
        assert cm.get("linked_sale_id") == data["id"]

    def test_pos_qris_creates_kas_cash_movement(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        payload = {"channel": "POS", "sku": sku, "quantity": 1, "unit_price": 200000,
                   "customer": "TEST_pos_qris", "payment_method": "qris"}
        r = admin_s.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "linked_cash_movement_id" in data
        cms = admin_s.get(f"{API}/cash-movements", timeout=15).json()
        cm = next((m for m in cms if m.get("id") == data["linked_cash_movement_id"]), None)
        assert cm is not None
        assert cm["account"] == "kas" and cm["category"] == "pos_penjualan" and cm["amount"] == 200000


# ---------- POS payment_method: cash_movement bank (kartu/transfer) ----------
class TestPOSCashMovementBank:
    def test_pos_kartu_creates_bank_cash_movement(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        payload = {"channel": "POS", "sku": sku, "quantity": 1, "unit_price": 175000,
                   "customer": "TEST_pos_kartu", "payment_method": "kartu"}
        r = admin_s.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "linked_cash_movement_id" in data
        cms = admin_s.get(f"{API}/cash-movements", timeout=15).json()
        cm = next((m for m in cms if m.get("id") == data["linked_cash_movement_id"]), None)
        assert cm is not None, "cash_movement not found"
        assert cm["account"] == "bank"
        assert cm["category"] == "pos_penjualan"
        assert cm["amount"] == 175000

    def test_pos_transfer_creates_bank_cash_movement(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        payload = {"channel": "POS", "sku": sku, "quantity": 1, "unit_price": 125000,
                   "customer": "TEST_pos_transfer", "payment_method": "transfer"}
        r = admin_s.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        cms = admin_s.get(f"{API}/cash-movements", timeout=15).json()
        cm = next((m for m in cms if m.get("id") == r.json()["linked_cash_movement_id"]), None)
        assert cm is not None and cm["account"] == "bank"


# ---------- POS payment_method: bayar_nanti → piutang ----------
class TestPOSBayarNanti:
    def test_pos_bayar_nanti_creates_piutang(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        payload = {"channel": "POS", "sku": sku, "quantity": 1, "unit_price": 300000,
                   "customer": "TEST_pos_debtor_ok", "payment_method": "bayar_nanti"}
        r = admin_s.post(f"{API}/sales", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "Piutang"
        assert "linked_receivable_id" in data
        assert "linked_cash_movement_id" not in data
        # Verify receivable exists
        r2 = admin_s.get(f"{API}/receivables-payables", timeout=15)
        assert r2.status_code == 200
        body = r2.json()
        rps = body.get("items", body) if isinstance(body, dict) else body
        rp = next((x for x in rps if x.get("id") == data["linked_receivable_id"]), None)
        assert rp is not None, "receivable not found"
        assert rp["type"] == "piutang_usaha"
        assert rp["amount"] == 300000
        assert rp["counterparty"] == "TEST_pos_debtor_ok"
        assert rp.get("linked_sale_id") == data["id"]

    def test_pos_bayar_nanti_walkin_rejected(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        for bad_cust in ["Walk-in", "walk-in", "", "  ", "Pelanggan Umum"]:
            r = admin_s.post(f"{API}/sales", json={"channel": "POS", "sku": sku, "quantity": 1,
                             "unit_price": 100000, "customer": bad_cust, "payment_method": "bayar_nanti"}, timeout=15)
            assert r.status_code == 422, f"Expected 422 for customer='{bad_cust}', got {r.status_code}"


# ---------- Validation ----------
class TestSaleValidation:
    def test_invalid_payment_method_rejected(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        r = admin_s.post(f"{API}/sales", json={"channel": "POS", "sku": sku, "quantity": 1,
                         "unit_price": 100000, "customer": "TEST_x", "payment_method": "krypto"}, timeout=15)
        assert r.status_code == 422

    def test_offline_channel_no_cash_movement(self, admin_s, sku_barang_jadi):
        """Offline channel with payment_method=tunai must succeed BUT not create cash_movement."""
        sku, _ = sku_barang_jadi
        # Count POS cash_movements before
        before = admin_s.get(f"{API}/cash-movements", timeout=15).json()
        before_pos = [m for m in before if m.get("category") == "pos_penjualan"]
        r = admin_s.post(f"{API}/sales", json={"channel": "Offline", "sku": sku, "quantity": 1,
                         "unit_price": 90000, "customer": "TEST_offline_cash",
                         "payment_method": "tunai"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "linked_cash_movement_id" not in data, "Non-POS channel must NOT create cash_movement"
        assert "linked_receivable_id" not in data
        after = admin_s.get(f"{API}/cash-movements", timeout=15).json()
        after_pos = [m for m in after if m.get("category") == "pos_penjualan"]
        assert len(after_pos) == len(before_pos), "Offline sale wrongly created cash_movement"


# ---------- Balance sheet double-counting prevention ----------
class TestBalanceSheetNoDoubleCount:
    def test_pos_qris_increases_kas_via_cash_movement_only(self, admin_s, sku_barang_jadi):
        """POS+QRIS Rp X should raise kas by X, driven by cash_movement, not op_cash_in."""
        sku, _ = sku_barang_jadi
        r0 = admin_s.get(f"{API}/reports", timeout=15)
        assert r0.status_code == 200, r0.text
        kas_before = r0.json()["balance"]["detail"]["assets"]["lancar"]["kas"]
        amt = 250000
        rs = admin_s.post(f"{API}/sales", json={"channel": "POS", "sku": sku, "quantity": 1,
                          "unit_price": amt, "customer": "TEST_no_double",
                          "payment_method": "qris"}, timeout=15)
        assert rs.status_code == 200, rs.text
        r1 = admin_s.get(f"{API}/reports", timeout=15).json()
        kas_after = r1["balance"]["detail"]["assets"]["lancar"]["kas"]
        # Kas should increase by exactly amt (revenue) - unit_cost portion (op_cash_out has NO change from sale)
        # Since sale doesn't affect op_cash_out (only purchases/production/opex do), kas should rise exactly by amt
        delta = kas_after - kas_before
        assert delta == amt, f"Expected kas to rise by exactly {amt}, got delta={delta}"

    def test_pos_bayar_nanti_increases_piutang_not_kas(self, admin_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        r0 = admin_s.get(f"{API}/reports", timeout=15).json()
        kas_before = r0["balance"]["detail"]["assets"]["lancar"]["kas"]
        piut_before = r0["balance"]["detail"]["assets"]["lancar"]["piutang_usaha"]
        amt = 275000
        rs = admin_s.post(f"{API}/sales", json={"channel": "POS", "sku": sku, "quantity": 1,
                          "unit_price": amt, "customer": "TEST_piut_no_kas",
                          "payment_method": "bayar_nanti"}, timeout=15)
        assert rs.status_code == 200
        r1 = admin_s.get(f"{API}/reports", timeout=15).json()
        kas_after = r1["balance"]["detail"]["assets"]["lancar"]["kas"]
        piut_after = r1["balance"]["detail"]["assets"]["lancar"]["piutang_usaha"]
        assert kas_after == kas_before, f"Bayar nanti should NOT change kas (before={kas_before} after={kas_after})"
        assert piut_after - piut_before == amt, f"Piutang should rise by {amt}, got {piut_after - piut_before}"


# ---------- Payment methods summary endpoint ----------
class TestPaymentMethodsSummary:
    def test_summary_default(self, admin_s):
        r = admin_s.get(f"{API}/payment-methods-summary?months=6", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(["months", "methods", "labels", "donut"]).issubset(data.keys())
        assert len(data["months"]) == 6
        assert set(data["methods"]) == {"tunai", "qris", "kartu", "transfer", "bayar_nanti", ""}
        for row in data["months"]:
            assert set(["label", "year", "month", "methods", "total"]).issubset(row.keys())
            for mm in data["methods"]:
                assert mm in row["methods"]
        assert "methods" in data["donut"]

    def test_summary_out_of_range(self, admin_s):
        r = admin_s.get(f"{API}/payment-methods-summary?months=25", timeout=15)
        assert r.status_code == 422

    def test_summary_requires_auth(self):
        r = requests.get(f"{API}/payment-methods-summary?months=6", timeout=15)
        assert r.status_code == 401


# ---------- Kasir role via POS bayar_nanti ----------
class TestKasirRolePOS:
    def test_kasir_can_create_pos_bayar_nanti(self, kasir_s, sku_barang_jadi):
        sku, _ = sku_barang_jadi
        r = kasir_s.post(f"{API}/sales", json={"channel": "POS", "sku": sku, "quantity": 1,
                         "unit_price": 130000, "customer": "TEST_kasir_debtor",
                         "payment_method": "bayar_nanti"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Piutang"


# ---------- Regression on previous endpoints ----------
class TestRegression:
    def test_receivables_payables(self, admin_s):
        r = admin_s.get(f"{API}/receivables-payables", timeout=15)
        assert r.status_code == 200

    def test_assets(self, admin_s):
        r = admin_s.get(f"{API}/assets", timeout=15)
        assert r.status_code == 200

    def test_cash_movements(self, admin_s):
        r = admin_s.get(f"{API}/cash-movements", timeout=15)
        assert r.status_code == 200

    def test_reports(self, admin_s):
        r = admin_s.get(f"{API}/reports", timeout=15)
        assert r.status_code == 200

    def test_balance_detail_kas(self, admin_s):
        r = admin_s.get(f"{API}/balance-detail?kind=kas", timeout=15)
        assert r.status_code == 200

    def test_balance_detail_kas_matches_reports_kas_exact(self, admin_s):
        """Invariant: reports.balance.detail.assets.lancar.kas == sum(balance-detail?kind=kas).
        Fix at server.py ~L417 excludes POS+payment_method sales from kas-kind loop to prevent double count."""
        rep = admin_s.get(f"{API}/reports", timeout=15).json()
        kas_reports = rep["balance"]["detail"]["assets"]["lancar"]["kas"]
        drill = admin_s.get(f"{API}/balance-detail?kind=kas", timeout=15).json()
        rows = drill if isinstance(drill, list) else drill.get("rows", [])
        kas_drill = sum((row.get("amount", 0) if row.get("direction") == "in" else -row.get("amount", 0)) for row in rows)
        assert kas_reports == kas_drill, f"kas mismatch: reports={kas_reports} drill={kas_drill} diff={kas_reports - kas_drill}"

    def test_balance_detail_bank(self, admin_s):
        r = admin_s.get(f"{API}/balance-detail?kind=bank", timeout=15)
        assert r.status_code == 200

    def test_ledger(self, admin_s):
        r = admin_s.get(f"{API}/ledger", timeout=15)
        assert r.status_code == 200

    def test_inventory(self, admin_s):
        r = admin_s.get(f"{API}/inventory", timeout=15)
        assert r.status_code == 200
