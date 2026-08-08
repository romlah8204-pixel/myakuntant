import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fashion-mfg-hub.preview.emergentagent.com").rstrip("/")


def test_auth_dashboard_reports_and_transactions():
    session = requests.Session()
    denied = session.get(f"{BASE_URL}/api/auth/me")
    assert denied.status_code == 401

    login = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": "admin@liniar.id", "password": "Liniar123!"
    })
    assert login.status_code == 200
    assert "access_token" in session.cookies
    assert login.json()["email"] == "admin@liniar.id"
    assert "HttpOnly" in login.headers.get("set-cookie", "")

    me = session.get(f"{BASE_URL}/api/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "admin"
    dashboard = session.get(f"{BASE_URL}/api/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["metrics"]["revenue"] > 0 and len(body["sales"]) >= 6
    assert len(body["inventory"]) >= 1 and len(body["queue"]) >= 1

    reports = session.get(f"{BASE_URL}/api/reports")
    assert reports.status_code == 200
    income = reports.json()["income"]
    assert income["revenue"] - income["cogs"] == income["gross_profit"]
    assert income["gross_profit"] - income["operating_expense"] == income["net_profit"]

    purchase = session.post(f"{BASE_URL}/api/purchases", json={
        "supplier": "TEST_Supplier", "material": "TEST_Cotton",
        "quantity": 10, "unit": "meter", "unit_cost": 12500
    })
    assert purchase.status_code == 200 and purchase.json()["total"] == 125000

    production = session.post(f"{BASE_URL}/api/production", json={
        "sku": "TEST-SKU", "product": "TEST Product", "output_qty": 10,
        "material_cost": 100000, "labor_cost": 50000, "overhead_cost": 25000
    })
    assert production.status_code == 200 and production.json()["hpp"] == 17500

    logout = session.post(f"{BASE_URL}/api/auth/logout")
    assert logout.status_code == 200
    assert session.get(f"{BASE_URL}/api/auth/me").status_code == 401