"""Iteration 10: Product photo upload/get/delete endpoints tests."""
import os
import io
import struct
import zlib
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@liniar.id", "password": "Liniar123!"}
STAFF = {"email": "staff@liniar.id", "password": "Staff123!"}

SEED_SKUS = ["LIN-OVR-001", "FAB-COT-042", "LIN-PNT-008"]


def _make_png(width=1, height=1):
    """Return valid 1x1 PNG bytes."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\xff\x00\x00" * width  # one scanline red
    idat = zlib.compress(raw * height)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _make_jpeg():
    # minimal JPEG-ish payload (not a real jpeg but content_type controls validation)
    return b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"


def _make_webp():
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 50


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def staff_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=STAFF, timeout=15)
    assert r.status_code == 200, f"Staff login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def uploaded_sku(admin_session):
    sku = "LIN-OVR-001"
    png = _make_png()
    r = admin_session.post(f"{API}/inventory/{sku}/photo",
                           files={"file": ("t.png", png, "image/png")}, timeout=60)
    assert r.status_code == 200, f"Setup upload failed: {r.status_code} {r.text}"
    return sku


# --- Upload tests ---
class TestUpload:
    def test_admin_png_upload_ok(self, admin_session):
        sku = "LIN-OVR-001"
        png = _make_png()
        r = admin_session.post(f"{API}/inventory/{sku}/photo",
                               files={"file": ("hero.png", png, "image/png")}, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sku"] == sku
        assert body["content_type"] == "image/png"
        assert body["size"] == len(png)
        assert body["photo_path"].endswith(".png")
        assert f"product-photos/{sku}/" in body["photo_path"]

    def test_jpeg_upload_ok_and_ext_jpg(self, admin_session):
        sku = "FAB-COT-042"
        r = admin_session.post(f"{API}/inventory/{sku}/photo",
                               files={"file": ("x.jpg", _make_jpeg(), "image/jpeg")}, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["content_type"] == "image/jpeg"
        assert r.json()["photo_path"].endswith(".jpg")

    def test_webp_upload_ok_and_ext_webp(self, admin_session):
        sku = "LIN-PNT-008"
        r = admin_session.post(f"{API}/inventory/{sku}/photo",
                               files={"file": ("x.webp", _make_webp(), "image/webp")}, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["content_type"] == "image/webp"
        assert r.json()["photo_path"].endswith(".webp")

    def test_sku_not_found(self, admin_session):
        r = admin_session.post(f"{API}/inventory/NOPE-XYZ/photo",
                               files={"file": ("t.png", _make_png(), "image/png")}, timeout=30)
        assert r.status_code == 404
        assert "SKU tidak ditemukan" in r.text

    def test_invalid_content_type(self, admin_session):
        r = admin_session.post(f"{API}/inventory/LIN-OVR-001/photo",
                               files={"file": ("t.txt", b"hello", "text/plain")}, timeout=30)
        assert r.status_code == 422
        assert "Format tidak didukung" in r.text

    def test_too_large(self, admin_session):
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024 + 100)
        r = admin_session.post(f"{API}/inventory/LIN-OVR-001/photo",
                               files={"file": ("big.png", big, "image/png")}, timeout=60)
        assert r.status_code == 413
        assert "3 MB" in r.text

    def test_upload_without_auth(self):
        s = requests.Session()
        r = s.post(f"{API}/inventory/LIN-OVR-001/photo",
                   files={"file": ("t.png", _make_png(), "image/png")}, timeout=30)
        assert r.status_code == 401

    def test_upload_by_staff_forbidden(self, staff_session):
        r = staff_session.post(f"{API}/inventory/LIN-OVR-001/photo",
                               files={"file": ("t.png", _make_png(), "image/png")}, timeout=30)
        assert r.status_code == 403

    def test_overwrite_changes_path_and_updated_at(self, admin_session):
        sku = "LIN-OVR-001"
        r1 = admin_session.post(f"{API}/inventory/{sku}/photo",
                                files={"file": ("a.png", _make_png(), "image/png")}, timeout=60)
        assert r1.status_code == 200
        path1 = r1.json()["photo_path"]
        # fetch inventory to get updated_at
        inv1 = admin_session.get(f"{API}/inventory", timeout=15).json()
        u1 = next(i for i in inv1 if i["sku"] == sku).get("photo_updated_at")

        import time; time.sleep(1.1)
        r2 = admin_session.post(f"{API}/inventory/{sku}/photo",
                                files={"file": ("b.png", _make_png(), "image/png")}, timeout=60)
        assert r2.status_code == 200
        path2 = r2.json()["photo_path"]
        inv2 = admin_session.get(f"{API}/inventory", timeout=15).json()
        u2 = next(i for i in inv2 if i["sku"] == sku).get("photo_updated_at")

        assert path1 != path2, "photo_path should differ due to new uuid"
        assert u1 != u2, "photo_updated_at should refresh"


# --- GET tests ---
class TestGetPhoto:
    def test_get_photo_returns_bytes_and_headers(self, admin_session, uploaded_sku):
        r = admin_session.get(f"{API}/inventory/{uploaded_sku}/photo", timeout=30)
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("image/png")
        assert "Cache-Control" in r.headers
        assert len(r.content) > 0

    def test_get_photo_no_photo(self, admin_session):
        # Ensure a SKU has no photo by deleting first
        sku = "FAB-COT-042"
        admin_session.delete(f"{API}/inventory/{sku}/photo", timeout=30)
        r = admin_session.get(f"{API}/inventory/{sku}/photo", timeout=30)
        assert r.status_code == 404
        assert "Foto belum tersedia" in r.text
        # Re-upload for other tests
        admin_session.post(f"{API}/inventory/{sku}/photo",
                           files={"file": ("x.jpg", _make_jpeg(), "image/jpeg")}, timeout=60)

    def test_get_photo_without_auth(self, uploaded_sku):
        s = requests.Session()
        r = s.get(f"{API}/inventory/{uploaded_sku}/photo", timeout=30)
        assert r.status_code == 401


# --- Inventory listing ---
class TestInventoryListing:
    def test_inventory_returns_photo_path_when_present(self, admin_session):
        # Upload fresh to guarantee state independent of parallel workers
        sku = "FAB-COT-042"
        up = admin_session.post(f"{API}/inventory/{sku}/photo",
                                files={"file": ("t.png", _make_png(), "image/png")}, timeout=60)
        assert up.status_code == 200
        rows = admin_session.get(f"{API}/inventory", timeout=15).json()
        item = next(i for i in rows if i["sku"] == sku)
        assert item.get("photo_path"), f"photo_path missing on {sku}: {item}"
        assert item.get("photo_content_type", "").startswith("image/")


# --- Delete tests ---
class TestDelete:
    def test_delete_by_staff_forbidden(self, staff_session):
        r = staff_session.delete(f"{API}/inventory/LIN-OVR-001/photo", timeout=30)
        assert r.status_code == 403

    def test_delete_no_photo(self, admin_session):
        sku = "LIN-PNT-008"
        # ensure removed
        admin_session.delete(f"{API}/inventory/{sku}/photo", timeout=30)
        r = admin_session.delete(f"{API}/inventory/{sku}/photo", timeout=30)
        assert r.status_code == 404
        assert "Foto tidak ditemukan" in r.text

    def test_delete_ok_then_get_404(self, admin_session):
        sku = "LIN-OVR-001"
        # ensure a photo exists
        admin_session.post(f"{API}/inventory/{sku}/photo",
                           files={"file": ("z.png", _make_png(), "image/png")}, timeout=60)
        d = admin_session.delete(f"{API}/inventory/{sku}/photo", timeout=30)
        assert d.status_code == 200
        body = d.json()
        assert body == {"sku": sku, "deleted": True}
        # inventory should no longer have photo_path
        rows = admin_session.get(f"{API}/inventory", timeout=15).json()
        item = next(i for i in rows if i["sku"] == sku)
        assert not item.get("photo_path")
        # get photo now 404
        g = admin_session.get(f"{API}/inventory/{sku}/photo", timeout=30)
        assert g.status_code == 404


# --- Activity log integration ---
class TestActivityLogs:
    def test_upload_logs_update_entry(self, admin_session):
        sku = "LIN-OVR-001"
        png = _make_png()
        r = admin_session.post(f"{API}/inventory/{sku}/photo",
                               files={"file": ("h.png", png, "image/png")}, timeout=60)
        assert r.status_code == 200
        logs = admin_session.get(f"{API}/activity-logs", params={"entity": "inventory", "action": "update", "limit": 20}, timeout=15).json()
        rows = logs["rows"]
        assert any(sku in row.get("summary", "") and "diunggah" in row.get("summary", "") and "KB" in row.get("summary", "") for row in rows), f"Missing upload log. Rows: {rows[:5]}"

    def test_delete_logs_delete_entry(self, admin_session):
        sku = "LIN-OVR-001"
        # ensure photo exists then delete
        admin_session.post(f"{API}/inventory/{sku}/photo",
                           files={"file": ("h.png", _make_png(), "image/png")}, timeout=60)
        d = admin_session.delete(f"{API}/inventory/{sku}/photo", timeout=30)
        assert d.status_code == 200
        logs = admin_session.get(f"{API}/activity-logs", params={"entity": "inventory", "action": "delete", "limit": 20}, timeout=15).json()
        rows = logs["rows"]
        assert any(sku in row.get("summary", "") and "dilepas" in row.get("summary", "") for row in rows), f"Missing delete log. Rows: {rows[:5]}"


# --- Regression: prior iterations quick smoke ---
class TestRegression:
    def test_auth_me_admin(self, admin_session):
        r = admin_session.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_purchases_list(self, admin_session):
        r = admin_session.get(f"{API}/purchases", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_sales_list(self, admin_session):
        r = admin_session.get(f"{API}/sales", timeout=15)
        assert r.status_code == 200

    def test_reports_all(self, admin_session):
        r = admin_session.get(f"{API}/reports", params={"granularity": "all", "channel": "Semua"}, timeout=15)
        assert r.status_code == 200
        assert "income" in r.json()

    def test_ready_to_sell(self, admin_session):
        r = admin_session.get(f"{API}/ready-to-sell", timeout=15)
        assert r.status_code == 200

    def test_sales_by_channel(self, admin_session):
        r = admin_session.get(f"{API}/sales-by-channel", timeout=15)
        assert r.status_code == 200
        assert "channels" in r.json()

    def test_opex_list(self, admin_session):
        r = admin_session.get(f"{API}/opex", timeout=15)
        assert r.status_code == 200

    def test_backups_list(self, admin_session):
        r = admin_session.get(f"{API}/backups", timeout=15)
        assert r.status_code == 200

    def test_activity_logs_list(self, admin_session):
        r = admin_session.get(f"{API}/activity-logs", params={"limit": 5}, timeout=15)
        assert r.status_code == 200
        assert "rows" in r.json()

    def test_activity_logs_csv_export(self, admin_session):
        r = admin_session.get(f"{API}/activity-logs/export.csv", timeout=30)
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("text/csv")
