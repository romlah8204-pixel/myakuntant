import os

import pytest
import requests


BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture
def client():
    session = requests.Session()
    login = session.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@liniar.id", "password": "Liniar123!"})
    if login.status_code != 200:
        pytest.skip(f"Demo login unavailable: {login.status_code}")
    return session


def test_sales_requires_authentication():
    response = requests.get(f"{BASE_URL}/api/sales")
    assert response.status_code == 401


def test_sales_list_has_no_mongodb_object_id(client):
    response = client.get(f"{BASE_URL}/api/sales")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert all("_id" not in sale for sale in response.json())


@pytest.mark.parametrize("channel", ["Offline", "Bazar", "Marketplace"])
def test_create_sale_calculates_totals_and_channel(client, channel):
    before = client.get(f"{BASE_URL}/api/inventory").json()
    item = next(x for x in before if x["sku"] == "LIN-OVR-001")
    quantity, unit_price = 1, 325000
    response = client.post(f"{BASE_URL}/api/sales", json={
        "channel": channel, "sku": item["sku"], "quantity": quantity,
        "unit_price": unit_price, "customer": "TEST_Sales", "order_ref": "TEST-FLOW",
    })
    assert response.status_code == 200
    sale = response.json()
    assert sale["channel"] == channel
    assert sale["revenue"] == quantity * unit_price
    assert sale["cogs"] == round(quantity * item["value"] / item["stock"], 2)
    assert sale["gross_profit"] == round(sale["revenue"] - sale["cogs"], 2)
    after = next(x for x in client.get(f"{BASE_URL}/api/inventory").json() if x["sku"] == item["sku"])
    assert after["stock"] == item["stock"] - quantity
    assert after["available"] == item["available"] - quantity


def test_sale_over_available_stock_returns_conflict(client):
    response = client.post(f"{BASE_URL}/api/sales", json={
        "channel": "Offline", "sku": "LIN-OVR-001", "quantity": 999999,
        "unit_price": 1, "customer": "TEST_Sales",
    })
    assert response.status_code == 409
    assert "Stok tersedia" in response.json()["detail"]