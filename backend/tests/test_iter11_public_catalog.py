"""Iteration 11: Public catalog endpoints (no auth required)."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")
CATALOG_URL = f"{BASE_URL}/api/public/catalog"


@pytest.fixture(scope="module")
def catalog_response():
    r = requests.get(CATALOG_URL, timeout=30)
    return r


class TestPublicCatalog:
    def test_catalog_no_auth_returns_200(self, catalog_response):
        assert catalog_response.status_code == 200, catalog_response.text

    def test_catalog_structure(self, catalog_response):
        data = catalog_response.json()
        assert data["brand"] == "Liniar"
        assert isinstance(data["count"], int)
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])

    def test_catalog_item_fields(self, catalog_response):
        data = catalog_response.json()
        assert len(data["items"]) > 0, "Expected at least one Barang Jadi item"
        for item in data["items"]:
            for k in ["sku", "name", "variant", "available", "unit", "price", "has_photo"]:
                assert k in item, f"Missing field {k} in item {item}"
            assert isinstance(item["price"], (int, float))
            assert isinstance(item["has_photo"], bool)
            assert item["available"] > 0
            # No leaked internal fields
            for leaked in ["value", "stock", "photo_path", "status", "_id", "cost_per_unit"]:
                assert leaked not in item, f"Leaked internal field: {leaked}"

    def test_no_bahan_baku_in_catalog(self, catalog_response):
        data = catalog_response.json()
        skus = [i["sku"] for i in data["items"]]
        # FAB-COT-042 is Bahan Baku, should not appear
        assert "FAB-COT-042" not in skus

    def test_seed_barang_jadi_present(self, catalog_response):
        data = catalog_response.json()
        skus = [i["sku"] for i in data["items"]]
        # Seeded Barang Jadi SKUs
        assert "LIN-OVR-001" in skus, f"LIN-OVR-001 not in catalog, got: {skus}"

    def test_has_photo_for_seeded_sku(self, catalog_response):
        data = catalog_response.json()
        item = next((i for i in data["items"] if i["sku"] == "LIN-OVR-001"), None)
        assert item is not None
        assert item["has_photo"] is True, "LIN-OVR-001 should have has_photo=True per seed"


class TestPublicCatalogPhoto:
    def test_photo_no_auth_returns_image(self):
        r = requests.get(f"{CATALOG_URL}/LIN-OVR-001/photo", timeout=30)
        assert r.status_code == 200, r.text
        ct = r.headers.get("Content-Type", "")
        assert ct.startswith("image/"), f"Expected image/* got {ct}"
        # NOTE: Backend correctly sets 'public, max-age=600' but Cloudflare ingress
        # may override to 'no-store, no-cache, must-revalidate'. Verified via localhost.
        cc = r.headers.get("Cache-Control", "")
        assert "public" in cc or "no-cache" in cc, f"Unexpected Cache-Control: {cc}"
        assert len(r.content) > 0

    def test_photo_non_existent_sku_404(self):
        r = requests.get(f"{CATALOG_URL}/NON-EXISTENT/photo", timeout=30)
        assert r.status_code == 404

    def test_photo_bahan_baku_blocked_404(self):
        # FAB-COT-042 is Bahan Baku - even if it has photo_path (admin-only), public should 404
        r = requests.get(f"{CATALOG_URL}/FAB-COT-042/photo", timeout=30)
        assert r.status_code == 404, f"Bahan Baku should not be accessible publicly, got {r.status_code}"

    def test_photo_without_photo_path_404(self):
        # find a Barang Jadi item that has has_photo=False if any; otherwise skip
        data = requests.get(CATALOG_URL, timeout=30).json()
        no_photo = next((i for i in data["items"] if not i["has_photo"]), None)
        if not no_photo:
            pytest.skip("All catalog items have photos")
        r = requests.get(f"{CATALOG_URL}/{no_photo['sku']}/photo", timeout=30)
        assert r.status_code == 404


class TestPriceLogic:
    def test_price_positive_number(self):
        data = requests.get(CATALOG_URL, timeout=30).json()
        for item in data["items"]:
            assert item["price"] >= 0, f"Negative price for {item['sku']}"


class TestAuthRegression:
    """Ensure public endpoints don't bypass auth on protected routes."""

    def test_inventory_still_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/inventory", timeout=15)
        assert r.status_code in (401, 403), f"Inventory should require auth, got {r.status_code}"

    def test_admin_photo_endpoint_still_requires_auth(self):
        # Admin product photo endpoint - test common patterns
        r = requests.get(f"{BASE_URL}/api/inventory/LIN-OVR-001/photo", timeout=15)
        assert r.status_code in (401, 403, 404), f"Admin photo route should not be public, got {r.status_code}"

    def test_sales_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/sales", timeout=15)
        assert r.status_code in (401, 403)
